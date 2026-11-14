from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, NamedTuple

from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.pass_kinds import PASSES
from promptpotter.domain.records.cell_beat import CellBeatRecord
from promptpotter.domain.records.run_records import LLMCallProgressRecord
from promptpotter.infrastructure.llm.telemetry import active_cycle_ledger
from promptpotter.shared.clock import SUSPEND_GRACE_S, sleep_measuring_suspend
from promptpotter.shared.errors import FileUnreadableError
from promptpotter.shared.measurement_context import measured_pass

if TYPE_CHECKING:
    from promptpotter.infrastructure.llm.openai_compat import OpenAICompatibleClient

logger = logging.getLogger(__name__)


def silence_detail(client: OpenAICompatibleClient, model: str | None, *, role: str) -> str:
    name = model or "(unnamed)"
    held = client.pushback(model) if model else None
    if held is not None and held.since is not None:
        return f"{role} {name} is held by a rate limit: {held.detail}"
    return f"{role} {name} has not answered"


HEARTBEAT_INTERVAL_S = 10.0
"""How often an open call beats. `run_state.py::RECENT_STEP_S` and `WEDGED_AFTER_S` are
counted in beats; `webapp/lib/format.ts::fmtGap` reads the served `RECENT_STEP_S`, never this."""
assert SUSPEND_GRACE_S > HEARTBEAT_INTERVAL_S, "a late tick would read as a suspend"


class CallBeat(NamedTuple):
    call_id: str
    node: str


class CellBeat(NamedTuple):
    """A scoring cell's wait, by its bank row; its arm is the pass it is measured in."""

    position: int


async def heartbeat(
    beat: CallBeat | CellBeat,
    *,
    round_num: int | None,
    start_monotonic: float,
    detail_fn: Callable[[], str | None] | None = None,
    inner_fn: Callable[[], CycleHop | None] | None = None,
    on_suspend: Callable[[float], None] | None = None,
) -> None:
    """A beat's I/O fault is logged, never raised, so it never replaces the awaited call's reply or raise; an unbound or muted scope still ticks, so a missing ledger cannot disarm ``on_suspend``."""
    ledger = active_cycle_ledger()
    while True:
        overshoot = await sleep_measuring_suspend(HEARTBEAT_INTERVAL_S)
        try:
            if on_suspend is not None and overshoot > SUSPEND_GRACE_S:
                on_suspend(overshoot)
            if ledger is not None:
                elapsed_s = time.monotonic() - start_monotonic
                detail, inner = _safe_read(detail_fn), _safe_read(inner_fn)
                match beat:
                    case CallBeat(call_id, node):
                        ledger.append(
                            LLMCallProgressRecord(
                                call_id=call_id,
                                node=node,
                                round=round_num,
                                elapsed_s=elapsed_s,
                                detail=detail,
                                inner=inner,
                            )
                        )
                    case CellBeat(position):
                        walked = measured_pass()
                        arm = (
                            None
                            if walked is None or not PASSES[walked.role].arm
                            else ArmKey(walked.individual_id)
                        )
                        ledger.append(
                            CellBeatRecord(
                                round=round_num,
                                arm=arm,
                                position=position,
                                elapsed_s=elapsed_s,
                                detail=detail,
                                inner=inner,
                            )
                        )
        except (OSError, FileUnreadableError):
            logger.exception("heartbeat %s failed a beat", beat)


def _safe_read[T](read: Callable[[], T | None] | None) -> T | None:
    """A beat's read can raise on another process's write; it must never fail the call."""
    if read is None:
        return None
    try:
        return read()
    except (OSError, ValueError, FileUnreadableError) as exc:
        logger.warning("heartbeat read unavailable — %s: %s", exc.__class__.__name__, exc)
        return None
