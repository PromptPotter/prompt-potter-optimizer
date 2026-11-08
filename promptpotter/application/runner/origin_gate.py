"""``rescore`` is one force-fresh walk of the origin, which makes fix-rescore-watch a loop with no re-mint."""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, get_args

from promptpotter.application.bench.node_context import rewalk_origin
from promptpotter.application.bench.resume_and_fork.decisions import record_decision
from promptpotter.application.optimizers.nodes import RoundContext
from promptpotter.application.run_observers import declare_gate, declare_running
from promptpotter.application.runner.operator_hold import hold_for_operator
from promptpotter.application.runner.round import emit_origin_round
from promptpotter.application.runner.termination import origin_gate_tripped
from promptpotter.domain.phases import STOP_REASON_INFO, GateDecision, StopReason
from promptpotter.domain.run_records import BenchCheckpointKind

if TYPE_CHECKING:
    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.run_callbacks import RunCallbacks
    from promptpotter.domain.campaign_config import OriginGateMode
    from promptpotter.domain.sample import Sample

logger = logging.getLogger(__name__)


_DECISIONS: tuple[str, ...] = get_args(GateDecision)


def pass_origin_gate(cb: RunCallbacks, *, by: str) -> None:
    """The operator's override, a decision of round 0: every later launch of the cycle reads it."""
    record_decision(
        cb.ledger,
        BenchCheckpointKind.ORIGIN_GATE_PASS,
        {},
        "proceed",
        node=None,
        data={"by": by},
        round=0,
    )


def origin_gate_passed(session: Session) -> bool:
    standing = session.store.campaigns.standing_rounds(session.hop, moment=None)
    return any(
        d.kind == BenchCheckpointKind.ORIGIN_GATE_PASS for d in standing.decisions.get(0, ())
    )


async def run_origin_gate(
    cycle: Cycle,
    dataset: list[Sample],
    session: Session,
    cb: RunCallbacks,
    mode: OriginGateMode,
) -> StopReason | None:
    """``None`` proceeds into L1; a ``rescore`` re-emits round 0 and a still-unhealthy verdict waits again.
    An origin that scored no cell has nothing to gate: it halts, and its resume re-measures it
    (`repair.py::rewalk_lost_origin`), passing it on that resume if it is still unhealthy; an
    edited origin is a new campaign or an operator-steered fork at round 0
    (`OriginSource.FORK_SEED`)."""
    while True:
        grade = cycle.origin_round.health.grade
        if cycle.origin_round.health.scored_cells == 0:
            logger.error(
                "Origin gate: round 0 scored no cell (%s) — halted. %s",
                cycle.origin_round.health.last_error or "no error recorded",
                STOP_REASON_INFO[StopReason.ORIGIN_GATE].next_step,
            )
            return StopReason.ORIGIN_GATE
        logger.warning(
            "Origin gate (%s): round-0 verdict is %s — holding before L1. Decide via "
            "the webapp modal, the TTY prompt, or an `origin-gate` command (%s).",
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
        declare_gate(cb.ledger, "origin")

        outcome = await hold_for_operator(
            session, session.state.control.take_gate_decision, node="origin_gate", round_num=0
        )
        if outcome is None:
            return StopReason.PAUSED
        if outcome == "abort":
            logger.warning("Origin gate: abort — ending cycle (origin_gate).")
            return StopReason.ORIGIN_GATE
        if outcome == "proceed":
            logger.warning(
                "Origin gate: proceed — entering L1 against a %s origin (operator override).",
                grade,
            )
            pass_origin_gate(cb, by="proceed")
            declare_running(cb.ledger)
            return None

        logger.warning("Origin gate: re-scoring the origin force-fresh.")
        try:
            await _rescore_and_reemit(cycle, dataset, session, cb)
        except Exception:
            logger.exception(
                "Origin gate: re-score failed — staying at the gate. "
                "Fix the backend and decide again."
            )
            continue
        if origin_gate_tripped(cycle.origin_round.health, mode) is None:
            new_grade = cycle.origin_round.health.grade
            logger.warning("Origin gate: re-scored origin is %s — entering L1.", new_grade)
            declare_running(cb.ledger)
            return None


async def _rescore_and_reemit(
    cycle: Cycle, dataset: list[Sample], session: Session, cb: RunCallbacks
) -> None:
    ctx = RoundContext(cycle=cycle, round_num=0, callbacks=cb)
    await rewalk_origin(ctx, cycle.origin_cells(dataset), force_fresh=True)
    await emit_origin_round(cycle, session, cb)
