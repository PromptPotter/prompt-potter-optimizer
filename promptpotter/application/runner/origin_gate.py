"""Origin gate — the round-0 HITL checkpoint.

When round 0's verdict is too unhealthy to optimize against, the loop blocks here at
``run_phase: gate`` rather than entering L1 against a broken floor — the usual case while
a developer brings up a connector whose backend is still buggy. ``rescore`` re-scores
force-fresh so a code fix is actually reflected, which is what makes fix-rescore-watch an
iterate loop with no re-mint. One decision channel for every surface,
``.runtime/gate_decision.json``, polled here; a pause always wins and leaves the gate
resumable. The wait is unbounded, so it heartbeats — see ``dispatch/llm_call/heartbeat``.
"""

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
    from promptpotter.application.bootstrap.session import Session
    from promptpotter.application.config import CampaignConfig
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
    """Block at the round-0 origin gate until the operator decides.

    Returns ``None`` to proceed into L1 (operator chose ``proceed``, or a
    ``rescore`` produced a now-healthy origin) or a ``StopReason`` to halt
    (``ORIGIN_GATE`` on ``abort``, ``PAUSED`` if a pause was requested while
    waiting). Re-entrant: a ``rescore`` re-scores the origin force-fresh, re-emits
    round 0, and loops — a still-unhealthy verdict waits again, a healthy one
    proceeds. The caller invokes this only after ``origin_gate_tripped`` already
    returned a stop for the freshly-emitted round 0."""
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
    """Block until a decision lands on the flag file or stdin. A pause always
    wins. A stale decision from a prior gate entry is cleared first.

    **Heartbeated, and it is the whole reason this wait is safe.** This is the
    longest await in the package — unbounded, since it ends only when a human
    decides — and it writes nothing of its own. Without a heartbeat the cycle went
    silent the moment it declared ``gate``, and three things followed, none of them
    raising: after ``RUN_FRESH_S`` (30 s) ``derive_run_phase`` read DETACHED, so the
    cycle **dropped out of the operator's dock** while waiting on that same
    operator; after ``DEAD_AFTER_S`` (15 min) the liveness reaper stamped it
    TERMINAL (``producer_vanished``) though the process was alive and still
    polling; and a decision arriving afterwards resumed a run into a cycle already
    marked finished.

    So the gate rides the ONE shared heartbeat (``dispatch/llm_call/heartbeat.py``)
    as its fourth caller, never a second loop of its own. The record is a pure
    freshness tick — ``LiveDashboardView._handle_llm_call_progress`` mutates no
    state, it only re-persists — so a held gate looks alive because it *is*.
    """
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
    """Re-score the origin force-fresh over the same-sized sample set the origin
    used, then re-emit round 0 through the standard ``close_round`` seam so the
    fresh verdict and every round-0 surface (``round_0000.json``,
    ``dashboard.json::rounds[0]``) update in one shape."""
    from promptpotter.application.datasets.loaders import sample_dataset
    from promptpotter.application.origin import rescore_parent

    scoring_set = sample_dataset(dataset, config.origin_budget())
    origin = await rescore_parent(cycle, scoring_set, 0, callbacks=cb, force_fresh=True)
    # A fresh round replaces round 0 outright, so it is re-graded as a fresh floor
    # (no prior track record) exactly as the first origin emit was.
    cycle.restamp_origin_round(origin)
    await emit_origin_round(cycle, session, cb)


def _decision_path(session: Session):  # type: ignore[no-untyped-def]
    cycle_dir = session.store.campaigns.cycle_dir(session.campaign_id, session.state.cycle_id)
    return CycleLayout(cycle_dir).gate_decision


def _read_decision_file(path) -> GateDecision | None:  # type: ignore[no-untyped-def]
    """Read a decision from the flag file; ``None`` if absent or malformed."""
    data = read_json_tolerant(path)
    decision = data.get("decision") if isinstance(data, dict) else None
    return decision if decision in _DECISIONS else None


def _spawn_stdin_reader() -> queue.Queue[GateDecision] | None:
    """Start a daemon thread reading the gate decision from an attached TTY.

    Returns a queue the poll loop drains non-blockingly, or ``None`` when stdin
    is not a TTY (webapp-launched background run, notebook, redirected input) —
    those surfaces drive the gate through the command channel instead. The thread
    is a daemon so a still-blocked ``input()`` never hangs process exit when the
    decision arrived from another surface."""
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
