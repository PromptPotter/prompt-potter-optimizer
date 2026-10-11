"""``rescore`` re-scores force-fresh, which makes fix-rescore-watch a loop with no re-mint."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
import time
from typing import TYPE_CHECKING, Literal, get_args

from promptpotter.application.datasets.loaders import sample_dataset
from promptpotter.application.run_observers import declare_run_phase
from promptpotter.application.runner.measurement import rescore_parent
from promptpotter.application.runner.round import emit_origin_round
from promptpotter.application.runner.termination import origin_gate_tripped
from promptpotter.domain.phases import GateDecision, RunPhase, StopReason
from promptpotter.infrastructure.llm.heartbeat import heartbeat

if TYPE_CHECKING:
    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.application.campaign_config import CampaignConfig, OriginGateMode
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.run_callbacks import RunCallbacks
    from promptpotter.domain.sample import Sample

logger = logging.getLogger(__name__)

__all__ = ["run_origin_gate"]

_GATE_POLL_S = 1.0

_GateOutcome = Literal["rescore", "proceed", "abort", "pause"]

_DECISIONS: tuple[str, ...] = get_args(GateDecision)


async def run_origin_gate(
    cycle: Cycle,
    dataset: list[Sample],
    config: CampaignConfig,
    session: Session,
    cb: RunCallbacks,
    mode: OriginGateMode,
) -> StopReason | None:
    """``None`` proceeds into L1; a ``rescore`` re-emits round 0 and a still-unhealthy verdict waits again."""
    while True:
        grade = cycle.origin_round.health.grade if cycle.origin_round.health else "unknown"
        logger.warning(
            "Origin gate (%s): round-0 verdict is %s — holding before L1. Decide via "
            "the webapp modal, the TTY prompt, or `python -m promptpotter origin-gate %s`.",
            mode,
            grade,
            "{" + ",".join(_DECISIONS) + "}",
        )
        if sys.stdin is not None and sys.stdin.isatty():
            # ASCII-only: on a cp1252 console any other glyph raises UnicodeEncodeError and ends the run.
            keys = " / ".join(f"{d[0]}={d}" for d in _DECISIONS)
            print(
                f"\n  [ORIGIN GATE: {grade}] type {keys} then Enter (or use the webapp):",
                flush=True,
            )
        declare_run_phase(session, RunPhase.GATE)

        outcome = await _await_gate_decision(session)
        if outcome == "pause":
            return StopReason.PAUSED
        if outcome == "abort":
            logger.warning("Origin gate: abort — ending cycle (origin_gate).")
            return StopReason.ORIGIN_GATE
        if outcome == "proceed":
            logger.warning(
                "Origin gate: proceed — entering L1 against a %s origin (operator override).",
                grade,
            )
            declare_run_phase(session, RunPhase.RUNNING)
            return None

        logger.warning("Origin gate: re-scoring the origin force-fresh.")
        try:
            await _rescore_and_reemit(cycle, dataset, config, session, cb)
        except Exception:
            logger.exception(
                "Origin gate: re-score failed — staying at the gate. "
                "Fix the backend and decide again."
            )
            continue
        if origin_gate_tripped(cycle.origin_round.health, mode) is None:
            new_grade = cycle.origin_round.health.grade if cycle.origin_round.health else "unknown"
            logger.warning("Origin gate: re-scored origin is %s — entering L1.", new_grade)
            declare_run_phase(session, RunPhase.RUNNING)
            return None


async def _await_gate_decision(session: Session) -> _GateOutcome:
    """Rides the shared heartbeat: writing nothing of its own, a gated cycle reads as detached while alive."""
    ledger = session.state.ledger
    beat = (
        None
        if ledger is None
        else asyncio.create_task(
            heartbeat(
                ledger,
                call_id=f"origin_gate:{session.state.cycle_id}",
                node="origin_gate",
                round_num=0,
                start_monotonic=time.monotonic(),
            )
        )
    )
    try:
        while True:
            if session.control.pause_requested():
                return "pause"
            decided = session.control.take_gate_decision()
            if decided is not None:
                return decided
            await asyncio.sleep(_GATE_POLL_S)
    finally:
        if beat is not None:
            beat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await beat


async def _rescore_and_reemit(
    cycle: Cycle,
    dataset: list[Sample],
    config: CampaignConfig,
    session: Session,
    cb: RunCallbacks,
) -> None:
    scoring_set = sample_dataset(dataset, config.sp_budget_origin)
    origin = await rescore_parent(cycle, scoring_set, force_fresh=True)
    # Re-graded as a fresh floor, with no prior track record, as the first origin emit was.
    cycle.restamp_origin_round(origin)
    await emit_origin_round(cycle, session, cb)
