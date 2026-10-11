from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from promptpotter.domain.run_records import LLMCallProgressRecord
from promptpotter.shared.clock import SUSPEND_GRACE_S, sleep_measuring_suspend

if TYPE_CHECKING:
    from promptpotter.infrastructure.ledger import CycleEventLog
    from promptpotter.infrastructure.llm.base import LLMClientBase

logger = logging.getLogger(__name__)

__all__ = ["HEARTBEAT_INTERVAL_S", "heartbeat", "waiting_on"]


def waiting_on(client: LLMClientBase, model: str | None, *, role: str) -> str:
    name = model or "(unnamed)"
    held = client.pushback(model) if model else None
    if held is not None and held.since is not None:
        return f"{role} {name} is held by a rate limit: {held.detail}"
    return f"{role} {name} has not answered"


HEARTBEAT_INTERVAL_S = 10.0
"""`webapp/lib/format.ts::fmtGap` derives its threshold from this: change one, re-read the other."""


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
    """``ledger=None`` still ticks, so a missing ledger cannot disarm ``on_suspend``."""
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
    """A detail read can raise on another process's write; it must never fail the call."""
    if detail_fn is None:
        return None
    try:
        return detail_fn()
    except Exception as exc:
        logger.warning("heartbeat detail unavailable — %s: %s", exc.__class__.__name__, exc)
        return None
