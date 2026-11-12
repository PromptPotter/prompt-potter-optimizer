"""The spend ceiling's stop is not here: ``RunControl.budget_tripped``."""

from __future__ import annotations

import asyncio
import logging
import traceback
from typing import TYPE_CHECKING

from promptpotter.domain.phases import (
    ERROR_CATEGORY_INFO,
    STOP_REASON_INFO,
    ErrorRecord,
    StopLoop,
    StopOutcome,
    StopReason,
)
from promptpotter.infrastructure.llm.telemetry import emit_error_record
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
    from promptpotter.application.initialization.session import Session
    from promptpotter.domain.campaign_config import OriginGateMode, PanelGateMode
    from promptpotter.domain.round_health import DegradationHealth
    from promptpotter.domain.scoring import GradedCell

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
    return reason, emit_error_record(exc, stop_reason=reason, traceback=tb)


def origin_gate_tripped(health: DegradationHealth, mode: OriginGateMode) -> StopReason | None:
    if mode == "off":
        return None
    if health.grade == "critical":
        return StopReason.ORIGIN_GATE
    if mode == "strict" and health.grade == "degraded":
        return StopReason.ORIGIN_GATE
    return None


def panel_gate_tripped(
    holed_rows: Sequence[GradedCell], cut: StopReason | None, mode: PanelGateMode
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


def target_tripped(cycle: Cycle, target: float | None) -> StopReason | None:
    """The ONE ceiling stop: the selection's headline level on the origin panel, read at its paired
    interval's lower bound, never a point estimate of a truncated draw. No pair, no stop."""
    standing = cycle.standing
    if target is None or standing is None:
        return None
    lift = standing.vs_origin.headline
    reached = lift is not None and lift.rate_a + lift.estimate.ci_lo >= target
    return StopReason.TARGET_HIT if reached else None


def standing_tripped(cycle: Cycle) -> StopReason | None:
    standing = cycle.standing
    assert standing is not None, "a boundary stop reads the close it follows"
    if standing.lives is not None and standing.lives.spent:
        return StopReason.LIVES_EXHAUSTED
    patience = cycle.config.optimization.convergence_patience
    if patience is not None and standing.rounds_without_advance >= patience:
        return StopReason.CONVERGED
    return None
