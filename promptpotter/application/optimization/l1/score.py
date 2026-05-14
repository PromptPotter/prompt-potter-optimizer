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
from dataclasses import dataclass, field
from enum import StrEnum
from functools import partial
from typing import TYPE_CHECKING

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
        return SignalEffect(False, False, False, False, "", None, None, None, None)

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
            "total_queries": int(cr.get("total_queries", len(dataset))),
            "n_priors": int(cr.get("n_priors", 0)),
            "leader_locked": leader_locked_loose,
        }

    queries_scored = int(cr.get("queries_scored", len(results)))
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
            },
            pobb_decision_data(cr),
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
            },
            pobb_decision_data(cr),
        )

    return SignalEffect(
        aborted=aborted,
        elimination_stopped=elimination_stopped,
        leader_locked=leader_locked,
        leader_locked_loose=leader_locked_loose,
        leader_id=leader_id,
        runtime_failure=new_rf,
        elim_context=elim_ctx,
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

    results, scores, was_cached, signal = await score_search_point(
        osp_c.to_job_search_point(
            base_pipeline_params=merged_pp, schema=cycle.session.pipeline_schema
        ),
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
    )

    # Path 2 — full-run cache replay.
    if was_cached:
        elim_check.register_completed(
            [r.get("fitness", 0.0) for r in results], candidate_id=osp_c.lineage.id
        )
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
        elim_check.register_completed(
            [r.get("fitness", 0.0) for r in results], candidate_id=osp_c.lineage.id
        )

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
        new_runtime_failure=effect.runtime_failure,
        l1_diversity=l1_diversity,
    )

    # Decorate elim_ctx with prior label when the leader was a prior candidate.
    if (
        effect.leader_id
        and effect.leader_id in priors_at_test
        and report.elimination_context is not None
    ):
        prior_label = next(
            (r.label for r in candidate_scores if r.candidate_id == effect.leader_id),
            None,
        )
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
    elim_check = PoBBCheck(pobb_config, n_samples=len(dataset))

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

    aborted_ids = {
        cs.candidate_id
        for cs in candidate_scores
        if cs.escalation_aborted and not cs.elimination_stopped
    }
    scored = [
        ind
        for ind in osp_population
        if ind.lineage.id in all_candidate_results and ind.lineage.id not in aborted_ids
    ]
    best_acc = origin.accuracy
    best_comp = origin.composite_fitness
    best_osp: OptSearchPoint = origin.osp
    best_results: list = list(origin.results)
    best_label = origin.label
    best_scores: dict[str, float] = dict(origin.evaluators)
    winner_idx: int | None = None
    for idx, ind in enumerate(scored):
        s = compute_composite_fitness(
            all_candidate_results[ind.lineage.id],
            schema,
            opt_sp=ind,
            round_scorer=session.scoring.round_scorer,
            l1_diversity=yield_stats.l1_yield,
        )
        if s["accuracy"] > best_acc:
            best_acc = s["accuracy"]
            best_comp = s["composite_fitness"]
            best_osp = ind
            best_results = list(all_candidate_results[ind.lineage.id])
            best_label = ind.lineage.changes_description or ind.lineage.id[:12]
            best_scores = dict(s.get("evaluators") or {})
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
        origin_hits = round(origin.accuracy * base["total"])
        p_value = proportion_test(base["hits"], base["total"], origin_hits, base["total"])

    delta_ok = best_acc > origin.accuracy + improvement_threshold
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
