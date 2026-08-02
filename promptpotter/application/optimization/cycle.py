"""Cycle state — round-loop mutable orchestration object.

**Wrong-level guardrail:** new *optimizer* state flows through ``OptSearchPoint``
(``domain/opt_search_point.py``), never a sidecar field here. `Cycle` holds
round-loop orchestration — the escalation FSM, the axis index, this round's
bookkeeping — and is rebuilt per cycle. State a layer must carry ACROSS
generations (anything L1/L2/L3 reads or writes about the individual) belongs on
the searchpoint, which is what lineage, hashing, and resume all key on. A field
added here instead is invisible to `derive()` and silently lost on fork.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

# Leaf import (not the package surface): EscalationFSM is the foundational
# state type Cycle holds; importing it via escalation/__init__ would load the
# firing driver, which depends back on Cycle → import cycle. See escalation/__init__.
from promptpotter.application.optimization.escalation.state import EscalationFSM
from promptpotter.application.scoring.metrics import _compute_accuracy
from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.domain.escalation_signals import rf_dedup_key
from promptpotter.domain.opt_search_point import OptSearchPoint, node_config_items
from promptpotter.domain.results import (
    RoundParent,
    RoundResult,
    ScoredCandidate,
)
from promptpotter.domain.run_records import RebaseRequest, ResumeCheckpointRecord
from promptpotter.domain.search_point import JobSearchPoint, TaskDecomposition

if TYPE_CHECKING:
    from promptpotter.application.bootstrap.session import Session
    from promptpotter.application.config import CampaignConfig
    from promptpotter.application.intelligence.exploration import Observation, RulerEntry
    from promptpotter.application.intelligence.indexes.axis import AxisIndex
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.results import CalibrationModel
    from promptpotter.domain.scoring import QueryMeasurement
    from promptpotter.domain.search_point import TaskDecomposition

logger = logging.getLogger(__name__)

__all__ = ["Cycle", "CycleRoundState"]


def _merge_known_outcomes(
    prior: list[dict[str, Any]], incoming: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Sample-keyed merge: incoming overwrites prior; entries without ``sample_id`` dropped.

    **This pool is not a score, and must never be scored.** Its rows are measured by
    DIFFERENT configurations — round N's winner on the samples it ran, whatever ran them
    last everywhere else — so an accuracy over it belongs to no individual. It exists to
    decide what to measure NEXT (PoBB seeding, resume's election floor, replay parity),
    which is a question the pool's mixed provenance does not spoil.

    Scoring it is what published ``57%→78%`` on a cycle whose best candidate ever measured
    0.679: the round-7 number was 12 rows from round 6's config glued to 28 from round 7's,
    and because the round subset is the CONTESTED one, the carried rows are the easy tail
    the previous config scored 12/12 on. Every configuration inherited its predecessor's
    perfect easy-tail score, so a mutation that regressed there was invisible."""
    by_sid: dict[Any, dict[str, Any]] = {
        r.get("sample_id"): r for r in prior if r.get("sample_id") is not None
    }
    for r in incoming:
        sid = r.get("sample_id")
        if sid is not None:
            by_sid[sid] = r
    return list(by_sid.values())


def _origin_round(
    opt_sp: OptSearchPoint,
    sp: JobSearchPoint,
    *,
    report: ScoredCandidate,
    results: list[dict[str, Any]],
    theta: tuple[float, float] | None,
    calibration_model: CalibrationModel | None,
) -> RoundResult:
    """Round 0 as an ordinary ``RoundResult`` — one candidate, C0, no rivals.

    C0's row IS the report the scoring gateway produced, with the two facts only a round
    close can add stamped on: its ability θ on the cycle's δ ruler, and — because it faced
    nobody — a matched origin that is itself. Nothing is re-derived here; a second
    computation of an already-measured number is how the row and the ledger came to hold
    different answers about the same candidate.

    The origin changed nothing, so it describes no change — winner identity is
    ``candidate_id``, which both this row and the round stamp from the same lineage.
    """
    prompt_fields = {**opt_sp.prompt_field_dict(), "lineage": opt_sp.lineage.model_dump()}
    deprecated = _compute_accuracy(cast("list[QueryMeasurement]", results))["deprecated"]
    row = report.model_copy(
        update={
            "theta": theta[0] if theta is not None else None,
            "theta_se": theta[1] if theta is not None else None,
            "prompt_fields": prompt_fields,
            "resolved_pipeline_params": sp.config_params,
            "matched_origin_accuracy": report.accuracy,
            "matched_origin_composite": report.composite_fitness,
        }
    )
    return RoundResult(
        round=0,
        label=row.label,
        accuracy=row.accuracy,
        composite_fitness=row.composite_fitness,
        total=row.total,
        improved=False,
        origin_accuracy=row.accuracy,
        matched_origin_accuracy=row.accuracy,
        matched_origin_composite=row.composite_fitness,
        prompt_fields=prompt_fields,
        pipeline_params=sp.pipeline_params,
        results=results,
        all_candidate_results={opt_sp.lineage.id: results},
        candidates_scored=1,
        candidate_scores=[row],
        deprecated=deprecated,
        # Round 0's frontier θ is the origin's θ — the subset-invariant peer of the round's
        # own `accuracy`, so the trend line starts on the θ scale too, and the one place θ
        # is read from.
        cumulative_theta=theta[0] if theta is not None else None,
        cumulative_theta_se=theta[1] if theta is not None else None,
        calibration_model=calibration_model,
        evaluators=dict(row.evaluators),
        opt_sp=opt_sp,
    )


def _build_initial_opt_sp(
    resolved_origin: OptSearchPoint, task_context: TaskDecomposition
) -> OptSearchPoint:
    """Seed the optimizer state from a scored origin: nest task_context + a copied
    l1_overrides dict under ``memory`` so L2/L3 mutations don't share references."""
    return resolved_origin.model_copy(
        update={
            "memory": resolved_origin.memory.model_copy(
                update={
                    "task_context": task_context,
                    "l1_overrides": dict(resolved_origin.memory.l1_overrides),
                }
            ),
        }
    )


def _assert_overlay_preserved(
    sp: JobSearchPoint, session_pipeline_params: dict[str, Any] | None
) -> None:
    """Dataset-overlay keys must survive into ``sp.pipeline_params`` so ``content_hash``
    flips on overlay edits and the wire adapter forwards the overlay. ``Cycle.start``
    must pass ``session.pipeline_params`` (the merged overlay), not a sparse schema view."""
    sp_pp = sp.pipeline_params or {}
    for node, cfg in node_config_items(session_pipeline_params):
        missing = set(cfg) - set(sp_pp.get(node, {}))
        assert not missing, (
            f"overlay keys stripped from {node}: {sorted(missing)} — "
            "Cycle.start must pass session.pipeline_params, not a sparse schema view"
        )


def _calibrate_delta_ruler(
    session: Session,
    origin_results: list[dict[str, Any]] | None,
    n_min: int,
    *,
    enable_2pl: bool,
    archive_obs: list[Observation],
) -> tuple[dict[int, RulerEntry], tuple[float, float] | None, CalibrationModel | None]:
    """Calibrate the per-cycle FIXED difficulty ruler + read the origin's ``(θ, θ_se)`` on it.

    The cross-round comparability anchor (slice 2 of fitness-comparability). Every
    later θ readout in this cycle (round winners for ``c0_ok``, the stall ladder) is
    measured against this one fixed ruler via ``fit_theta_given_delta`` — so they compare
    on a shared scale instead of each round's own re-anchored fit. Calibrated at cycle
    start (and, on a cold start, re-attempted per round until it warms + LOCKS —
    ``Cycle._maybe_warm_ruler``) from the **grade-A** archive (the operator's pick: cleanest reference, same
    de-biasing the AxisIndex digest applies) plus the origin's own per-sample outcomes
    (so the origin's samples are always on the ruler). The origin rides under
    ``ORIGIN_ABILITY_ID``, which is how the fit sees it as one arm; **θ_C0 is then read back
    off the finished ruler by the same conditional estimator every other level uses**, never
    off the joint fit (see the comment at the return). Cold start —
    fewer than ``n_min`` calibrated samples (a fresh dataset's first cycle) — returns a
    **FLAT ruler** ``{}`` (δ≡0) and the origin's θ on it (logit-accuracy), so the gates
    still compare in θ (one ruler, θ always) rather than a separate accuracy floor.
    ``n_min`` is ``optimization.elimination_n_min`` — the single min-samples floor that
    also gates when PoBB acts on a candidate's θ; difficulty and ability become trustworthy
    at the same evidence threshold (no separate ruler-only constant).

    The model is chosen by ``graduate_ruler_model`` (slice 3): the bank uses 1PL until a
    data-rich, genuinely-discriminating dataset wins held-out cross-validation, then the
    ruler carries per-sample discrimination ``(δ, a)``. Returned as the third element so
    the operator reads the model actually fitted; a cold ruler is **neither** 1PL nor 2PL
    (it is flat), so it returns ``None`` rather than the name the graduation would have
    picked had the fit been adopted. Gated by ``enable_2pl``
    (``optimization.enable_2pl_graduation``). The switch is invisible above the
    seam — ``ruler()`` folds δ + a into the one mapping every θ consumer already reads.
    """
    from promptpotter.application.intelligence.exploration import (
        ORIGIN_ABILITY_ID,
        Observation,
        dedup_observations,
        fit_theta_given_delta,
        graded_response,
        graduate_ruler_model,
    )
    from promptpotter.shared.errors import is_error_result

    origin_obs = [
        Observation(ORIGIN_ABILITY_ID, int(sid), graded_response(r))
        for r in origin_results or []
        if (sid := r.get("sample_id")) is not None and not is_error_result(r)
    ]
    # ``archive_obs`` already carries the origin under ``ORIGIN_ABILITY_ID`` (the caller passes
    # its sp_hash) whenever the archive has banked it. ``origin_obs`` is still the guaranteed,
    # freshest source — an L4 inner cycle that cache-replays its origin banked that run INSIDE
    # its own evidence epoch, so the archive read cannot see it — and it wins the cells both hold.
    obs = dedup_observations(archive_obs, origin_obs)
    if not obs:
        return {}, None, None
    # Two warmth conditions, both knowable without fitting — below either the ruler stays flat
    # and the fit would be discarded unread, so skip it entirely rather than pay a
    # 1PL + 2PL + 5-fold-CV storm per round to throw the result away.
    #
    # DISTINCT SAMPLES ≥ n_min: both fits key δ on ``sorted({o.sample_id})``.
    #
    # DISTINCT ARMS ≥ 2: δ is only identified against a second ability. One arm and the
    # likelihood has nothing to separate — the anchor pins θ, and δ becomes that arm's own
    # hit pattern shrunk to two values, one for what it passed and one for what it failed.
    # Adopting that as THE ruler makes every later θ in the cycle a restatement of whether
    # round 0 happened to get the sample right, on a scale where the origin sits at exactly
    # 0.000 by construction. It is what a fresh campaign always hands this function: 40 origin
    # rows from one candidate clears any sample floor on its own, so the L4 inner cycles were
    # locking a two-valued ruler at ``Cycle.start`` and then judging eight rounds of promotion
    # against it (`justlogic-d234`, 2026-07-27 — two rounds carrying +14.3pp at p<0.05 were
    # stamped `improved=False` on it, and both campaigns died of the lives that cost them).
    # One arm therefore stays FLAT and re-attempts next round, by which time the round's own
    # candidates are banked grade-A and the fit has arms to compare.
    ruler: dict[int, RulerEntry] = {}
    model: CalibrationModel | None = None
    if len({o.sample_id for o in obs}) >= n_min and len({o.candidate_id for o in obs}) >= 2:
        fitted, post = graduate_ruler_model(obs, enable=enable_2pl)
        # Cold below the floor: too few banked samples to trust a fitted ruler → stay flat.
        if len(post.delta) >= n_min:
            ruler, model = post.ruler(), fitted
            if model == "2PL":
                logger.info("δ ruler graduated to 2PL (%d samples fit)", len(post.delta))
    # θ_C0 THROUGH THE SAME ESTIMATOR EVERY OTHER LEVEL USES — the whole point of this line.
    # The warm branch used to hand back ``post.theta[ORIGIN_ABILITY_ID]``, the JOINT fit's
    # ability, while every round level is a ``fit_theta_given_delta`` MAP at the locked ruler
    # (``_cumulative_theta``). ``fit_theta_given_delta``'s own docstring disqualifies the joint
    # θ for exactly this: ``fit_rasch`` re-anchors ``mean(θ)==0`` and re-estimates σ_θ by EB on
    # every call, so its scale is set by whichever arms happened to be in the pool. The L4 law
    # then differenced two abilities from two estimators — one shrunk toward a pool mean with a
    # data-dependent σ_θ, the other toward 0 with a fixed one — and the anchor's bias moved with
    # the arm measuring it. Measured on the 50 banked cells: the ruler's shift is ~87% common-mode
    # and does cancel in the difference (r=+0.75 within seed), so this is worth ~2% of the delta's
    # variance — but the residual is a BIAS channel, not noise, and bias does not average out over
    # a panel. It also makes the cold and warm branches agree: one expression, one estimator.
    # ``obs``, not ``origin_obs``: the deduped set carries every origin row the archive holds too.
    origin_obs_all = [o for o in obs if o.candidate_id == ORIGIN_ABILITY_ID]
    return ruler, fit_theta_given_delta(origin_obs_all, ruler).get(ORIGIN_ABILITY_ID), model


_FRONTIER_ABILITY_ID = "_frontier"


def _cumulative_theta(
    results: list[dict[str, Any]], delta_scale: dict[int, RulerEntry] | None
) -> tuple[float, float] | None:
    """Ability ``(θ, θ_se)`` of the cumulative frontier ``results`` on the fixed ``delta_scale``.

    The θ-space peer of the cumulative composite — what the stall ladder compares
    round-over-round. One virtual candidate (the frontier) fit against the fixed δ (flat
    where the ruler is cold), so successive rounds land on one scale even once per-round
    subsets drift. ``None`` only when no non-error result remains to fit.

    The SE comes back with it because the fit already computed it (dispersion-corrected) and
    dropping it left every downstream consumer — the stall ladder, the L4 panel — to re-derive
    precision from the spread of point estimates it did not have enough of."""
    from promptpotter.application.intelligence.exploration import (
        Observation,
        fit_theta_given_delta,
        graded_response,
    )
    from promptpotter.shared.errors import is_error_result

    obs = [
        Observation(_FRONTIER_ABILITY_ID, int(sid), graded_response(r))
        for r in results
        if (sid := r.get("sample_id")) is not None and not is_error_result(r)
    ]
    return fit_theta_given_delta(obs, delta_scale or {}).get(_FRONTIER_ABILITY_ID)


def _inherit_sibling_runtime_failures(opt_sp: OptSearchPoint, session: Session) -> None:
    """Pull RuntimeFailures from sibling forks of this cycle's root so L1 sees configs
    prior siblings already proved to fail (``_r_runtime_failures`` filters by pipeline match)."""
    from promptpotter.application.intelligence.sibling_wounds import (
        gather_sibling_runtime_failures,
    )
    from promptpotter.infrastructure.store.layout import root_cycle_id as _root_cycle_id

    if not session.state.cycle_id:
        return
    try:
        root_id = _root_cycle_id(session.state.cycle_id)
        failures = gather_sibling_runtime_failures(
            session.store,
            session.campaign_id,
            root_id,
            session.backend_id,
            exclude_cycle_id=session.state.cycle_id,
        )
    except Exception:
        # Surface, don't swallow: on failure L1 never sees configs sibling forks
        # already proved to fail — a real optimization-quality regression, not noise.
        logger.warning("sibling runtime_failures inheritance skipped", exc_info=True)
        return
    if failures:
        opt_sp.memory.wounds.runtime_failures.extend(failures)
        logger.info(
            "inherited %d runtime_failures from sibling forks of %s",
            len(failures),
            session.state.cycle_id,
        )


@dataclass
class CycleRoundState:
    """Current/best searchpoint trajectory. The origin's own scalars are NOT here —
    they are round 0's, read off ``Cycle.origin_round``."""

    current_sp: JobSearchPoint | None = None
    current_accuracy: float = 0.0
    current_composite_fitness: float = 0.0
    current_results: list[dict[str, Any]] = field(default_factory=list)
    best_accuracy: float = 0.0
    best_composite_fitness: float = 0.0
    best_round: int = 0
    best_sp: JobSearchPoint | None = None
    # Running-max ability θ of the cumulative frontier on the fixed ruler — the
    # θ-space peer of ``best_composite_fitness``, the comparator the L2/L3 stall
    # ladder reads (slice 2). Seeded from ``origin_theta``, re-maxed each round
    # in ``absorb_round``. None for a cold-started cycle → the ladder falls back
    # to ``best_composite_fitness``. Like c0_ok, a near-no-op while the scoring
    # set is fixed (per_round_resubset OFF) — θ and accuracy then move together.
    best_theta: float | None = None


@dataclass
class Cycle:
    session: Session
    config: CampaignConfig

    # The whole trajectory, 0-indexed: ``rounds[0]`` IS the origin's measurement.
    # ``start`` builds it before the loop opens, so it is never absent.
    rounds: list[RoundResult] = field(default_factory=list)
    tracking: CycleRoundState = field(default_factory=CycleRoundState)
    opt_sp: OptSearchPoint = field(default_factory=OptSearchPoint)
    axes: AxisIndex | None = None
    # Earned prompt-block library for this cycle's task shape — `{field: (block, ...)}` of short
    # reusable field values that earned credible lift on a run with the SAME answer-space
    # signature. Mined once at `start` (the walk is cross-campaign); the `guidance` block
    # catalogue renders from it, silent when empty. Never the static seed set.
    earned_blocks: dict[str, tuple[str, ...]] = field(default_factory=dict)
    escalation: EscalationFSM = field(default_factory=EscalationFSM)
    pending_decisions: list[ResumeCheckpointRecord] = field(default_factory=list)
    archive_observations: list[Observation] = field(default_factory=list)
    # The origin's searchpoint identity (``prompt_fields_id`` = ``sp_hash``), captured at
    # ``start`` because ``opt_sp`` advances on ``adopt``. The δ fit renames the archive
    # candidate carrying it to ``ORIGIN_ABILITY_ID``, so the origin is ONE candidate with one θ
    # instead of one per round subset it was ever re-scored against.
    origin_sp_hash: str = ""
    # The cycle's δ ruler (sample_id → difficulty), from the grade-A archive + origin
    # (slice 2). Calibrated at start; a cold start (empty) re-warms from round data and
    # LOCKS on the first warm fit (``_maybe_warm_ruler``), then held constant — so every
    # cross-round θ readout (``c0_ok``, the stall ladder) lands on one scale via
    # ``fit_theta_given_delta``. Empty {} = still cold → gates degenerate to θ==logit-accuracy.
    delta_scale: dict[int, RulerEntry] | None = None
    # Which model the ruler above was fitted under, for the operator's readout. None while
    # the ruler is cold — a flat ruler is neither 1PL nor 2PL, so naming one would lie.
    calibration_model: CalibrationModel | None = None
    # Stashed by L2/L3 rebase emission; runner.entry resolves it post-finalize
    # into _mint_fork + observer rebuild + loop re-entry on the new fork.
    rebase_request: RebaseRequest | None = None

    @classmethod
    def start(
        cls,
        resolved_origin: OptSearchPoint,
        origin_report: ScoredCandidate,
        *,
        task_context: TaskDecomposition,
        schema: PipelineSchema,
        origin_results: list[dict[str, Any]] | None = None,
        session: Session,
        config: CampaignConfig,
    ) -> Cycle:
        """Construct a fresh Cycle from a scored origin.

        The origin arrives ALREADY measured — ``origin_report`` is what the scoring gateway
        returned, so nothing here recomputes its accuracy, composite or evaluator namespace.
        Doing so was a second code path onto the same rows with the same scorer, and it fed
        round 0's row while the ledger got the gateway's answer.
        """
        origin_accuracy = origin_report.accuracy
        opt_sp = _build_initial_opt_sp(resolved_origin, task_context)
        # Pass session.pipeline_params (carries dataset overlay) — schema.to_pipeline_params() is sparse and strips operator config.
        sp = opt_sp.to_job_search_point(
            base_pipeline_params=session.pipeline_params or None,
            schema=schema,
        )
        _assert_overlay_preserved(sp, session.pipeline_params)
        _inherit_sibling_runtime_failures(opt_sp, session)
        from promptpotter.application.intelligence.hard_sample_archive import (
            build_archive_observations,
        )

        # ONE archive walk, both consumers. The ruler and the intelligence layer asked for
        # the same observations at the same moment and each loaded them — a full fold plus
        # a detail read per grade-A run, done twice.
        origin_sp_hash = sp.sp_hash(schema)
        archive_obs = build_archive_observations(
            session.store,
            dataset_name=session.dataset_name,
            origin_sp_hash=origin_sp_hash,
        )
        delta_scale, origin_theta, calibration_model = _calibrate_delta_ruler(
            session,
            origin_results,
            config.optimization.elimination_n_min,
            enable_2pl=config.optimization.enable_2pl_graduation,
            archive_obs=archive_obs,
        )
        from promptpotter.application.intelligence.earned_blocks import (
            answer_space_signature,
            earned_library_for,
        )

        # Mine the earned block library for THIS task's answer-space shape, once — the
        # `guidance` catalogue renders it instead of the static seed set, silent when no block
        # earned credible lift on a matching shape (the dispatch-first "signal or silence" rule).
        earned_blocks = earned_library_for(
            session.store,
            answer_space_signature(r.get("ground_truth") for r in (origin_results or [])),
        )
        return cls(
            session=session,
            config=config,
            earned_blocks=earned_blocks,
            rounds=[
                _origin_round(
                    opt_sp,
                    sp,
                    report=origin_report,
                    results=list(origin_results or []),
                    theta=origin_theta,
                    calibration_model=calibration_model,
                )
            ],
            tracking=CycleRoundState(
                current_sp=sp,
                current_accuracy=origin_accuracy,
                current_composite_fitness=origin_report.composite_fitness,
                current_results=origin_results or [],
                best_accuracy=origin_accuracy,
                best_composite_fitness=origin_report.composite_fitness,
                best_sp=sp,
                best_theta=origin_theta[0] if origin_theta is not None else None,
            ),
            opt_sp=opt_sp,
            archive_observations=archive_obs,
            origin_sp_hash=origin_sp_hash,
            delta_scale=delta_scale,
            calibration_model=calibration_model,
        )

    @property
    def origin_round(self) -> RoundResult:
        """Round 0 — the origin's measurement, located like any round. Its accuracy,
        composite, evaluators, per-sample results, θ, verdict and critique are round
        fields; none of them is a sidecar on this object."""
        return self.rounds[0]

    def restamp_origin_round(self, parent: RoundParent) -> None:
        """Replace round 0 with a fresh measurement of the same origin — the origin
        gate's rescore. A whole round in, a whole round out: re-measuring C0 cannot
        leave one of its fields (evaluators, the CI whisker, the verdict) reading
        from the run before the fix. θ is carried, not re-fit: the ruler is locked."""
        assert self.tracking.current_sp is not None
        prior = self.origin_round
        carried = (
            None
            if prior.cumulative_theta is None or prior.cumulative_theta_se is None
            else (prior.cumulative_theta, prior.cumulative_theta_se)
        )
        self.rounds[0] = _origin_round(
            self.opt_sp,
            self.tracking.current_sp,
            report=parent.report,
            results=list(parent.results),
            theta=carried,
            calibration_model=self.calibration_model,
        )

    def replay_priors(self, priors: list[RoundResult]) -> None:
        """Reconstruct round-loop state from persisted prior rounds (in-place).

        ``EscalationFSM`` is NOT touched — caller rebuilds via ``from_ledger``.

        A persisted round supersedes the same-numbered one already in memory: round 0
        is the only one ``start`` builds, and the file it was written to is the record
        of what actually ran (its verdict, its critique, a gate rescore). A fork whose
        priors begin at round 1 keeps the round 0 it inherited.
        """
        if not priors:
            return
        schema = self.session.pipeline_schema
        tr = self.tracking
        by_round = {rr.round: rr for rr in self.rounds}
        by_round.update({rr.round: rr for rr in priors})
        self.rounds = [by_round[n] for n in sorted(by_round)]
        l1_rounds = [rr for rr in self.rounds if rr.round > 0]
        if not l1_rounds:
            # Resumed right after the origin — no L1 trajectory to replay, and
            # ``start`` already seeded tracking from round 0.
            return
        last_rr = l1_rounds[-1]
        if last_rr.opt_sp is None or last_rr.pipeline_params is None:
            raise ValueError(
                f"round {last_rr.round} closed without an opt_sp / pipeline_params — "
                "the round file cannot seed a resume."
            )
        # Deep copy: the cycle mutates its working OSP (wounds, memory), and the round
        # is a record of what ran, not a scratchpad.
        self.opt_sp = last_rr.opt_sp.model_copy(deep=True)
        for f in PROMPT_STRING_FIELDS:
            setattr(self.opt_sp, f, last_rr.prompt_fields.get(f, ""))
        # The winner's OWN resolved params — not the origin's. Reading them off the round
        # is only possible because the round file records them; while it didn't, resume
        # reverted every config axis L1 had won back to the origin floor.
        tr.current_sp = self.opt_sp.to_job_search_point(
            base_pipeline_params=last_rr.pipeline_params, schema=schema
        )
        # best_round = high-water-mark over what each round actually MEASURED, walked from
        # round 0 (the origin floor) forward. It is the round NUMBER, which is now also its
        # index. Each round's own scalars — never a score over `acc_cum`, whose rows come
        # from different configurations (see `_merge_known_outcomes`); the pool is rebuilt
        # here only to reseed `current_results` and `best_theta` below.
        acc_cum: list[dict[str, Any]] = []
        for rr in self.rounds:
            acc_cum = _merge_known_outcomes(acc_cum, list(rr.results))
            if rr.composite_fitness > tr.best_composite_fitness:
                tr.best_composite_fitness = rr.composite_fitness
                tr.best_accuracy = rr.accuracy
                tr.best_round = rr.round
                # Build best_sp from THIS round's prompts, not self.opt_sp (pinned to the last
                # prior above) — else a resumed best≠last cycle pairs best params with last text.
                best_opt_sp = self.opt_sp.model_copy(
                    update={f: rr.prompt_fields.get(f, "") for f in PROMPT_STRING_FIELDS}
                )
                tr.best_sp = best_opt_sp.to_job_search_point(
                    base_pipeline_params=rr.pipeline_params, schema=schema
                )
            # best_theta is a running max over the same cumulative frontier as
            # best_composite — re-maxed here so a resumed cycle reconstructs exactly
            # what fresh absorb_round held (seeded from origin_theta by Cycle.start).
            cum = _cumulative_theta(acc_cum, self.delta_scale)
            if cum is not None and (tr.best_theta is None or cum[0] > tr.best_theta):
                tr.best_theta = cum[0]
        tr.current_results = acc_cum
        # Mirrors `absorb_round`: "current" is the last round's OWN measurement.
        tr.current_accuracy = last_rr.accuracy
        tr.current_composite_fitness = last_rr.composite_fitness

    def _maybe_warm_ruler(self) -> None:
        """Warm the δ ruler from a cold start, then LOCK it. While ``delta_scale`` is
        still flat (a fresh dataset with no grade-A archive), re-attempt calibration
        from the round observations accumulated so far; the first fit that clears the
        ``elimination_n_min`` warmth floor is adopted and never re-fit — so warm rounds
        all compare on one shared ruler (comparability preserved), while the cold rounds
        that preceded it already compared on the frozen subset's raw accuracy. Repeat/
        reference-backed campaigns start warm at ``Cycle.start`` and skip this entirely.

        A fresh campaign is cold for exactly one round: ``Cycle.start`` sees only C0, which is
        one arm and so cannot identify δ (``_calibrate_delta_ruler``), and the first re-attempt
        after round 1 closes reads that round's candidates back out of the archive. Round 1 is
        therefore always measured on the frozen bank prefix — the deliberate consequence, since
        an adaptive subset drawn against a one-arm ruler selects the incumbent's own failures.

        Re-reading the archive is what re-warms it: this fires from ``absorb_round`` *after* the
        round closed, so every candidate it scored is already banked grade-A — under its own
        searchpoint identity. Feeding the in-cycle rounds in beside the archive read would only
        re-enter the same measurements under a second name.
        """
        from promptpotter.application.intelligence.hard_sample_archive import (
            build_archive_observations,
        )

        if self.delta_scale:
            return
        delta_scale, origin_theta, calibration_model = _calibrate_delta_ruler(
            self.session,
            self.origin_round.results,
            self.config.optimization.elimination_n_min,
            enable_2pl=self.config.optimization.enable_2pl_graduation,
            archive_obs=build_archive_observations(
                self.session.store,
                dataset_name=self.session.dataset_name,
                origin_sp_hash=self.origin_sp_hash,
            ),
        )
        if delta_scale:  # warmed — lock the ruler + re-read every θ already taken on it
            self.delta_scale = delta_scale
            self.calibration_model = calibration_model
            # Round 0 carries θ twice — its own frontier and C0's row — and a warm fit
            # must move both, or the round file reports the origin at two abilities.
            o_theta, o_se = origin_theta if origin_theta is not None else (None, None)
            self.origin_round.cumulative_theta = o_theta
            self.origin_round.cumulative_theta_se = o_se
            self.origin_round.candidate_scores = [
                c.model_copy(update={"theta": o_theta, "theta_se": o_se})
                for c in self.origin_round.candidate_scores
            ]
            # …and every L1 round that already closed, which is the same argument one line up
            # and used to be skipped. A round that closed while the ruler was flat had its θ
            # fit at δ≡0, where ``fit_theta_given_delta`` degenerates to logit-accuracy on that
            # round's own subset — a DIFFERENT scale from the one every later round lands on.
            # Left unrestamped they sit side by side in ``round_adopted_levels``, and the L4 law
            # averages the mixture and differences it against a warm-ruler origin. Free to fix:
            # the rows are all in hand, and this is the walk ``_bind_rounds`` already does.
            frontier: list[dict[str, Any]] = []
            for rr in self.rounds:
                frontier = _merge_known_outcomes(frontier, list(rr.results))
                if rr.round > 0:
                    restamped = _cumulative_theta(frontier, delta_scale)
                    rr.cumulative_theta, rr.cumulative_theta_se = (
                        restamped if restamped is not None else (None, None)
                    )
                    rr.calibration_model = calibration_model

    def adopt(self, new_incumbent: OptSearchPoint, *, advanced: dict[str, Any]) -> None:
        """Make ``new_incumbent`` the cycle's searchpoint — the ONE adoption seam for
        an L1 win and an L2/L3 transition alike. Identity advances: ``new_incumbent``
        already carries its own lineage (parent = the outgoing incumbent). The
        persistent L2/L3 memory (wounds, l1_layout, l1_overrides, task_context) carries
        forward from the outgoing incumbent via ``copy_memory_to``; only the surfaces in
        ``advanced`` are then taken from ``new_incumbent`` — the layer that owns them
        (L1 → task_context; L2 → task_context/l1_layout). The differentiated carry lives
        here, not in two hand-coded call sites."""
        self.opt_sp.copy_memory_to(new_incumbent)
        for surface, val in advanced.items():
            setattr(new_incumbent.memory, surface, val)
        self.opt_sp = new_incumbent

    def absorb_round(
        self,
        rr: RoundResult,
        round_num: int,
    ) -> RoundResult:
        """Sole sink for a finished L1 round; returns the round, stamped for ``save_round_file``."""
        schema = self.session.pipeline_schema
        tr = self.tracking

        existing_keys = {
            rf_dedup_key(rf.model_dump()) for rf in self.opt_sp.memory.wounds.runtime_failures
        }
        for cs in rr.candidate_scores:
            for rf in cs.runtime_failures:
                k = rf_dedup_key(rf.model_dump())
                if k in existing_keys:
                    continue
                existing_keys.add(k)
                self.opt_sp.memory.wounds.runtime_failures.append(rf)

        self.rounds.append(rr)
        self._maybe_warm_ruler()
        # Advance the incumbent to the elected winner. The winner OSP already carries
        # its own lineage (parent = this incumbent), so identity — not just the six
        # prompt strings — moves forward, and next round's candidates descend from the
        # winner. A HELD round returns the incumbent itself as the "winner" (origin.opt_sp),
        # so the lineage ids match and nothing is adopted: the incumbent is unchanged and
        # mints no node. The winner's task_context (an L1 child can win on a framing
        # override, its 3rd mutation slot) rides along; wounds + l1_layout carry from the
        # outgoing incumbent — all through the single `adopt` seam.
        winner_opt_sp = rr.opt_sp
        if winner_opt_sp is not None and winner_opt_sp.lineage.id != self.opt_sp.lineage.id:
            self.adopt(winner_opt_sp, advanced={"task_context": winner_opt_sp.memory.task_context})
        assert tr.current_sp is not None
        _pp = (
            rr.pipeline_params if rr.pipeline_params is not None else tr.current_sp.pipeline_params
        )
        tr.current_sp = self.opt_sp.to_job_search_point(base_pipeline_params=_pp, schema=schema)
        tr.current_results = _merge_known_outcomes(tr.current_results, list(rr.results))
        # "Current" is what the incumbent SCORED — its own measurement on its own samples,
        # never an accuracy over the mixed-provenance pool above (see `_merge_known_outcomes`).
        # On a held round `rr` already carries the incumbent's re-score for this round's
        # subset (`rescore_parent`), so this stays a real measurement either way.
        tr.current_accuracy, tr.current_composite_fitness = rr.accuracy, rr.composite_fitness
        if tr.current_composite_fitness > tr.best_composite_fitness:
            tr.best_composite_fitness = tr.current_composite_fitness
            tr.best_accuracy = tr.current_accuracy
            tr.best_round = round_num
            tr.best_sp = tr.current_sp
        cur = _cumulative_theta(tr.current_results, self.delta_scale)
        if cur is not None and (tr.best_theta is None or cur[0] > tr.best_theta):
            tr.best_theta = cur[0]

        rr.cumulative_theta, rr.cumulative_theta_se = cur if cur is not None else (None, None)
        rr.calibration_model = self.calibration_model
        rr.opt_sp = self.opt_sp
        return rr
