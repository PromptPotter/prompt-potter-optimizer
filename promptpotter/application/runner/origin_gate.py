"""The round-0 HITL checkpoint: block at ``run_phase: gate`` rather than enter L1 against a broken
floor. ``rescore`` re-scores force-fresh, which is what makes fix-rescore-watch a loop with no re-mint."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import queue
import sys
import threading
import time
from typing import TYPE_CHECKING, Literal

from promptpotter.application.optimization.dispatch.llm_call.heartbeat import heartbeat
from promptpotter.application.run_phase_control import declare_run_phase, pause_requested
from promptpotter.application.runner.round import emit_origin_round
from promptpotter.application.runner.termination import OriginGateMode, origin_gate_tripped
from promptpotter.domain.phases import RunPhase, StopReason
from promptpotter.infrastructure.store.io import read_json_tolerant
from promptpotter.infrastructure.store.layout import CycleLayout

if TYPE_CHECKING:
    from promptpotter.application.config import CampaignConfig
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.application.run_observers import RunCallbacks
    from promptpotter.domain.sample import Sample

logger = logging.getLogger(__name__)

__all__ = ["run_origin_gate"]

# Poll cadence while blocked at the gate. Brisker than the pause poll (2 s): the
# operator is actively watching a gate modal/prompt, so the decision should land
# within ~1 s.
_GATE_POLL_S = 1.0

_DECISIONS = ("rescore", "proceed", "abort")

GateDecision = Literal["rescore", "proceed", "abort"]
_GateOutcome = Literal["rescore", "proceed", "abort", "pause"]


async def run_origin_gate(
    cycle: Cycle,
    dataset: list[Sample],
    config: CampaignConfig,
    session: Session,
    cb: RunCallbacks,
    mode: OriginGateMode,
) -> StopReason | None:
    """Block until the operator decides: ``None`` to proceed into L1, else a ``StopReason``. Re-entrant —
    a ``rescore`` re-emits round 0 and loops, so a still-unhealthy verdict simply waits again."""
    stdin_q = _spawn_stdin_reader()
    while True:
        grade = cycle.origin_round.health.grade if cycle.origin_round.health else "unknown"
        logger.warning(
            "Origin gate (%s): round-0 verdict is %s — holding before L1. Decide via "
            "the webapp modal, the CLI prompt, or the origin-gate-decision command.",
            mode,
            grade,
        )
        if stdin_q is not None:
            # Operator-facing gate prompt; the gate state is also on disk
            # (dashboard run_phase=gate + round_0000.json health). ASCII-only:
            # this prints to the launching console, whose encoding may be cp1252
            # (Windows) — a non-ASCII glyph (e.g. an emoji) raises
            # UnicodeEncodeError there and crashes the whole run.
            print(
                f"\n  [ORIGIN GATE: {grade}] type r=rescore / p=proceed / a=abort "
                "then Enter (or use the webapp):",
                flush=True,
            )
        declare_run_phase(session, RunPhase.GATE)

        outcome = await _await_gate_decision(session, stdin_q)
        if outcome == "pause":
            declare_run_phase(session, RunPhase.PAUSED)
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

        # rescore: re-measure the origin live, re-grade, re-evaluate the gate.
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
        # Still not healthy — loop and wait for the next decision.


async def _await_gate_decision(
    session: Session, stdin_q: queue.Queue[GateDecision] | None
) -> _GateOutcome:
    """Block until a decision lands on the flag file or stdin; a pause always wins. Rides the ONE shared
    heartbeat — writing nothing of its own, the cycle went DETACHED, then reaped, while alive and polling."""
    decision_path = _decision_path(session)
    decision_path.unlink(missing_ok=True)
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
            if pause_requested(session):
                return "pause"
            from_file = _read_decision_file(decision_path)
            if from_file is not None:
                decision_path.unlink(missing_ok=True)
                return from_file
            if stdin_q is not None:
                try:
                    return stdin_q.get_nowait()
                except queue.Empty:
                    pass
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
    """Re-score the origin force-fresh, then re-emit round 0 through the standard ``close_round`` seam so
    every round-0 surface updates in one shape."""
    from promptpotter.application.datasets.loaders import sample_dataset
    from promptpotter.application.origin import rescore_parent

    scoring_set = sample_dataset(dataset, config.origin_budget())
    origin = await rescore_parent(cycle, scoring_set, 0, callbacks=cb, force_fresh=True)
    # A fresh round replaces round 0 outright, so it is re-graded as a fresh floor
    # (no prior track record) exactly as the first origin emit was.
    cycle.restamp_origin_round(origin)
    await emit_origin_round(cycle, session, cb)


def _decision_path(session: Session):  # type: ignore[no-untyped-def]
    cycle_dir = session.store.campaigns.cycle_dir(session.hop)
    return CycleLayout(cycle_dir).gate_decision


def _read_decision_file(path) -> GateDecision | None:  # type: ignore[no-untyped-def]
    data = read_json_tolerant(path)
    decision = data.get("decision") if isinstance(data, dict) else None
    return decision if decision in _DECISIONS else None


def _spawn_stdin_reader() -> queue.Queue[GateDecision] | None:
    """Read the gate decision off an attached TTY on a daemon thread, or ``None`` when stdin is not one —
    those surfaces drive the gate through the command channel, and a blocked ``input()`` never hangs exit."""
    if not (sys.stdin is not None and sys.stdin.isatty()):
        return None
    q: queue.Queue[GateDecision] = queue.Queue()

    def _read() -> None:
        mapping = {
            "r": "rescore",
            "rescore": "rescore",
            "p": "proceed",
            "proceed": "proceed",
            "a": "abort",
            "abort": "abort",
        }
        try:
            for line in sys.stdin:
                choice = mapping.get(line.strip().lower())
                if choice is not None:
                    q.put(choice)  # type: ignore[arg-type]
                    return
        except (OSError, ValueError):
            return

    threading.Thread(target=_read, daemon=True, name="origin-gate-stdin").start()
    return q
