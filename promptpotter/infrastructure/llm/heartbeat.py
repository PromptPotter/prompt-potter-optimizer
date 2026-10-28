from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from promptpotter.domain.run_records import LLMCallProgressRecord
from promptpotter.shared.clock import SUSPEND_GRACE_S, sleep_measuring_suspend
from promptpotter.shared.errors import LedgerUnreadableError, ManifestUnreadableError

if TYPE_CHECKING:
    from promptpotter.domain.cycle_paths import CycleHop
    from promptpotter.infrastructure.ledger import CycleEventLog
    from promptpotter.infrastructure.llm.base import LLMClientBase

logger = logging.getLogger(__name__)


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
    inner_fn: Callable[[], CycleHop | None] | None = None,
    on_suspend: Callable[[float], None] | None = None,
) -> None:
    """Raises only its cancellation, so a beat's fault never replaces the awaited call's reply or raise; ``ledger=None`` still ticks, so a missing ledger cannot disarm ``on_suspend``."""
    while True:
        overshoot = await sleep_measuring_suspend(HEARTBEAT_INTERVAL_S)
        try:
            if on_suspend is not None and overshoot > SUSPEND_GRACE_S:
                on_suspend(overshoot)
            if ledger is not None:
                ledger.append(
                    LLMCallProgressRecord(
                        call_id=call_id,
                        node=node,
                        round=round_num,
                        elapsed_s=time.monotonic() - start_monotonic,
                        detail=_safe_read(detail_fn),
                        inner=_safe_read(inner_fn),
                    )
                )
        except Exception:
            logger.exception("heartbeat of %s (call %s) failed a beat", node, call_id)


def _safe_read[T](read: Callable[[], T | None] | None) -> T | None:
    """A beat's read can raise on another process's write; it must never fail the call."""
    if read is None:
        return None
    try:
        return read()
    except (OSError, ValueError, LedgerUnreadableError, ManifestUnreadableError) as exc:
        logger.warning("heartbeat read unavailable — %s: %s", exc.__class__.__name__, exc)
        return None
