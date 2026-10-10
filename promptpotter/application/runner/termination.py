"""The spend ceiling's stop is not here: ``RunControl.budget_tripped``."""

from __future__ import annotations

import asyncio
import logging
import traceback
from typing import TYPE_CHECKING

from promptpotter.domain.phases import (
    REFUSAL_STOPS,
    STOP_REASON_INFO,
    StopLoop,
    StopOutcome,
    StopReason,
)
from promptpotter.domain.results import RoundAdvance
from promptpotter.domain.run_records import ErrorRecord
from promptpotter.infrastructure.llm.telemetry import emit_error_record, emit_round_warning
from promptpotter.shared.errors import (
    ErrorCategory,
    OptimizerTimeoutError,
    PayloadInvalidError,
    PromptCompositionError,
    ResumeDivergenceError,
    SendRefusedError,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.application.campaign_config import OriginGateMode, PanelGateMode
    from promptpotter.application.initialization.session import Session
    from promptpotter.domain.results import DegradationHealth, RunStanding
    from promptpotter.domain.scoring import GradedCell

logger = logging.getLogger(__name__)

RUN_STOPS = (StopLoop, SendRefusedError)
# `SystemExit` and `GeneratorExit` still pass.
RUN_ENDS = (Exception, KeyboardInterrupt, asyncio.CancelledError)


def run_stop_reason(exc: BaseException) -> StopReason:
    match exc:
        case StopLoop():
            return exc.reason
        case SendRefusedError():
            return REFUSAL_STOPS[exc.category]
        # A KeyboardInterrupt is the PAUSE FLAG's stop; an outer sample deadline cancels the same way.
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
    """Call INSIDE the ``except``: the traceback is read off the live exception, dead by finalize."""
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
        tb = traceback.format_exc()
        logger.exception("Optimization ended %s: %s.", where, info.label)
    else:
        logger.warning("Optimization ended %s: %s — %s", where, info.label, message)
    return reason, emit_error_record(
        kind=type(exc).__name__, message=message, stop_reason=reason, traceback=tb
    )


def origin_gate_tripped(
    health: DegradationHealth | None, mode: OriginGateMode
) -> StopReason | None:
    if mode == "off" or health is None:
        return None
    if health.grade == "critical":
        return StopReason.ORIGIN_GATE
    if mode == "strict" and health.grade == "degraded":
        return StopReason.ORIGIN_GATE
    return None


def panel_gate_tripped(holed_rows: Sequence[GradedCell], mode: PanelGateMode) -> StopReason | None:
    """``PANEL_CUT`` on ONE hole a declared bound cut: resuming re-cuts at the same place, unlike ``PAUSED``."""
    if mode == "off" or not holed_rows:
        return None
    if any(cell.facts.error_category is ErrorCategory.HALTED for cell in holed_rows):
        return StopReason.PANEL_CUT
    return StopReason.PAUSED


def target_tripped(cycle: Cycle, target: float | None) -> StopReason | None:
    """The declared pick's own reading, never a high-water across rounds: those sat different rows."""
    if target is None:
        return None
    accuracy = cycle.selection.accuracy
    return StopReason.TARGET_HIT if accuracy is not None and accuracy >= target else None


def standing_tripped(cycle: Cycle, standing: RunStanding) -> StopReason | None:
    # The OBJECTIVE, not accuracy: where it prices tokens, 100% correct at 3x the tokens is no ceiling.
    objective = cycle.tracking.current_composite_fitness
    if (
        cycle.rounds[-1].overlap.advance is RoundAdvance.ADVANCED
        and objective is not None
        and objective >= 1.0
    ):
        return StopReason.PERFECT
    if standing.stalls_left == 0:
        return StopReason.LIVES_EXHAUSTED
    patience = cycle.config.optimization.convergence_patience
    if patience is not None and standing.rounds_without_advance >= patience:
        return StopReason.CONVERGED
    return None


__all__ = [
    "RUN_ENDS",
    "RUN_STOPS",
    "end_run_on",
    "origin_gate_tripped",
    "panel_gate_tripped",
    "run_stop_reason",
    "standing_tripped",
    "target_tripped",
]
