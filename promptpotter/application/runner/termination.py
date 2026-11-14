"""The spend ceiling's stop is not here: ``RunControl.budget_tripped``."""

from __future__ import annotations

import asyncio
import logging
import traceback
from collections.abc import Sequence
from typing import TYPE_CHECKING

from promptpotter.domain.campaign_config import (
    GateMode,
    OptimizationConfig,
)
from promptpotter.domain.crowning_bar import BAR_ADMISSION
from promptpotter.domain.phases import (
    ERROR_CATEGORY_INFO,
    STOP_REASON_INFO,
    StopOutcome,
    StopReason,
)
from promptpotter.domain.records.error import ErrorRecord
from promptpotter.domain.records.run_records import StopLoop
from promptpotter.domain.round_health import DegradationHealth
from promptpotter.domain.run_standing import RunStanding
from promptpotter.domain.scoring import GradedCell
from promptpotter.infrastructure.llm.telemetry import append_record, current_round
from promptpotter.shared.errors import (
    ErrorCategory,
    OptimizerTimeoutError,
    PayloadInvalidError,
    PromptCompositionError,
    ResumeDivergenceError,
    SendRefusedError,
)
from promptpotter.shared.statistics import anytime_level_lower

if TYPE_CHECKING:
    from promptpotter.application.initialization.session import Session

logger = logging.getLogger(__name__)

RUN_STOPS = (StopLoop, SendRefusedError)
# `SystemExit` and `GeneratorExit` still pass.
RUN_ENDS = (Exception, KeyboardInterrupt, asyncio.CancelledError)


def run_stop_reason(exc: BaseException) -> StopReason:
    match exc:
        case StopLoop():
            return exc.reason
        case SendRefusedError() if (stop := ERROR_CATEGORY_INFO[exc.category].stops) is not None:
            return stop
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
    elif isinstance(exc, asyncio.CancelledError):
        logger.warning(
            "Optimization cancelled %s (%s); finalizing as paused (resumable).",
            where,
            session.cycle_id,
        )
    elif isinstance(exc, KeyboardInterrupt):
        logger.warning("Optimization paused %s (%s).", where, str(exc) or "user-initiated")
    if info.outcome is not StopOutcome.FAILED:
        return reason, None
    tb: str | None = None
    if info.has_traceback:
        tb = traceback.format_exc()
        logger.exception("Optimization ended %s: %s.", where, info.label)
    else:
        logger.warning("Optimization ended %s: %s — %s", where, info.label, exc)
    record = ErrorRecord.raised(
        exc,
        kind=type(exc).__name__,
        stop_reason=reason,
        traceback=tb,
        round=current_round(),
    )
    append_record(record)
    return reason, record


def origin_gate_tripped(health: DegradationHealth, mode: GateMode) -> StopReason | None:
    if mode == "strict" and health.grade in ("degraded", "critical"):
        return StopReason.ORIGIN_GATE
    return None


def panel_gate_tripped(
    holed_rows: Sequence[GradedCell], cut: StopReason | None, mode: GateMode
) -> StopReason | None:
    """The stop names the bound that made the hole: a budget's own, or ``PANEL_CUT``; a resume re-cuts both, unlike ``PAUSED``."""
    if mode == "off":
        return None
    if cut is not None:
        return cut
    if not holed_rows:
        return None
    if any(cell.facts.error_category is ErrorCategory.HALTED for cell in holed_rows):
        return StopReason.PANEL_CUT
    return StopReason.PAUSED


def standing_tripped(standing: RunStanding, optimization: OptimizationConfig) -> StopReason | None:
    """The ONE reading of whether *standing*'s round is the last: the close's stop, the adapter's
    skip and the controller's act all read it. The ceiling stop reads the pick's own level on the
    origin panel at its one-sided anytime-valid lower bound (``BAR_ADMISSION``), valid at whichever
    round it is looked at; no pair, no stop."""
    if standing.lives is not None and standing.lives.spent:
        return StopReason.LIVES_EXHAUSTED
    target, read = optimization.halt_at_fitness, standing.vs_origin
    if target is None or read.headline is None or read.b is None:
        return None
    floor = anytime_level_lower(read.headline.rate_b * read.b.n, read.b.n, 1.0 - BAR_ADMISSION)
    if floor is not None and floor >= target:
        return StopReason.TARGET_HIT
    return None
