"""The one in-flight heartbeat loop — shared by the optimizer call and L4.

``heartbeat`` periodically appends :class:`LLMCallProgressRecord` while a slow
awaitable is open, so the CLI display + webapp dashboard show a live elapsed
counter (and freshness stays fresh) instead of looking frozen for 30-200s.

Two callers ride this ONE loop (no duplicate heartbeat):

- ``llm_call`` (``call.py``) — the optimizer LLM call, ``detail_fn=None``.
- ``run_inner_cycle`` (``runner/inner_recursion.py``) — the L4 outer cycle awaiting
  a multi-minute inner campaign, with ``detail_fn`` reading the inner run's live
  ``dashboard.json`` so the outer chat shows ``"inner rX/Y · best Z%"``.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from promptpotter.domain.run_records import LLMCallProgressRecord

if TYPE_CHECKING:
    from promptpotter.infrastructure.ledger import CycleEventLog

__all__ = ["HEARTBEAT_INTERVAL_S", "heartbeat"]


HEARTBEAT_INTERVAL_S = 15.0
"""Seconds between in-flight progress ticks.

15s is a compromise: short enough that the operator sees a fresh
counter several times during a typical 60-120s optimizer call, long
enough that 5-15s critique calls finish without ever emitting one. The
ledger pays one append per tick - at four optimizer calls/round and
~90s average call duration that's ~24 records/round, negligible."""


async def heartbeat(
    ledger: CycleEventLog,
    *,
    call_id: str,
    node: str,
    round_num: int | None,
    start_monotonic: float,
    detail_fn: Callable[[], str | None] | None = None,
) -> None:
    """Periodically append :class:`LLMCallProgressRecord` while a call is open.

    Cancelled by the ``finally`` block in the caller when the awaited work
    returns (success, last 429 retry, or raise). The
    :class:`asyncio.CancelledError` is swallowed at the cancel site so
    cancellation looks like a clean exit.

    ``detail_fn`` (when supplied) is evaluated each tick and its result rides
    the record's ``detail`` — the L4 inner-progress line. ``None`` keeps the
    ordinary optimizer heartbeat's behavior (no detail).
    """
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL_S)
        elapsed = time.monotonic() - start_monotonic
        ledger.append(
            LLMCallProgressRecord(
                call_id=call_id,
                node=node,
                round=round_num,
                elapsed_s=elapsed,
                detail=detail_fn() if detail_fn is not None else None,
            )
        )
