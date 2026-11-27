"""The spend ceiling's stop is not here: ``RunControl.budget_tripped``."""

from __future__ import annotations

import asyncio
import traceback
from collections.abc import Sequence

from promptpotter.domain.account_words import refusal_words
from promptpotter.domain.campaign_config import (
    GateMode,
    OptimizationConfig,
)
from promptpotter.domain.paired_reading import ReadingState
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


def run_stop_detail(exc: BaseException) -> str:
    """What a run stop said of itself, its ending's `detail`: a `StopLoop`'s own words, a refused
    send's sentence."""
    if isinstance(exc, SendRefusedError):
        return refusal_words(exc)
    return exc.detail if isinstance(exc, StopLoop) else ""


def end_run_on(exc: BaseException) -> tuple[StopReason, ErrorRecord | None, str]:
    """Call INSIDE the ``except``: the traceback is read off the live exception, dead by finalize.
    Answers the stop, its error record where it failed, and `run_stop_detail`."""
    reason, detail = run_stop_reason(exc), run_stop_detail(exc)
    info = STOP_REASON_INFO[reason]
    if info.outcome is not StopOutcome.FAILED:
        return reason, None, detail
    record = ErrorRecord.raised(
        exc,
        kind=type(exc).__name__,
        stop_reason=reason,
        traceback=traceback.format_exc() if info.has_traceback else None,
        round=current_round(),
    )
    append_record(record)
    return reason, record, detail


def origin_gate_tripped(health: DegradationHealth, mode: GateMode) -> StopReason | None:
    if mode == "strict" and health.grade in ("degraded", "critical"):
        return StopReason.ORIGIN_GATE
    return None


def panel_gate_tripped(
    holed_rows: Sequence[GradedCell], cut: StopLoop | None, mode: GateMode
) -> StopLoop | None:
    """A budget's cut stops in its own words; every other hole in its holed rows' category and error:
    ``BACKEND_HOLE`` where one is the backend's, the hole a re-measure may heal, else ``PANEL_HOLE``."""
    if mode == "off":
        return None
    if cut is not None:
        return cut
    facts = [c.facts for c in holed_rows]
    if not (said := sorted({": ".join(filter(None, (f.error_category, f.error))) for f in facts})):
        return None
    # The first three: every door serves it, and an L4 outage holes every cell.
    more = f"; {len(said) - 3} more" if len(said) > 3 else ""
    server = any(f.error_category is ErrorCategory.SERVER for f in facts)
    reason = StopReason.BACKEND_HOLE if server else StopReason.PANEL_HOLE
    return StopLoop(reason, detail="; ".join(said[:3]) + more)


#: The ceiling stop's one-sided anytime-valid miss rate on the pick's origin-panel level.
_CEILING_ALPHA = 0.025


def standing_tripped(standing: RunStanding, optimization: OptimizationConfig) -> StopReason | None:
    """The ONE reading of whether *standing*'s round is the last: the close's stop, the adapter's
    skip and the controller's act all read it. The ceiling stop reads the pick's own level on the
    origin panel at its one-sided anytime-valid lower bound (``_CEILING_ALPHA``), valid at
    whichever round it is looked at; no pair, no stop."""
    if standing.lives is not None and standing.lives.spent:
        return StopReason.LIVES_EXHAUSTED
    target, read = optimization.halt_at_fitness, standing.vs_origin
    if target is None or read.state is not ReadingState.READ:
        return None
    floor = anytime_level_lower(read.headline.rate_b * read.b.n, read.b.n, _CEILING_ALPHA)
    if floor is not None and floor >= target:
        return StopReason.TARGET_HIT
    return None
