"""Per-candidate three-path lifecycle: validation-skip (synthetic 0, no eval), cache-replay (no backend calls), and
full eval classified into SCORED / LEADER_LOCKED / ESCALATED."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any

from promptpotter.application.optimization.l1.population import (
    INVALID_SCORES,
    build_score_report,
    fatal_validation_failures,
)
from promptpotter.application.optimization.l1.score.signal_effect import (
    CandidateOutcome,
    decode_signal_effect,
)
from promptpotter.application.optimization.pobb.checks import PoBBCheck
from promptpotter.application.optimization.resume_and_fork.decisions import (
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
from promptpotter.shared.instrument import MeasuredCandidate, MeasurementRole

if TYPE_CHECKING:
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.application.run_observers import RunCallbacks
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.search_point import JobSearchPoint


@dataclass(frozen=True)
class CandidateRunResult:
    """One candidate's full lifecycle output. ``runtime_failure`` is the CALLER's signal to append to the wounds: this
    function cannot mutate them, because the searchpoint is shared with other paths."""

    outcome: CandidateOutcome
    report: ScoredCandidate
    results: list[QueryMeasurement] = field(default_factory=list)
    runtime_failure: RuntimeFailure | None = None
    escalation_signal: EscalationSignal | None = None


async def score_one_candidate(
    *,
    idx: int,
    opt_sp_c: OptSearchPoint,
    candidate_sp: JobSearchPoint,
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
    force_fresh: bool = False,
) -> CandidateRunResult:
    """One candidate through the three-exit-path lifecycle. ``candidate_sp`` is built ONCE by the caller and shared with the in-flight
    dashboard seed, so the origin⊕delta merge happens at a single site."""
    label = candidate_label(round_num, idx)
    resolved_pipeline_params = candidate_sp.config_params

    # Who this pass measures, handed to the gateway rather than bound here — every
    # re-entrant asker declares its own, so none can inherit this one. The L4 recursion
    # reads it to stamp an inner campaign's provenance; the connector seam carries only
    # `(query, payload)`, so identity cannot reach it any other way.
    measured = MeasuredCandidate(
        idx=idx, candidate_id=opt_sp_c.lineage.id, label=label, role=MeasurementRole.PANEL
    )

    # Path 1 — validation-skip synthetic-0. A ``hallucinated_node`` wound is the one
    # NON-fatal validation failure: L1 named a node that doesn't exist, but that phantom
    # edit is simply stripped from the wire — the candidate's real edits still ran, so its
    # score stands and the wound rides along as routed signal (``l1_wounds`` +
    # ``validation_failure_rate``), not a synthetic-0. Every other failure (forbidden axis,
    # type mismatch, out-of-enum value) is a genuinely invalid program and still nukes it.
    if fatal_validation_failures(opt_sp_c):
        return CandidateRunResult(
            outcome=CandidateOutcome.SKIPPED_VALIDATION,
            results=[],
            report=build_score_report(
                opt_sp_c,
                pipeline_params_override,
                INVALID_SCORES,
                [],
                dataset,
                label=label,
                resolved_pipeline_params=resolved_pipeline_params,
                invalid=True,
                l1_diversity=l1_diversity,
            ),
        )

    async def _catch_priors_up(sample: Sample) -> None:
        fresh = await elim_check.backfill_for_sample(sample)
        if fresh:
            callbacks.on_pobb_backfill(round_num, idx, n_total, sample.id, fresh)

    results, scores, signal = await score_search_point(
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
        opt_sp=opt_sp_c,
        measured=measured,
        on_sample_pre_check=_catch_priors_up,
        force_fresh=force_fresh,
    )

    # Path 2 — scored. Snapshot priors BEFORE eval registers this candidate.
    priors_at_test = list(elim_check.prior_ids)
    effect = decode_signal_effect(
        signal,
        results=results,
        dataset=dataset,
        effective_pipeline_params=effective_pipeline_params,
        round_num=round_num,
        elim_check=elim_check,
        candidate_id=opt_sp_c.lineage.id,
        candidate_label=opt_sp_c.lineage.changes_description or "",
        priors_at_test=priors_at_test,
    )
    # Aborted candidates must NOT seed priors — their scores are synthetic 0s.
    if len(results) == len(dataset) and not effect.aborted:
        elim_check.register_completed(results, candidate_id=opt_sp_c.lineage.id, sp=candidate_sp)

    report = build_score_report(
        opt_sp_c,
        pipeline_params_override,
        scores,
        results,
        dataset,
        label=label,
        resolved_pipeline_params=resolved_pipeline_params,
        aborted=effect.aborted,
        elimination_stopped=effect.elimination_stopped,
        elimination_context=effect.elim_context,
        degradation_context=effect.degradation_context,
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


__all__ = ["score_one_candidate"]
