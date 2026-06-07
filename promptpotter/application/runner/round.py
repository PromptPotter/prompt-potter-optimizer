"""Round-boundary helpers — persist, close, post-round, escalation hook.

The ledger is the sole persistence ingress; display projections subscribe to it
(bypassing this seam collapses display + audit for the round)."""

from __future__ import annotations

import logging
from typing import Any

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
    round_payload: dict[str, Any],
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
        round_payload["decisions"] = list(round_result.decisions)

    # Persist axis-memory peaked set on the round dict — review.py's
    # ``evidence_grounding_present`` check needs it (AxisIndex isn't reconstructable from round_NNNN.json alone).
    if cycle.axes is not None:
        round_payload["axis_memory_peaked"] = sorted(cycle.axes.peaked_axes())

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
                round_payload,
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
    round_payload: dict[str, Any],
    round_num: int,
    session: Session,
    cb: RunCallbacks,
    *,
    is_probe: bool = False,
) -> None:
    """Round-completion bookkeeping every completed round runs.
    Emits ``round:display`` (via ``cb.on_round_complete``) + ``round:complete`` (via ``persist_round``),
    writes ``round_NNNN.json``, refreshes axis memory. Independent of whether the round feeds escalation."""
    cb.on_round_complete(round_result, cycle.escalation.l1_stall_count)
    persist_round(cycle, round_result, round_payload, round_num, session, is_probe=is_probe)
    if cycle.axes and session.store and session.backend_id:
        cycle.axes.refresh(
            session.store,
            session.backend_id,
            scorer=session.scoring.scorer,
            scorer_id=session.scoring.scorer_id,
            scorer_formula=session.scoring.scorer_formula,
            dataset_name=session.dataset_name,
        )


async def post_round(
    cycle: Cycle,
    round_result: RoundResult,
    round_payload: dict[str, Any],
    round_num: int,
    config: CampaignConfig,
    session: Session,
    cb: RunCallbacks,
) -> None:
    """Clean-round escalation observation; raises ``StopLoop`` on stop condition.
    State machine observes outcome (CONTINUE / FIRE_L2 / STOP_*), then closes the round and dispatches.
    Probe rounds bypass observation and call ``close_round`` directly."""
    axes_with_positive_yield = count_positive_yield_axes(cycle)
    event = cycle.escalation.observe_round(
        improved=round_result.improved,
        current_accuracy=cycle.tracking.current_accuracy,
        l1_patience=config.optimization.l1_patience,
        axes_with_positive_yield=axes_with_positive_yield,
    )

    await close_round(cycle, round_result, round_payload, round_num, session, cb)

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
