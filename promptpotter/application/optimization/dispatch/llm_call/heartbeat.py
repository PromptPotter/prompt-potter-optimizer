"""The one in-flight heartbeat loop — shared by the optimizer call and L4.

Appends :class:`LLMCallProgressRecord` while a slow awaitable is open, so a call that
takes 30-200s reads as working rather than frozen. Every slow await in the package rides
this ONE loop; a second heartbeat is a bug.

**The rule: an await that outlasts ``RUN_FRESH_S`` and writes nothing of its own MUST
heartbeat.** Silence is how this package says "the producer died", so a live wait that
stays silent is claiming to be dead — and every liveness reader downstream believes it.

Because this is the one loop ticking beside every slow await, it is also the one place
that can notice the MACHINE went to sleep underneath one — hence ``on_suspend``. A
second watchdog task would be the second heartbeat this module forbids.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from promptpotter.domain.run_records import LLMCallProgressRecord
from promptpotter.shared.clock import SUSPEND_GRACE_S, sleep_measuring_suspend

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
    ledger: CycleEventLog | None,
    *,
    call_id: str,
    node: str,
    round_num: int | None,
    start_monotonic: float,
    detail_fn: Callable[[], str | None] | None = None,
    on_suspend: Callable[[float], None] | None = None,
) -> None:
    """Periodically append :class:`LLMCallProgressRecord` while a call is open.

    Cancelled by the ``finally`` block in the caller when the awaited work
    returns (success, last 429 retry, or raise). The
    :class:`asyncio.CancelledError` is swallowed at the cancel site so
    cancellation looks like a clean exit.

    ``detail_fn`` (when supplied) is evaluated each tick and its result rides
    the record's ``detail`` — the L4 inner-progress line. ``None`` keeps the
    ordinary optimizer heartbeat's behavior (no detail).

    ``on_suspend`` (when supplied) is called with the overshoot in seconds each
    time a tick's sleep runs long enough to mean the MACHINE was suspended — the
    hook a wall-clock deadline uses to give back time it never got to spend.

    ``ledger`` may be ``None``: the tick still runs, it just appends nothing. That
    is what lets a caller whose ledger is optional create this task
    unconditionally, so a guard on the ledger can never silently disarm an
    ``on_suspend`` that has nothing to do with telemetry.
    """
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
                detail=detail_fn() if detail_fn is not None else None,
            )
        )
