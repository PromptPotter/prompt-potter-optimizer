"""What a run ends on: the stop each exception names, and the origin, panel and target gates the
round loop asks at a boundary. The spend ceiling's stop is ``RunControl.budget_tripped``."""

from __future__ import annotations

import asyncio
import logging
import traceback
from typing import TYPE_CHECKING, Any, Literal

from promptpotter.domain.phases import (
    REFUSAL_STOPS,
    STOP_REASON_INFO,
    StopLoop,
    StopOutcome,
    StopReason,
)
from promptpotter.domain.run_records import ErrorRecord
from promptpotter.infrastructure.llm.telemetry import emit_error_record, emit_round_warning
from promptpotter.shared.errors import (
    OptimizerTimeoutError,
    PayloadInvalidError,
    PromptCompositionError,
    ResumeDivergenceError,
    SendRefusedError,
    is_repairable_hole,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.application.initialization.session import Session
    from promptpotter.domain.results import DegradationHealth

logger = logging.getLogger(__name__)

OriginGateMode = Literal["strict", "critical_only", "off"]
PanelGateMode = Literal["strict", "off"]

# What ends a run on a named reason wherever it is raised — prep, init or the round loop.
RUN_STOPS = (StopLoop, SendRefusedError)
# Everything a run ENDS on instead of propagating; `SystemExit` and `GeneratorExit` still pass.
RUN_ENDS = (Exception, KeyboardInterrupt, asyncio.CancelledError)


def run_stop_reason(exc: BaseException) -> StopReason:
    """The stop a run ends on for ANY exception it can end on, wherever it was raised; one no
    case names is a crash. A classification only — ``end_run_on`` is what a run ENDS through."""
    match exc:
        case StopLoop():
            return exc.reason
        case SendRefusedError():
            return REFUSAL_STOPS[exc.category]
        # A KeyboardInterrupt is the PAUSE FLAG's stop (`scoring/search_point_scorer.py`); a
        # terminal Ctrl+C and an outer sample deadline both arrive as the cancellation.
        case KeyboardInterrupt() | asyncio.CancelledError():
            return StopReason.PAUSED
        case PromptCompositionError():
            return StopReason.RENDER_ERROR
        case ResumeDivergenceError():
            return StopReason.DIVERGED
        case PayloadInvalidError():
            return StopReason.INPUT_REFUSED
        case OptimizerTimeoutError():
            return StopReason.OPTIMIZER_TIMEOUT
        case _:
            return StopReason.CRASHED


def end_run_on(
    exc: BaseException, session: Session, *, where: str
) -> tuple[StopReason, ErrorRecord | None]:
    """The stop ``exc`` ends the run on, and what that reason's ``STOP_REASON_INFO`` row owes: an
    error record where the outcome is FAILED, a stashed traceback where the row keeps one, and
    the ``send_refused`` warning for a refused send. Called INSIDE the ``except`` — the traceback
    is read off the live exception, dead by finalize."""
    reason = run_stop_reason(exc)
    info = STOP_REASON_INFO[reason]
    if isinstance(exc, SendRefusedError):
        logger.warning("Run halted: %s", exc)
        emit_round_warning(kind="send_refused", severity="error", message=str(exc))
    elif isinstance(exc, asyncio.CancelledError):
        logger.warning(
            "Optimization cancelled %s (%s); finalizing as paused (resumable).",
            where,
            session.state.cycle_id or "no cycle",
        )
    elif isinstance(exc, KeyboardInterrupt):
        logger.warning("Optimization paused %s (%s).", where, str(exc) or "user-initiated")
    if info.outcome is not StopOutcome.FAILED:
        return reason, None
    message = str(exc) or type(exc).__name__
    tb: str | None = None
    if info.has_traceback:
        tb = session.state.crash_traceback = traceback.format_exc()
        logger.exception("Optimization ended %s: %s.", where, info.label)
    else:
        logger.warning("Optimization ended %s: %s — %s", where, info.label, message)
    return reason, emit_error_record(
        kind=type(exc).__name__, message=message, stop_reason=reason, traceback=tb
    )


def origin_gate_tripped(
    health: DegradationHealth | None, mode: OriginGateMode
) -> StopReason | None:
    """``ORIGIN_GATE`` when round 0's verdict warrants a halt — candidates would otherwise be
    measured against a broken floor."""
    if mode == "off" or health is None:
        return None
    if health.grade == "critical":
        return StopReason.ORIGIN_GATE
    # Strict also halts on a degraded floor: `degraded` carries exactly one cause
    # (`results_health.py`) — transient backend noise the gate's rescore can re-measure away.
    if mode == "strict" and health.grade == "degraded":
        return StopReason.ORIGIN_GATE
    return None


def panel_gate_tripped(
    holed_rows: Sequence[Mapping[str, Any]], mode: PanelGateMode
) -> StopReason | None:
    """``PAUSED`` when an electable candidate's panel has holes, so candidates were ranked on different
    cell sets. **Not a second under-probing guard** — ``coverage_floor`` excludes; this halts, resumably.

    ONE hole a declared bound CUT makes it ``PANEL_CUT`` instead. Both halt and both are resumable,
    but only one is plugged by resuming: the declaration that cut the cell cuts the re-run at the
    same place, so the standing advice would re-buy the round at full price on every attempt."""
    if mode == "off" or not holed_rows:
        return None
    if any(not is_repairable_hole(row) for row in holed_rows):
        return StopReason.PANEL_CUT
    return StopReason.PAUSED


def target_tripped(cycle: Cycle, target: float | None) -> StopReason | None:
    """``TARGET_HIT`` once the optimizer's declared pick read ``target`` on the round that picked it —
    never a high-water across rounds, whose readings sat different rows and outrank the pick."""
    if target is None:
        return None
    # An UNMEASURED pick never hits: its rounds failed to read the bar, not reached it.
    accuracy = cycle.selection.accuracy
    return StopReason.TARGET_HIT if accuracy is not None and accuracy >= target else None


__all__ = [
    "RUN_ENDS",
    "RUN_STOPS",
    "OriginGateMode",
    "end_run_on",
    "origin_gate_tripped",
    "panel_gate_tripped",
    "run_stop_reason",
    "target_tripped",
]
