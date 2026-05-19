"""L1 scoring — per-candidate three-path lifecycle + per-population loop + winner selection.

* ``score_one_candidate`` runs one candidate through validation-skip /
  cache-replay / scored paths.
* ``score_population`` is the outer loop dispatching to
  ``score_one_candidate`` and handling LEADER_LOCKED / ESCALATED breaks.
* ``l1_score`` picks the round winner (comparing fitness, not
  composite_fitness) and produces the round's :class:`RoundResult`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from functools import partial
from typing import TYPE_CHECKING, cast

from promptpotter.application.intelligence.adaptive_picker import (
    chernoff_info_pair,
    expected_order_mfi,
    expected_order_track_and_stop,
    fisher_info,
    next_sample_mfi,
    next_sample_track_and_stop,
    posterior_from_outcomes,
)
from promptpotter.application.intelligence.exploration import Observation, fit_rasch
from promptpotter.application.optimization.l1.population import (
    INVALID_SCORES,
    build_score_report,
    parse_population,
    pobb_decision_data,
)
from promptpotter.application.optimization.pobb.elimination import PoBBCheck, PoBBConfig
from promptpotter.application.optimization.resume_and_fork import (
    ResumeCheckpointKind,
    ResumeCheckpointRecord,
    record_decision,
)
from promptpotter.application.optimization.validators.l1_strict import L1YieldStats
from promptpotter.application.scoring.metrics import (
    _compute_accuracy,
    compute_composite_fitness,
    count_degraded_samples,
    matched_origin_stats,
)
from promptpotter.application.scoring.search_point_scorer import score_search_point
from promptpotter.domain.escalation_signals import EscalationSignal, RuntimeFailure
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.results import (
    CandidateProposal,
    CandidateScore,
    RoundOrigin,
    RoundResult,
    candidate_label,
)
from promptpotter.domain.scoring import QueryMeasurement
from promptpotter.domain.validators import StopRule
from promptpotter.infrastructure.tracing import CandidateScored
from promptpotter.shared.errors import graceful
from promptpotter.shared.statistics import proportion_test

if TYPE_CHECKING:
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.application.run_observers import RunCallbacks
    from promptpotter.domain.search_point import JobSearchPoint

logger = logging.getLogger(__name__)


class CandidateOutcome(StrEnum):
    """How ``score_one_candidate`` exited. Caller fires the report unconditionally
    and uses the tag to decide whether to break the loop or continue.

    SCORED is the default exit. SKIPPED_VALIDATION / REPLAYED_FROM_CACHE are
    early returns from paths 1 and 2; both still produce a report. LEADER_LOCKED
    and ESCALATED are scored-path tags that signal the caller to break."""

    SKIPPED_VALIDATION = "skipped_validation"
    REPLAYED_FROM_CACHE = "replayed_from_cache"
    SCORED = "scored"
    LEADER_LOCKED = "leader_locked"
    ESCALATED = "escalated"


def round_winner_key(composite_fitness: float | None, accuracy: float) -> tuple[float, float]:
    """Tiebreak key for round-winner selection: composite-first, accuracy-tiebreak.

    Shared by ``l1_score`` (round-time selection over scored candidates) and
    ``pick_round_winner`` (post-hoc selection over ``ScoreEntry``s for the
    SCOREBOARD ``*`` marker). Centralised so the two surfaces cannot drift
    apart — a single PR that changes this rule moves both readers together.

    ``composite_fitness`` falls back to ``accuracy`` when ``None`` so the key
    is well-defined for ``CandidateScore`` rows minted before backfill (path-1
    validation-skip variants carry ``composite_fitness=0.0``, not ``None``,
    but the fallback keeps the helper total over the type signature).
    """
    return (composite_fitness if composite_fitness is not None else accuracy, accuracy)


@dataclass(frozen=True)
class CandidateRunResult:
    """One candidate's full lifecycle output. ``runtime_failure`` is the
    caller's signal to append to ``osp_c.wounds.runtime_failures``; the function
    cannot mutate it directly because the OSP is shared with other paths."""

    outcome: CandidateOutcome
    results: list[QueryMeasurement] = field(default_factory=list)
    report: CandidateScore = None  # type: ignore[assignment]
    runtime_failure: RuntimeFailure | None = None
    escalation_signal: EscalationSignal | None = None


@dataclass(frozen=True)
class SignalEffect:
    """One pure decode of an ``EscalationSignal`` over a candidate's eval state.

    Folds four overlapping reads of ``signal.check_result`` (RuntimeFailure
    construction, elimination context, ELIMINATION_CUT decision payload,
    LEADER_LOCK_IN decision payload) into a single pass. The caller still
    owns leader-label decoration (needs prior-rank lookup over already-scored
    candidates) and decision emission gating. Decision payloads are kept as
    ``(inputs_ref, data)`` tuples — the ``ResumeCheckpointKind`` literal stays at
    the ``record_decision`` callsite (static check in
    ``test_no_bare_string_decision_kinds``).
    """

    aborted: bool
    elimination_stopped: bool
    leader_locked: bool
    leader_locked_loose: bool
    leader_id: str
    runtime_failure: RuntimeFailure | None
    elim_context: dict | None
    degradation_context: dict | None
    elimination_decision: tuple[dict, dict] | None
    leader_lock_decision: tuple[dict, dict] | None


def decode_signal_effect(
    signal: EscalationSignal | None,
    *,
    results: list,
    dataset: list,
    merged_pp: dict | None,
    round_num: int,
    elim_check: PoBBCheck,
    candidate_id: str,
    candidate_label: str,
    priors_at_test: list[str],
) -> SignalEffect:
    """Decode all per-candidate signal effects in one pass over ``check_result``."""
    if signal is None:
        return SignalEffect(False, False, False, False, "", None, None, None, None, None)

    elimination_stopped = signal.is_elimination
    leader_locked_loose = signal.is_leader_lock
    scoring_error_abort = signal.check_name == "scoring_error_abort"
    leader_locked = signal.is_leader_lock and signal.check_name == elim_check.name
    aborted = not leader_locked_loose and (scoring_error_abort or len(results) < len(dataset))

    cr = signal.check_result

    new_rf: RuntimeFailure | None = None
    if elimination_stopped and signal.check_name == "degradation":
        rf_kind: str | None = "degradation_check"
        dominant = cr.get("dominant_warning", "unknown:unknown")
        node_cfg = (merged_pp or {}).get(dominant.split(":", 1)[0], {})
        rate = float(cr.get("degraded_rate", 0.0))
    elif scoring_error_abort:
        rf_kind = "scoring_error_abort"
        dominant = str(cr.get("dominant_warning") or "scoring_error")
        node_cfg = merged_pp or {}
        dc_tmp = int(cr.get("degraded_count", 0))
        te_tmp = int(cr.get("total_scored", len(results)))
        rate = (dc_tmp / te_tmp) if te_tmp else 0.0
    else:
        rf_kind = None
    if rf_kind is not None:
        new_rf = RuntimeFailure(
            source=rf_kind,
            dominant_warning=dominant,
            warning_types=dict(cr.get("warning_types") or {}),
            degraded_rate=rate,
            degraded_count=int(cr.get("degraded_count", 0)),
            total_scored=int(cr.get("total_scored", len(results))),
            observed_config=dict(node_cfg),
            first_seen_round=round_num,
            candidate_label=candidate_label,
        )

    elim_ctx: dict | None = None
    leader_id = ""
    if (elimination_stopped or leader_locked_loose) and signal.check_name == "elimination":
        leader_id = str(cr.get("leader_id", ""))
        elim_ctx = {
            "p_best": float(cr.get("p_best", 0.0)),
            "epsilon": float(cr.get("epsilon", 0.05)),
            "leader_id": leader_id,
            "queries_scored": int(cr.get("queries_scored", len(results))),
            "total_queries": int(cr.get("total_samples", len(dataset))),
            "n_priors": int(cr.get("n_priors", 0)),
            "leader_locked": leader_locked_loose,
        }

    # Degradation context — populated when DegradationCheck (or scoring-
    # error abort) fires. Disjoint from elim_ctx: the renderer reads one
    # or the other based on which check name attached to the signal.
    degrad_ctx: dict | None = None
    if elimination_stopped and signal.check_name in ("degradation", "scoring_error_abort"):
        degrad_ctx = {
            "degraded_rate": float(cr.get("degraded_rate", 0.0)),
            "degraded_count": int(cr.get("degraded_count", 0)),
            "total_scored": int(cr.get("total_scored", len(results))),
            "dominant_warning": str(cr.get("dominant_warning", "unknown")),
            "fatal": bool(cr.get("fatal", False)),
            "warning_types": dict(cr.get("warning_types") or {}),
            "source": signal.check_name,
        }

    queries_scored = int(cr.get("queries_scored", len(results)))
    recorded_p_best = float(cr.get("p_best", 0.0))
    # Paired-PoBB decision snapshot — the sample IDs in play at decision
    # time + each prior's fitness on exactly those samples. Replay reads
    # these directly (no cross-round crawl, no backfill replay).
    candidate_sample_ids = [
        str(r.get("sample_id", ""))
        for r in results[:queries_scored]
        if r.get("sample_id") is not None
    ]
    prior_histories_snapshot = elim_check.snapshot_priors(candidate_sample_ids)
    elimination_decision: tuple[dict, dict] | None = None
    if elimination_stopped and signal.check_name == elim_check.name:
        elimination_decision = (
            {
                "candidate_id": candidate_id,
                "prior_candidate_ids": priors_at_test,
                "queries_scored": queries_scored,
                "epsilon": float(elim_check.epsilon),
                "n_min": int(elim_check.n_min),
                "round_num": round_num,
                "recorded_p_best": recorded_p_best,
            },
            pobb_decision_data(
                cr,
                candidate_sample_ids=candidate_sample_ids,
                prior_histories=prior_histories_snapshot,
            ),
        )
    leader_lock_decision: tuple[dict, dict] | None = None
    if leader_locked:
        leader_lock_decision = (
            {
                "candidate_id": candidate_id,
                "prior_candidate_ids": priors_at_test,
                "queries_scored": queries_scored,
                "lock_in": float(elim_check.lock_in),
                "lock_in_n_min": int(elim_check.lock_in_n_min),
                "round_num": round_num,
                "recorded_p_best": recorded_p_best,
            },
            pobb_decision_data(
                cr,
                candidate_sample_ids=candidate_sample_ids,
                prior_histories=prior_histories_snapshot,
            ),
        )

    return SignalEffect(
        aborted=aborted,
        elimination_stopped=elimination_stopped,
        leader_locked=leader_locked,
        leader_locked_loose=leader_locked_loose,
        leader_id=leader_id,
        runtime_failure=new_rf,
        elim_context=elim_ctx,
        degradation_context=degrad_ctx,
        elimination_decision=elimination_decision,
        leader_lock_decision=leader_lock_decision,
    )


async def score_one_candidate(
    *,
    idx: int,
    osp_c: OptSearchPoint,
    pipeline_params_override: dict | None,
    cycle: Cycle,
    dataset: list,
    n_total: int,
    merged_pp: dict | None,
    elim_check: PoBBCheck,
    callbacks: RunCallbacks,
    degradation_checks: list[StopRule] | None,
    decisions: list[ResumeCheckpointRecord] | None,
    candidate_scores: list[CandidateScore],
    round_num: int,
    l1_diversity: float,
    next_sample: Callable[[dict[int, bool]], int | None] | None = None,
) -> CandidateRunResult:
    """Run one candidate through the three-exit-path lifecycle.

    Path 1 — validation-skip: synthetic-0 score (no eval).
    Path 2 — cache-replay: full-run cache hit (no backend calls).
    Path 3 — scored: full eval; classifies signal into SCORED / LEADER_LOCKED /
    ESCALATED, builds RuntimeFailure on degradation, records ELIMINATION_CUT
    / LEADER_LOCK_IN decisions on ``decisions``.

    ``candidate_scores`` is read for prior_label resolution (already-scored
    candidates only — caller appends the current report after this returns)."""
    label = candidate_label(round_num, idx)

    # Path 1 — validation-skip synthetic-0.
    if osp_c.wounds.validation_failures:
        return CandidateRunResult(
            outcome=CandidateOutcome.SKIPPED_VALIDATION,
            results=[],
            report=build_score_report(
                osp_c,
                pipeline_params_override,
                INVALID_SCORES,
                [],
                dataset,
                label=label,
                invalid=True,
                l1_diversity=l1_diversity,
            ),
        )

    candidate_sp = osp_c.to_job_search_point(
        base_pipeline_params=merged_pp, schema=cycle.session.pipeline_schema
    )
    # Backfill paired priors on the candidate's full sample budget BEFORE
    # eval starts. This is what restores PoBB's iid premise: every prior
    # in the pool gets measured on the same samples this candidate is
    # about to see (cache-first via per-sample archive; fresh LLM call
    # on miss). See docs/concepts/paired-sample-pobb.md.
    samples_by_id = {str(s.id): s for s in dataset}
    backfilled = await elim_check.backfill_priors([s.id for s in dataset], samples_by_id)
    callbacks.on_pobb_backfill(round_num, idx, n_total, backfilled)

    results, scores, was_cached, signal = await score_search_point(
        candidate_sp,
        dataset,
        cycle.session,
        label=f"candidate_{idx}",
        on_sample_scored=partial(callbacks.on_sample_scored, idx, n_total),
        on_sample_starting=partial(callbacks.on_sample_started, idx, n_total),
        degradation_checks=[*(degradation_checks or []), elim_check],
        candidate_idx=idx,
        n_total_candidates=n_total,
        axes=cycle.axes,
        l1_diversity=l1_diversity,
        next_sample=next_sample,
    )

    # Path 2 — full-run cache replay.
    if was_cached:
        elim_check.register_completed(results, candidate_id=osp_c.lineage.id, sp=candidate_sp)
        return CandidateRunResult(
            outcome=CandidateOutcome.REPLAYED_FROM_CACHE,
            results=results,
            report=build_score_report(
                osp_c,
                pipeline_params_override,
                scores,
                results,
                dataset,
                label=label,
                resumed_from_cache=True,
                l1_diversity=l1_diversity,
            ),
        )

    # Path 3 — scored. Snapshot priors BEFORE eval registers this candidate.
    priors_at_test = list(elim_check.prior_ids)
    effect = decode_signal_effect(
        signal,
        results=results,
        dataset=dataset,
        merged_pp=merged_pp,
        round_num=round_num,
        elim_check=elim_check,
        candidate_id=osp_c.lineage.id,
        candidate_label=osp_c.lineage.changes_description or "",
        priors_at_test=priors_at_test,
    )
    # Aborted candidates must NOT seed priors — their scores are synthetic 0s.
    if len(results) == len(dataset) and not effect.aborted:
        elim_check.register_completed(results, candidate_id=osp_c.lineage.id, sp=candidate_sp)

    report = build_score_report(
        osp_c,
        pipeline_params_override,
        scores,
        results,
        dataset,
        label=label,
        aborted=effect.aborted,
        elimination_stopped=effect.elimination_stopped,
        elimination_context=dict(effect.elim_context) if effect.elim_context else None,
        degradation_context=(
            dict(effect.degradation_context) if effect.degradation_context else None
        ),
        new_runtime_failure=effect.runtime_failure,
        l1_diversity=l1_diversity,
    )

    # Decorate elim_ctx with prior label when the leader was a prior
    # candidate. Seeded priors (``"origin"`` / ``"R{N}_winner"``) carry
    # operator-readable ids; current-round priors resolve through the
    # accumulated ``candidate_scores`` (labels like ``C2.3``).
    if (
        effect.leader_id
        and effect.leader_id in priors_at_test
        and report.elimination_context is not None
    ):
        prior_label = next(
            (r.label for r in candidate_scores if r.candidate_id == effect.leader_id),
            None,
        )
        if not prior_label and (
            effect.leader_id == "origin"
            or (effect.leader_id.startswith("R") and effect.leader_id.endswith("_winner"))
        ):
            prior_label = effect.leader_id
        if prior_label:
            report.elimination_context["leader_label"] = prior_label

    if decisions is not None and effect.elimination_decision is not None:
        inputs_ref, data = effect.elimination_decision
        record_decision(
            decisions,
            ResumeCheckpointKind.ELIMINATION_CUT,
            inputs_ref,
            True,
            data=data,
            round=round_num,
        )
    if decisions is not None and effect.leader_lock_decision is not None:
        inputs_ref, data = effect.leader_lock_decision
        record_decision(
            decisions,
            ResumeCheckpointKind.LEADER_LOCK_IN,
            inputs_ref,
            True,
            data=data,
            round=round_num,
        )

    residual = (
        None if (effect.elimination_stopped or effect.leader_locked_loose or not signal) else signal
    )
    if effect.leader_locked:
        outcome = CandidateOutcome.LEADER_LOCKED
    elif residual is not None:
        outcome = CandidateOutcome.ESCALATED
    else:
        outcome = CandidateOutcome.SCORED
    return CandidateRunResult(
        outcome=outcome,
        results=results,
        report=report,
        runtime_failure=effect.runtime_failure,
        escalation_signal=residual if outcome == CandidateOutcome.ESCALATED else None,
    )


async def score_population(
    cycle: Cycle,
    population: list[OptSearchPoint],
    merged_pp: list[dict | None],
    proposals: list[CandidateProposal],
    dataset: list,
    *,
    degradation_checks: list[StopRule] | None = None,
    callbacks: RunCallbacks,
    pobb_config: PoBBConfig,
    round_num: int = 0,
    decisions: list[ResumeCheckpointRecord] | None = None,
    l1_diversity: float = 1.0,
) -> tuple[dict[str, list[QueryMeasurement]], list[CandidateScore], EscalationSignal | None]:
    """Score each individual; dispatch over three exit paths (validation/cache/scored).

    Per-candidate body lives in ``score_one_candidate``; this function owns
    the loop, the shared accumulators (``candidate_scores``, ``decisions``,
    ``all_candidate_results``), and the break conditions on
    LEADER_LOCKED / ESCALATED outcomes."""
    session = cycle.session
    obs = session.state.obs
    n = len(population)

    all_candidate_results: dict[str, list[QueryMeasurement]] = {}
    candidate_scores: list[CandidateScore] = []
    escalation_signal: EscalationSignal | None = None

    async def _pobb_backfill(sp: JobSearchPoint, samples: list) -> list[QueryMeasurement]:
        """Score *sp* on *samples* and return the measurements.

        Used by PoBBCheck to fill in missing (prior, sample) pairs so paired
        comparison can run on identical sample sets. ``degradation_checks=None``
        so the backfill cannot recursively trip PoBB on its own measurements.

        Per-sample callbacks ride the same callback objects the main loop
        uses, with ``candidate_idx=-1`` as the "this is a backfill row"
        sentinel so the display layer can prefix the row distinctively and
        skip the main-loop counter. Without this the operator sees only the
        opaque "deprecated retry: resuming" stderr lines while the backfill
        burns LLM credits in silence.
        """
        bf_results, _bf_scores, _was_cached, _signal = await score_search_point(
            sp,
            samples,
            cycle.session,
            label="pobb_backfill",
            degradation_checks=None,
            candidate_idx=-1,
            n_total_candidates=0,
            axes=cycle.axes,
            l1_diversity=0.0,
            on_sample_scored=partial(callbacks.on_sample_scored, -1, 0),
            on_sample_starting=partial(callbacks.on_sample_started, -1, 0),
        )
        return bf_results

    elim_check = PoBBCheck(
        pobb_config,
        n_samples=len(dataset),
        round_num=round_num,
        backfill_fn=_pobb_backfill,
    )

    # Seed PoBB priors so candidate #1 of every round has a comparator
    # to lose against. Without this, ``PoBBCheck.check()`` short-circuits
    # on empty priors and the mechanism never fires until candidate #2
    # — round 1 candidate #1 was un-eliminable. ``current_results`` is
    # the round's "best-so-far" per-sample fitness history (campaign
    # origin in round 1, prior round winner in round 2+). The seed SP
    # is ``tracking.current_sp`` so the leader is backfill-able on the
    # candidate's hard samples when needed.
    seed_results = cycle.tracking.current_results
    seed_sp = cycle.tracking.current_sp
    if seed_results and seed_sp is not None:
        seed_id = f"R{cycle.rounds[-1].round}_winner" if cycle.rounds else "origin"
        elim_check.register_completed(seed_results, candidate_id=seed_id, sp=seed_sp)

    # Hoisted picker plumbing — shared across the per-candidate loop.
    #
    # Fix B — undermeasured filter: candidates with fewer than
    # n_samples // 2 measurements (i.e. ones the prior PoBB iteration
    # aborted early) contribute observations only on the picker's
    # discriminating samples. Including them biases the Rasch fit on
    # those samples — the seed prior (registered separately) is full,
    # so dropping early-abort residue from ``sorter_obs`` is safe.
    pop_min_obs = max(1, len(dataset) // 2)

    def _filtered_obs(
        results_by_cid: dict[str, list[QueryMeasurement]] | dict[str, list[dict]],
    ) -> list[Observation]:
        out: list[Observation] = []
        for cid, results in results_by_cid.items():
            valid = [r for r in results if r.get("sample_id") is not None and not r.get("error")]
            if len(valid) < pop_min_obs:
                continue
            out.extend(
                Observation(
                    candidate_id=cid,
                    sample_id=int(r["sample_id"]),
                    hit=bool(r.get("hit")),
                )
                for r in valid
            )
        return out

    prior_round_obs: list[Observation] = []
    for rr in cycle.rounds:
        prior_round_obs.extend(_filtered_obs(rr.all_candidate_results))
    base_sorter_obs: list[Observation] = list(prior_round_obs)
    if cycle.config.optimization.exploration.seed_evolve_from_archive:
        # Archive observations come from completed scored runs (no
        # mid-run abort residue), so no length filter needed there.
        base_sorter_obs = list(cycle.archive_observations) + base_sorter_obs

    # Prior on candidate θ_c: broad N(0, 1) centred at the Rasch
    # identifiability anchor (mean(θ) = 0). Observations dominate
    # within ~3 measurements; the prior just keeps the cold-start
    # posterior proper when the picker fires before any sample has
    # been measured on this candidate.
    PRIOR_MU = 0.0
    PRIOR_VAR = 1.0

    picker_objective = cycle.config.optimization.picker_objective
    dataset_sample_ids = [int(s.id) for s in dataset]

    def _delta_map_from_obs() -> dict[int, float]:
        """Fit Rasch on current observations; return sample-id → δ_s.

        Called once per candidate (before its query loop opens) so the
        picker's δ-map reflects every measurement seen so far across
        priors and in-progress in-round candidates. Empty observations
        → empty map → picker falls back to sample-id ascending (cold
        start), matching the pre-existing R0 candidate-1 behaviour.
        """
        obs = base_sorter_obs + _filtered_obs(all_candidate_results)
        if not obs:
            return {}
        posterior = fit_rasch(obs)
        return {int(sid): float(d) for sid, d in posterior.delta.items()}

    def _seed_mu_from_results() -> float:
        """Fold seed's per-sample outcomes into a θ_s posterior mean.

        Used by the Phase B (Track-and-Stop) objective so the picker can
        contrast each sample's predicted hit-rate between candidate and
        seed. Returns ``PRIOR_MU`` when the seed has no measurements
        (cold start) or when the picker objective is ``mfi`` (the value
        is unused). Computed once per round at the entry to
        ``score_population`` — the seed doesn't change between
        candidates within a round.
        """
        if not seed_results:
            return PRIOR_MU
        delta_for_seed = _delta_map_from_obs()
        if not delta_for_seed:
            return PRIOR_MU
        outcomes = [
            (delta_for_seed.get(int(sr["sample_id"]), 0.0), bool(sr.get("hit")))
            for sr in seed_results
            if sr.get("sample_id") is not None and not sr.get("error")
        ]
        mu, _ = posterior_from_outcomes(PRIOR_MU, PRIOR_VAR, outcomes)
        return mu

    seed_mu = _seed_mu_from_results() if picker_objective == "track_and_stop" else PRIOR_MU

    for idx, osp_c in enumerate(population):
        pipeline_params_override = proposals[idx].pipeline_params_override or None
        callbacks.on_candidate_started(
            idx, n, osp_c.lineage.changes_description or "", pipeline_params_override
        )
        # Bind the PoBBCheck to this candidate so its per-sample snapshot
        # lands on the live telemetry stream tagged with the right id.
        elim_check.set_current(
            osp_c.lineage.id,
            on_snapshot=partial(callbacks.on_p_best_update, round_num, idx, n),
        )

        # Online 1PL Rasch CAT picker. Refit δ on cumulative observations
        # at candidate start; each per-sample call to ``next_sample``
        # folds ``update_theta_posterior`` over the candidate's measured
        # outcomes so far and picks argmax over the configured objective
        # under the running θ̂_c posterior. Pure / stateless — callers
        # don't persist (μ_c, σ²_c) across calls.
        #
        # Objective ``mfi``: argmax Fisher info p(1-p) at μ_c — Phase A,
        # textbook CAT, decision-agnostic.
        # Objective ``track_and_stop``: argmax Chernoff(p_c, p_s) where
        # p_s is the seed's predicted hit-rate on the same sample —
        # Phase B, decision-aligned (Garivier-Kaufmann 2016).
        delta_map = _delta_map_from_obs()
        objective = picker_objective

        def _next_sample(
            scored_outcomes: dict[int, bool],
            _delta_map: dict[int, float] = delta_map,
            _objective: str = objective,
            _seed_mu: float = seed_mu,
        ) -> int | None:
            mu_c, _var_c = posterior_from_outcomes(
                PRIOR_MU,
                PRIOR_VAR,
                ((_delta_map.get(sid, 0.0), hit) for sid, hit in scored_outcomes.items()),
            )
            remaining = set(dataset_sample_ids) - scored_outcomes.keys()
            if _objective == "track_and_stop":
                return next_sample_track_and_stop(mu_c, _seed_mu, _delta_map, remaining)
            return next_sample_mfi(mu_c, _delta_map, remaining)

        # Candidate-start preview: rank all dataset sample_ids under the
        # active objective with the candidate's prior θ̂_c = PRIOR_MU.
        # Surfaces on ``dashboard.json::hard_sample_order`` so the webapp
        # table mirrors what the picker would pick if outcomes follow
        # the prior. Live execution re-picks per step.
        if picker_objective == "track_and_stop":
            expected_order = expected_order_track_and_stop(
                PRIOR_MU, seed_mu, delta_map, dataset_sample_ids
            )
            preview_scores = {
                sid: float(chernoff_info_pair(PRIOR_MU, seed_mu, delta_map.get(sid, 0.0)))
                for sid in expected_order[: min(3, len(expected_order))]
            }
        else:
            expected_order = expected_order_mfi(PRIOR_MU, delta_map, dataset_sample_ids)
            preview_scores = {
                sid: float(fisher_info(PRIOR_MU, delta_map.get(sid, 0.0)))
                for sid in expected_order[: min(3, len(expected_order))]
            }

        if delta_map:
            preview = [(sid, preview_scores[sid]) for sid in preview_scores]
            callbacks.on_sample_order_preview(
                round_num,
                idx,
                n,
                preview,
                n_priors=len(elim_check.priors_by_sample),
                sample_order=expected_order,
            )
        # Bind the candidate's sample budget so PoBB's dominance gate
        # can compute ``cand_max_hits = cand_hits + remaining`` against
        # the seed prior's total hits. Online picking means the candidate
        # consumes the full dataset; the universe is unordered.
        elim_check.set_sample_universe(dataset_sample_ids)

        cr_result = await score_one_candidate(
            idx=idx,
            osp_c=osp_c,
            pipeline_params_override=pipeline_params_override,
            cycle=cycle,
            dataset=dataset,
            n_total=n,
            merged_pp=merged_pp[idx],
            elim_check=elim_check,
            callbacks=callbacks,
            degradation_checks=degradation_checks,
            decisions=decisions,
            candidate_scores=candidate_scores,
            round_num=round_num,
            l1_diversity=l1_diversity,
            next_sample=_next_sample,
        )
        all_candidate_results[osp_c.lineage.id] = cr_result.results
        if cr_result.runtime_failure is not None:
            osp_c.wounds.runtime_failures = [
                *osp_c.wounds.runtime_failures,
                cr_result.runtime_failure,
            ]
        candidate_scores.append(cr_result.report)
        callbacks.on_candidate_scored(idx, n, cr_result.report.to_dict())
        if obs:
            with graceful("CandidateScored emit failed"):
                obs.emit_write_point(
                    CandidateScored,
                    campaign_id=session.state.tracing_campaign_id,
                    round_num=round_num,
                    candidate_idx=idx,
                    report=cr_result.report.to_dict(),
                )

        if cr_result.outcome == CandidateOutcome.LEADER_LOCKED:
            break  # round leader confirmed — skip remaining candidates
        if cr_result.outcome == CandidateOutcome.ESCALATED:
            escalation_signal = cr_result.escalation_signal
            break  # true degradation — abort remaining candidates

    return all_candidate_results, candidate_scores, escalation_signal


def is_leader_eligible(cs: CandidateScore) -> bool:
    """Whether *cs* may be elected the round leader.

    Two disjoint disqualifications:

    (a) Escalation-aborted without a PoBB stop — a true mid-run failure
        outside the measured-comparison surface.
    (b) Any candidate whose run halted on a degradation or
        ``scoring_error_abort`` check (``degradation_context`` is
        populated only by those two check names). Their accuracy is
        computed over a partial valid subset and can inflate above
        origin on a 6/20 run — paired PoBB doesn't help because the
        candidate never produced comparable measurements.

    PoBB-elimination is NOT a disqualification — those candidates went
    through fair paired comparison on real samples and stay in the pool.
    """
    if cs.escalation_aborted and not cs.elimination_stopped:
        return False
    return not cs.degradation_context


async def l1_score(
    cycle: Cycle,
    candidates: list[CandidateProposal],
    dataset: list,
    origin: RoundOrigin,
    *,
    pipeline_params: dict | None = None,
    improvement_threshold: float = 0.01,
    improvement_significance: float = 0.10,
    callbacks: RunCallbacks,
    degradation_checks: list[StopRule] | None = None,
    pobb_config: PoBBConfig,
    round_num: int = 0,
    yield_stats: L1YieldStats,
) -> tuple[RoundResult, OptSearchPoint]:
    """Score candidates and select the round winner (compares fitness, not composite_fitness).

    Returns ``(round_result, winner_osp)``. ``RoundResult`` is the
    persistence shape (dict-typed lists for serialization); ``winner_osp``
    rides alongside for the ``PromptVersion`` tracing emit in the caller.
    """
    session = cycle.session
    schema = session.pipeline_schema
    assert schema is not None, "l1_score requires pipeline_schema"

    osp_population, merged_pp = parse_population(
        candidates,
        pipeline_params,
        schema,
        forbidden_axes_strict=cycle.config.optimization.forbidden_axes_strict,
    )
    decisions: list[ResumeCheckpointRecord] = []
    all_candidate_results, candidate_scores, escalation_signal = await score_population(
        cycle,
        osp_population,
        merged_pp,
        candidates,
        dataset,
        degradation_checks=degradation_checks,
        callbacks=callbacks,
        pobb_config=pobb_config,
        round_num=round_num,
        decisions=decisions,
        l1_diversity=yield_stats.l1_yield,
    )

    aborted_ids = {cs.candidate_id for cs in candidate_scores if not is_leader_eligible(cs)}
    scored = [
        ind
        for ind in osp_population
        if ind.lineage.id in all_candidate_results and ind.lineage.id not in aborted_ids
    ]
    # Origin's full-set stats — the no-candidate-wins fallback for the
    # ``matched_origin`` quad below (every candidate ran every sample ⇒ matched
    # collapses to full).
    origin_base = _compute_accuracy(cast("list[QueryMeasurement]", origin.results))
    best_acc = origin.accuracy
    best_comp = origin.composite_fitness
    best_osp: OptSearchPoint = origin.osp
    best_results: list = list(origin.results)
    best_label = origin.label
    best_scores: dict[str, float] = dict(origin.evaluators)
    best_matched_origin_acc = origin.accuracy
    best_matched_origin_hits = origin_base["hits"]
    best_matched_origin_composite = origin.composite_fitness
    best_key = round_winner_key(origin.composite_fitness, origin.accuracy)
    winner_idx: int | None = None
    # ``score_population`` populated each ``CandidateScore`` with the
    # ``opt_sp=None`` composite (per-sample scoring path is target-layer; it
    # doesn't see ``OptSearchPoint``). That makes opt_sp-aware evaluators
    # (currently ``prompt_compactness``) collapse to their vacuous fallback,
    # inflating the per-candidate composite shown inline during scoring
    # relative to the post-backfill value that lands on the SCOREBOARD.
    # Index by candidate id so we can back-fill the opt_sp-aware composite +
    # evaluators in one pass before selecting the winner.
    cs_by_id = {cs.candidate_id: i for i, cs in enumerate(candidate_scores)}
    for idx, ind in enumerate(scored):
        cand_results = all_candidate_results[ind.lineage.id]
        s = compute_composite_fitness(
            cand_results,
            schema,
            opt_sp=ind,
            round_scorer=session.scoring.round_scorer,
            l1_diversity=yield_stats.l1_yield,
        )
        # Matched-pair origin: PoBB-locked candidates may have only run the
        # hardest q8/20; comparing their 8-sample accuracy to origin's full-set
        # 20-sample accuracy systematically punishes the early-stop optimization.
        # Restricting origin to the candidate's measured samples is the same
        # comparison PoBB's posterior used to declare the lock.
        matched = matched_origin_stats(
            cast("list[QueryMeasurement]", origin.results),
            cand_results,
            schema,
            round_scorer=session.scoring.round_scorer,
        )
        cs_idx = cs_by_id.get(ind.lineage.id)
        if cs_idx is not None:
            evaluators = {**(s.get("evaluators") or {}), "l1_diversity": yield_stats.l1_yield}
            candidate_scores[cs_idx] = replace(
                candidate_scores[cs_idx],
                composite_fitness=s["composite_fitness"],
                evaluators=evaluators,
                matched_origin_accuracy=matched["accuracy"],
                matched_origin_hits=matched["hits"],
                matched_origin_composite=matched["composite_fitness"],
            )
        # Winner = running max by (composite_fitness, accuracy). Same key as
        # ``round_winner_key`` (used by the SCOREBOARD's ``pick_round_winner``)
        # so the two surfaces report the same winner. The matched-origin gate
        # is applied after selection via ``delta_ok`` below — it decides
        # whether the round is ``improved``, not who won.
        cand_key = round_winner_key(s["composite_fitness"], s["accuracy"])
        if cand_key > best_key:
            best_key = cand_key
            best_acc = s["accuracy"]
            best_comp = s["composite_fitness"]
            best_osp = ind
            best_results = list(cand_results)
            best_label = ind.lineage.changes_description or ind.lineage.id[:12]
            best_scores = dict(s.get("evaluators") or {})
            best_matched_origin_acc = matched["accuracy"]
            best_matched_origin_hits = matched["hits"]
            best_matched_origin_composite = matched["composite_fitness"]
            winner_idx = idx

    winner_id = scored[winner_idx].lineage.id if winner_idx is not None and scored else ""
    record_decision(
        decisions,
        ResumeCheckpointKind.ROUND_WINNER,
        {
            "candidate_ids": [ind.lineage.id for ind in scored],
            "round_num": round_num,
        },
        winner_id,
        data={"current_best_accuracy_at_record": origin.accuracy},
        round=round_num,
    )

    base = _compute_accuracy(best_results)
    p_value: float | None = None
    if base["total"] > 0:
        # Real matched-pair hits from the same sample subset the candidate ran.
        # The prior ``round(origin.accuracy * base["total"])`` faked this by
        # extrapolating origin's full-set rate onto the candidate's subset —
        # exactly wrong for hard-first sampling where origin underperforms
        # its full-set rate on the early samples.
        p_value = proportion_test(
            base["hits"], base["total"], best_matched_origin_hits, base["total"]
        )

    delta_ok = best_acc > best_matched_origin_acc + improvement_threshold
    n_min = pobb_config.n_min
    n_ok = base["total"] >= n_min
    sig_ok = improvement_significance >= 1.0 or (
        p_value is not None and p_value < improvement_significance
    )
    improved = delta_ok and n_ok and sig_ok
    improved_reason: str | None = None
    if delta_ok and not improved:
        reasons: list[str] = []
        if not n_ok:
            reasons.append(f"n={base['total']} < elimination_n_min={n_min}")
        if not sig_ok:
            p_repr = f"{p_value:.3f}" if p_value is not None else "None"
            reasons.append(f"p={p_repr} >= {improvement_significance:.2f}")
        improved_reason = "; ".join(reasons)
    round_result = RoundResult(
        round=round_num,
        label=best_label,
        accuracy=best_acc,
        composite_fitness=best_comp,
        hits=base["hits"],
        total=base["total"],
        improved=improved,
        p_value=p_value,
        improved_reason=improved_reason,
        origin_accuracy=origin.accuracy,
        matched_origin_accuracy=best_matched_origin_acc,
        matched_origin_hits=best_matched_origin_hits,
        matched_origin_composite=best_matched_origin_composite,
        prompt_fields={
            **best_osp.prompt_field_dict(),
            "lineage": best_osp.lineage.model_dump(),
        },
        pipeline_params=merged_pp[winner_idx] if winner_idx is not None else pipeline_params,
        results=best_results,
        all_candidate_results=dict(all_candidate_results),
        candidates_scored=len(scored),
        candidate_scores=[cs.to_dict() for cs in candidate_scores],
        decisions=[d.to_dict() for d in decisions],
        degraded_samples=count_degraded_samples(best_results),
        deprecated=base["deprecated"],
        escalation_signal=escalation_signal,
        evaluators=best_scores,
        l1_yield=yield_stats.l1_yield,
        l1_n_no_op=yield_stats.l1_n_no_op,
        l1_n_duplicate=yield_stats.l1_n_duplicate,
    )
    return round_result, best_osp


__all__ = [
    "CandidateOutcome",
    "CandidateRunResult",
    "SignalEffect",
    "decode_signal_effect",
    "l1_score",
    "round_winner_key",
    "score_one_candidate",
    "score_population",
]
