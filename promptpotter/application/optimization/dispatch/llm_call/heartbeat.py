"""The ONE in-flight heartbeat loop; a second is a bug. **An await that outlasts ``RUN_FRESH_S`` and writes nothing
of its own MUST heartbeat** — silence is how this package says the producer died."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from promptpotter.domain.run_records import LLMCallProgressRecord
from promptpotter.shared.clock import SUSPEND_GRACE_S, sleep_measuring_suspend

if TYPE_CHECKING:
    from promptpotter.infrastructure.ledger import CycleEventLog

logger = logging.getLogger(__name__)

__all__ = ["HEARTBEAT_INTERVAL_S", "heartbeat"]


HEARTBEAT_INTERVAL_S = 10.0
"""Seconds between in-flight progress ticks.

This is the refresh rate of the only surface that says WHY the run is quiet — the
chat's progress chip and the terminal's `still waiting` line both re-render per tick,
naming the provider the call is waiting on. 15s was chosen so short calls emitted no
tick at all; the cost of that silence turned out to be an operator reading a healthy
long call as a hang, which is the more expensive failure. The ledger pays one append
per tick — at four optimizer calls/round and ~90s average duration that is ~36
records/round, still negligible.

**`webapp/lib/format.ts::fmtGap` derives its threshold from this number** (it counts
missed heartbeats to decide a silence is real). Change one, re-read the other."""


async def heartbeat(
    ledger: CycleEventLog | None,
    *,
    call_id: str,
    node: str,
    round_num: int | None,
    start_monotonic: float,
    detail_fn: Callable[[], str | None] | None = None,
    on_suspend: Callable[[float], None] | None = None,
) -> None:
    """Append progress records while a call is open. ``ledger=None`` still ticks — that is what lets a caller
    create the task unconditionally, so a ledger guard cannot silently disarm ``on_suspend``."""
    while True:
        overshoot = await sleep_measuring_suspend(HEARTBEAT_INTERVAL_S)
        if on_suspend is not None and overshoot > SUSPEND_GRACE_S:
            on_suspend(overshoot)
        if ledger is None:
            continue
        elapsed = time.monotonic() - start_monotonic
        ledger.append(
            LLMCallProgressRecord(
                call_id=call_id,
                node=node,
                round=round_num,
                elapsed_s=elapsed,
                detail=_safe_detail(detail_fn),
            )
        )


def _safe_detail(detail_fn: Callable[[], str | None] | None) -> str | None:
    """A tick's status line, and never a reason the call it describes fails.

    A detail function reads a surface another process is writing — that is what makes it one — so
    it can raise where nothing about the await has gone wrong."""
    if detail_fn is None:
        return None
    try:
        return detail_fn()
    except Exception as exc:
        logger.warning("heartbeat detail unavailable — %s: %s", exc.__class__.__name__, exc)
        return None
