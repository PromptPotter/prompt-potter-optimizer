"""Round-boundary helpers — persist, close, post-round, escalation hook.

The ledger is the sole persistence ingress; display projections subscribe to it
(bypassing this seam collapses display + audit for the round)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from promptpotter.application.bootstrap.session import Session
from promptpotter.application.config import CampaignConfig
from promptpotter.application.optimization.cycle import Cycle
from promptpotter.application.optimization.escalation import NextAction, escalate_l2
from promptpotter.application.optimization.l1.critique import run_l1_critique
from promptpotter.application.optimization.round_analysis import compute_round_diagnostics
from promptpotter.application.optimization.validators.l1_strict import (
    DROPPED_MANDATORY_PLACEHOLDER,
)
from promptpotter.application.output import (
    write_hard_samples_artifacts,
    write_log_md,
    write_review_md,
)
from promptpotter.application.run_observers import RunCallbacks
from promptpotter.application.scoring.metrics import _compute_accuracy, composite_ci
from promptpotter.domain.phases import StopLoop
from promptpotter.domain.results import (
    RoundResult,
    ScoredCandidate,
    candidate_label,
)
from promptpotter.domain.results_health import (
    assemble_prior_healths,
    compute_node_failure_rates,
    compute_round_health,
    evidence_starved_node,
)
from promptpotter.domain.run_records import PhaseRecord, ResumeCheckpointRecord
from promptpotter.infrastructure.tracing import observed_node
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.domain.scoring import QueryMeasurement

logger = logging.getLogger(__name__)


async def emit_origin_round(
    cycle: Cycle,
    session: Session,
    cb: RunCallbacks,
) -> None:
    """Emit the scored origin as **round 0** through the standard completion path.

    Origin is not a lesser, separate entity — it's the first round. This builds a
    one-candidate ``RoundResult`` from the cycle's frozen origin state
    (``cycle.tracking.origin_*`` + ``cycle.opt_sp``) and closes it via the same
    ``close_round`` seam every L1 round uses, so the public round file
    (``rounds/round_0000.json``), the ``index.json::rounds[]`` entry, and the live
    ``dashboard.json::rounds[]`` summary all carry round 0 in the identical shape.

    It deliberately does **not** call ``cycle.absorb_round`` — ``cycle.rounds``
    stays the 1-indexed L1 trajectory (origin is the floor it improves on, not a
    member of it) — and does **not** feed escalation (``close_round`` never does;
    only ``post_round`` observes). Idempotent at the call site: the loop guards on
    the round-0 file already existing.
    """
    tr = cycle.tracking
    osp = cycle.opt_sp
    results = list(tr.origin_per_sample_results)
    base = _compute_accuracy(cast("list[QueryMeasurement]", results))
    prompt_fields = {**osp.prompt_field_dict(), "lineage": osp.lineage.model_dump()}
    label = candidate_label(0, 0)  # "C0"
    # C0 is enriched through the SAME shared derivations every L1 candidate uses, so
    # the origin row can't silently omit a field the L1 path stamps: ``composite_ci``
    # (the always-on whisker, one reader home in metrics.py), its difficulty-adjusted
    # ability θ (the frozen round-0 ``origin_theta`` on the cycle's δ ruler — the exact
    # floor ``c0_ok`` judges every winner against), and the per-evaluator ``evaluators``
    # breakdown. All three are already-computed values (``tracking``); the origin round
    # bypasses ``l1_score`` only for population/election, not for these.
    ci_lo, ci_hi = composite_ci(cast("list[QueryMeasurement]", results))
    sc = ScoredCandidate(
        candidate_id=osp.lineage.id,
        label=label,
        # changes_description == round label ⇒ ``is_round_winner`` marks the sole
        # origin candidate the winner, same rule every round's winner uses.
        changes_description=label,
        accuracy=tr.origin_accuracy,
        composite_fitness=tr.origin_composite_fitness,
        composite_ci_lo=ci_lo,
        composite_ci_hi=ci_hi,
        theta=tr.origin_theta,
        evaluators=dict(tr.origin_evaluators),
        hits=base["hits"],
        total=base["total"],
        prompt_fields=prompt_fields,
        resolved_pipeline_params=(tr.current_sp.config_params if tr.current_sp else None),
        scored_samples=base["total"],
        expected_samples=base["total"],
        matched_origin_accuracy=tr.origin_accuracy,
        matched_origin_hits=base["hits"],
        matched_origin_composite=tr.origin_composite_fitness,
    )
    round_result = RoundResult(
        round=0,
        label=label,
        accuracy=tr.origin_accuracy,
        composite_fitness=tr.origin_composite_fitness,
        hits=base["hits"],
        total=base["total"],
        improved=False,
        origin_accuracy=tr.origin_accuracy,
        matched_origin_accuracy=tr.origin_accuracy,
        matched_origin_hits=base["hits"],
        matched_origin_composite=tr.origin_composite_fitness,
        prompt_fields=prompt_fields,
        pipeline_params=(tr.current_sp.pipeline_params if tr.current_sp else None),
        results=results,
        all_candidate_results={osp.lineage.id: results},
        candidates_scored=1,
        candidate_scores=[sc],
        deprecated=base["deprecated"],
        cumulative_accuracy=tr.origin_accuracy,
        # Round 0's frontier θ is the origin's θ (what ``best_theta`` seeds to), the θ-peer of
        # ``cumulative_accuracy=origin_accuracy`` — so the trend line starts on the θ scale too.
        cumulative_theta=tr.origin_theta,
        evaluators=dict(tr.origin_evaluators),
    )
    # Seed round 1's L1 with a critique over the origin's misses. The loop runs
    # critique only at round end (``l1/execute.py``), so without this seed round 1
    # opens blind — it never sees the per-sample failure pattern (here: predicted
    # material vs ground-truth process) and falls back to surface-axis guesses.
    # Sweep/diag forks inherit round 0 and never call this path, so the seed is
    # automatically off there (round 1 stays bit-identical across cheap forks).
    if results:
        round_result.diagnostics = compute_round_diagnostics(
            round_result,
            [round_result],
            session.pipeline_schema,
        )
        with graceful("Origin critique failed; round 1 proceeds without seeded feedback"):
            async with observed_node(
                "l1_critique_r0",
                "llm/meta",
                obs=session.state.obs,
                campaign_id=session.state.tracing_campaign_id,
                round_num=0,
            ):
                critique_result = await run_l1_critique(
                    cycle, round_result, round_num=0, ledger=session.state.ledger
                )
            round_result.critique = critique_result
            cycle.origin_critique = critique_result

    round_result.opt_search_point = osp
    await close_round(cycle, round_result, 0, session, cb)


async def escalate_or_stop(
    cycle: Cycle,
    config: CampaignConfig,
    session: Session,
    round_num: int,
    cb: RunCallbacks,
) -> None:
    """Run L2 escalation; raise ``StopLoop`` if it returned a stop reason."""
    stop = await escalate_l2(
        cycle,
        config,
        session.pipeline_schema,
        round_num,
        cb.on_phase,
        obs=session.state.obs,
        tracing_campaign_id=session.state.tracing_campaign_id,
    )
    if stop:
        raise StopLoop(stop)


def persist_round(
    cycle: Cycle,
    round_result: RoundResult,
    round_num: int,
    session: Session,
    *,
    is_probe: bool = False,
) -> None:
    """Flush decisions, mirror to ledger, write round_data + log.md/review.md, flush recorder.
    ``is_probe`` rides the ``round:complete`` payload so ``EscalationFSM.fold`` can ignore probe
    rounds; the emit is unconditional (every completed round lands on the ledger)."""
    flushed: list[ResumeCheckpointRecord] = []
    if cycle.pending_decisions:
        flushed = list(cycle.pending_decisions)
        cycle.pending_decisions.clear()
        round_result.decisions.extend(d.to_dict() for d in flushed)

    if cycle.axes is not None:
        round_result.axis_memory_peaked = sorted(cycle.axes.peaked_axes())

    if (ledger := session.state.ledger) is not None:
        for d in flushed:
            ledger.append(d)
        ledger.append(
            PhaseRecord(
                phase="round",
                event="complete",
                round=round_num,
                payload={
                    "accuracy": round_result.accuracy,
                    "composite_fitness": round_result.composite_fitness,
                    "improved": round_result.improved,
                    "label": round_result.label,
                    "is_probe": is_probe,
                },
            )
        )

    if session.state.cycle_id:
        with graceful("Round checkpoint failed"):
            session.store.campaigns.save_round_file(
                session.campaign_id,
                session.state.cycle_id,
                round_result,
            )
        hard_samples_artifact = write_hard_samples_artifacts(session, cycle)
        write_log_md(session, hard_samples_artifact=hard_samples_artifact)
        write_review_md(session, cycle)

    if _rr := session.state.audit_projection:
        _rr.flush()


def count_positive_yield_axes(cycle: Cycle) -> int | None:
    """Axes with effect_size > AxisIndex noise floor (= 0.02); ``None`` pre-first-round (no AxisIndex yet)."""
    if cycle.axes is None:
        return None
    from promptpotter.application.intelligence.indexes.axis import NOISE_THRESHOLD

    return sum(1 for r in cycle.axes.axis_rankings() if r.effect_size > NOISE_THRESHOLD)


async def close_round(
    cycle: Cycle,
    round_result: RoundResult,
    round_num: int,
    session: Session,
    cb: RunCallbacks,
    *,
    is_probe: bool = False,
) -> None:
    """Round-completion bookkeeping every completed round runs.
    Emits ``round:display`` (via ``cb.on_round_complete``) + ``round:complete`` (via ``persist_round``),
    writes ``round_NNNN.json``, refreshes axis memory. Independent of whether the round feeds escalation.

    SINGLE degradation-verdict compute site (origin + L1 both funnel here): stamp
    ``round_result.health`` BEFORE the dashboard emit (``cb.on_round_complete`` reads
    ``rr.health``) and the round-file write (``persist_round``), so the verdict reaches
    every surface in one shape.

    Track record = prior rounds' verdicts: the origin (round 0) first, then the prior
    L1 rounds. The origin lives on ``cycle.origin_health`` (``cycle.rounds`` is the
    1-indexed L1 trajectory that omits it), so it's prepended explicitly and the
    round-0 entry that ``replay_priors`` leaves in ``cycle.rounds`` is excluded to
    avoid double-counting. The round being closed is excluded too (L1's
    ``absorb_round`` already appended it to ``cycle.rounds``).

    Probe rounds get NO verdict: they rescore a warned-query subset, not the round's
    scoring set, so a grade over that biased slice would be meaningless — and they
    must not seed the track record either. ``health`` stays ``None`` on a probe."""
    if not is_probe:
        round_result.health = compute_round_health(
            hits=round_result.hits,
            total=round_result.total,
            results=round_result.results,
            prior_healths=assemble_prior_healths(cycle.origin_health, cycle.rounds, round_num),
            is_origin=round_num == 0,
        )
        if round_num == 0:
            cycle.origin_health = round_result.health
    cb.on_round_complete(round_result, cycle.escalation.l1_stall_count, cycle.escalation.lives)
    persist_round(cycle, round_result, round_num, session, is_probe=is_probe)
    if cycle.axes and session.store:
        cycle.axes.refresh(
            session.store,
            scorer=session.scoring.scorer,
            scorer_id=session.scoring.scorer_id,
            scorer_formula=session.scoring.scorer_formula,
            dataset_name=session.dataset_name,
        )


async def post_round(
    cycle: Cycle,
    round_result: RoundResult,
    round_num: int,
    config: CampaignConfig,
    session: Session,
    cb: RunCallbacks,
    *,
    is_final_round: bool = False,
) -> None:
    """Clean-round escalation observation; raises ``StopLoop`` on stop condition.
    State machine observes outcome (CONTINUE / FIRE_L2 / STOP_*), then closes the round and dispatches.
    Probe rounds bypass observation and call ``close_round`` directly.

    ``is_final_round`` suppresses the L2 fire: L2's refined ``task_context`` is read by the
    NEXT round's L1, so on the last round it is a ~35s LLM call whose output dies with the
    cycle. The lives-exhausted boundary needs no flag — ``observe_round`` sets
    ``stop_reason`` and we raise before reaching the fire."""
    axes_with_positive_yield = count_positive_yield_axes(cycle)
    # A dropped mandatory backend placeholder is structural, not a stall — heal L2 now
    # (patience 0) instead of burning l1_patience rounds while L1 re-drops it.
    l1_mandatory_breach = any(
        vf.reason == DROPPED_MANDATORY_PLACEHOLDER
        for sc in round_result.candidate_scores
        for vf in sc.validation_failures
    )
    # A zero-candidate round (l1_generate returned nothing parseable) is the same class of
    # structural l1_generate fault as a mandatory-placeholder breach: re-running the identical
    # meta-prompt reproduces it, so route L2 to heal now instead of burning l1_patience dead
    # rounds. `l1_mandatory_breach` reads candidate_scores, which is empty in exactly this round,
    # so it can't catch this — the round owns the signal on `l1_parse_failure`.
    l1_zero_candidates = round_result.l1_parse_failure is not None
    # Evidence-starvation router input, derived from the SAME helper the degradation grade
    # reads (``evidence_starved_node``) so routing and verdict can't diverge. Health itself
    # isn't stamped until ``close_round`` (below), so we read the rates directly here.
    evidence_starved = (
        evidence_starved_node(compute_node_failure_rates(round_result.results, round_result.total))
        is not None
    )
    event = cycle.escalation.observe_round(
        improved=round_result.improved,
        current_accuracy=cycle.tracking.current_accuracy,
        l1_patience=config.optimization.l1_patience,
        lives=config.optimization.lives,
        axes_with_positive_yield=axes_with_positive_yield,
        l1_mandatory_breach=l1_mandatory_breach,
        l1_zero_candidates=l1_zero_candidates,
        evidence_starved=evidence_starved,
    )

    await close_round(cycle, round_result, round_num, session, cb)

    if event.stop_reason is not None:
        raise StopLoop(event.stop_reason)
    if is_final_round:
        return
    if event.next_action == NextAction.FIRE_L2:
        await escalate_or_stop(cycle, config, session, round_num, cb)


__all__ = [
    "close_round",
    "count_positive_yield_axes",
    "emit_origin_round",
    "escalate_or_stop",
    "persist_round",
    "post_round",
]
