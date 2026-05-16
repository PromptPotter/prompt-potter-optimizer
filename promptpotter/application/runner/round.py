"""Round-boundary helpers: persist + close + post-round + escalation hook.

Every helper here is part of the round-by-round bookkeeping the main
loop pivots around. Per CLAUDE.md root: the ledger is the sole
persistence ingress and display projections subscribe to it; bypassing
this seam collapses display + audit for the round.
"""

from __future__ import annotations

import logging

from promptpotter.application.bootstrap.session import Session
from promptpotter.application.config import CampaignConfig
from promptpotter.application.optimization.cycle import Cycle
from promptpotter.application.optimization.escalation import NextAction, escalate_l2
from promptpotter.application.run_observers import RunCallbacks
from promptpotter.domain.phases import StopLoop
from promptpotter.domain.results import RoundResult
from promptpotter.domain.run_records import PhaseRecord, ResumeCheckpointRecord
from promptpotter.presentation.writers import (
    write_hard_samples_artifacts,
    write_log_md,
    write_review_md,
)
from promptpotter.shared.errors import graceful

logger = logging.getLogger(__name__)


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
    trial_dict: dict,
    round_num: int,
    session: Session,
    *,
    is_probe: bool = False,
) -> None:
    """Flush decisions, mirror to ledger, write round_data + log.md/review.md, flush recorder.

    ``is_probe`` rides the ``round:complete`` payload so the reducer side
    (``EscalationState.fold``) can ignore probe rounds without the writer
    omitting the emit. The emit itself is unconditional — every round that
    runs to completion lands on the ledger, per §0's "ledger is the sole
    persistence ingress; display subscribes to the ledger".
    """
    flushed: list[ResumeCheckpointRecord] = []
    if cycle.pending_decisions:
        flushed = list(cycle.pending_decisions)
        cycle.pending_decisions.clear()
        round_result.decisions.extend(d.to_dict() for d in flushed)
        trial_dict["decisions"] = list(round_result.decisions)

    # Stash the axis-memory peaked set on the round dict so the post-round
    # ``evidence_grounding_present`` behaviour check (run from review.py
    # against on-disk round files) can reject variants citing axis_memory
    # to justify mutating a peaked axis. AxisIndex isn't reconstructable
    # from a round_NNNN.json alone, so we persist the derived set here.
    if cycle.axes is not None:
        trial_dict["axis_memory_peaked"] = sorted(cycle.axes.peaked_axes())

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
                session.backend_id,
                session.state.cycle_id,
                trial_dict,
            )
        hard_samples_artifact = write_hard_samples_artifacts(session, cycle)
        write_log_md(session, hard_samples_artifact=hard_samples_artifact)
        write_review_md(session, cycle)

    if _rr := session.state.audit_projection:
        _rr.flush()


def count_positive_yield_axes(cycle: Cycle) -> int | None:
    """Count axes with effect_size above the AxisIndex noise floor.

    Returns ``None`` when AxisIndex isn't initialised (pre-first-round); a
    rule consulting this signal must treat ``None`` as "no evidence yet."
    The threshold mirrors :data:`promptpotter.application.intelligence.indexes.axis.NOISE_THRESHOLD`
    (= 0.02) — same definition the digest formatter uses.
    """
    if cycle.axes is None:
        return None
    from promptpotter.application.intelligence.indexes.axis import NOISE_THRESHOLD

    return sum(1 for r in cycle.axes.axis_rankings() if r.effect_size > NOISE_THRESHOLD)


async def close_round(
    cycle: Cycle,
    round_result: RoundResult,
    trial_dict: dict,
    round_num: int,
    session: Session,
    cb: RunCallbacks,
    *,
    is_probe: bool = False,
) -> None:
    """Round-completion bookkeeping that EVERY completed round runs.

    Emits the ledger event trio's tail (``round:display`` via
    ``cb.on_round_complete`` and ``round:complete`` via ``persist_round``),
    writes ``round_NNNN.json``, refreshes axis memory. Independent of
    whether the round feeds escalation — that decision is the caller's.
    Per §0 of ``docs/architecture.md``: the ledger is the sole persistence
    ingress and display projections subscribe to it; bypassing this seam
    collapses display + audit for the round.
    """
    cb.on_round_complete(round_result, cycle.escalation.l1_stall_count)
    persist_round(cycle, round_result, trial_dict, round_num, session, is_probe=is_probe)
    if cycle.axes and session.store and session.backend_id:
        cycle.axes.refresh(
            session.store,
            session.backend_id,
            scorer=session.scoring.scorer,
            scorer_id=session.scoring.scorer_id,
            scorer_formula=session.scoring.scorer_formula,
        )


async def post_round(
    cycle: Cycle,
    round_result: RoundResult,
    trial_dict: dict,
    round_num: int,
    config: CampaignConfig,
    session: Session,
    cb: RunCallbacks,
) -> None:
    """Clean-round escalation observation. Raises StopLoop on stop condition.

    The state machine observes the round outcome up front (bumping L1 stall,
    deciding CONTINUE / FIRE_L2 / STOP_*); the rest of this function closes
    the round (via ``close_round``) and dispatches the chosen action. Probe
    rounds bypass observation and call ``close_round`` directly.
    """
    axes_with_positive_yield = count_positive_yield_axes(cycle)
    event = cycle.escalation.observe_round(
        improved=round_result.improved,
        current_accuracy=cycle.tracking.current_accuracy,
        l1_patience=config.optimization.l1_patience,
        axes_with_positive_yield=axes_with_positive_yield,
    )

    await close_round(cycle, round_result, trial_dict, round_num, session, cb)

    if event.stop_reason is not None:
        raise StopLoop(event.stop_reason)
    if event.next_action == NextAction.FIRE_L2:
        await escalate_or_stop(cycle, config, session, round_num, cb)


__all__ = [
    "close_round",
    "count_positive_yield_axes",
    "escalate_or_stop",
    "persist_round",
    "post_round",
]
