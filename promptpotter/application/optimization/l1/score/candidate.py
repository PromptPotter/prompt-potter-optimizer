"""Per-candidate three-path lifecycle — :func:`score_one_candidate`.

Path 1 — validation-skip: synthetic-0 score (no eval). Path 2 —
cache-replay: full-run cache hit (no backend calls). Path 3 — scored:
full eval, classified into SCORED / LEADER_LOCKED / ESCALATED.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any

from promptpotter.application.optimization.l1.population import (
    INVALID_SCORES,
    build_score_report,
)
from promptpotter.application.optimization.l1.score.signal_effect import (
    CandidateOutcome,
    decode_signal_effect,
)
from promptpotter.application.optimization.pobb.elimination import PoBBCheck
from promptpotter.application.optimization.resume_and_fork import (
    ResumeCheckpointKind,
    ResumeCheckpointRecord,
    record_decision,
)
from promptpotter.application.scoring.search_point_scorer import score_search_point
from promptpotter.domain.escalation_signals import EscalationSignal, RuntimeFailure
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.results import ScoredCandidate, candidate_label
from promptpotter.domain.scoring import QueryMeasurement
from promptpotter.domain.validators import StopRule

if TYPE_CHECKING:
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.application.run_observers import RunCallbacks
    from promptpotter.domain.sample import Sample


@dataclass(frozen=True)
class CandidateRunResult:
    """One candidate's full lifecycle output. ``runtime_failure`` is the
    caller's signal to append to ``osp_c.memory.wounds.runtime_failures``; the function
    cannot mutate it directly because the OSP is shared with other paths."""

    outcome: CandidateOutcome
    results: list[QueryMeasurement] = field(default_factory=list)
    report: ScoredCandidate = None  # type: ignore[assignment]
    runtime_failure: RuntimeFailure | None = None
    escalation_signal: EscalationSignal | None = None


async def score_one_candidate(
    *,
    idx: int,
    osp_c: OptSearchPoint,
    pipeline_params_override: dict[str, Any] | None,
    cycle: Cycle,
    dataset: list[Sample],
    n_total: int,
    effective_pipeline_params: dict[str, Any] | None,
    elim_check: PoBBCheck,
    callbacks: RunCallbacks,
    degradation_checks: list[StopRule] | None,
    decisions: list[ResumeCheckpointRecord] | None,
    candidate_scores: list[ScoredCandidate],
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
    if osp_c.memory.wounds.validation_failures:
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
        base_pipeline_params=effective_pipeline_params, schema=cycle.session.pipeline_schema
    )

    async def _catch_priors_up(sample: Sample) -> None:
        fresh = await elim_check.backfill_for_sample(sample)
        if fresh:
            callbacks.on_pobb_backfill(round_num, idx, n_total, sample.id, fresh)

    results, scores, from_cache, signal = await score_search_point(
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
        on_sample_pre_check=_catch_priors_up,
    )

    # Path 2 — full-run cache replay.
    if from_cache:
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
        effective_pipeline_params=effective_pipeline_params,
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


__all__ = ["CandidateRunResult", "score_one_candidate"]
