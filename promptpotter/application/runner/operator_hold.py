"""The one wait every gate holds on: it writes nothing of its own, so it rides the shared heartbeat."""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING

from promptpotter.infrastructure.llm.heartbeat import heartbeat

if TYPE_CHECKING:
    from collections.abc import Callable

    from promptpotter.application.initialization.session import Session


_POLL_S = 1.0


async def hold_for_operator[T](
    session: Session, take: Callable[[], T | None], *, node: str, round_num: int
) -> T | None:
    """What *take* first answers, or ``None`` where a pause reached the hold before an answer did."""
    beat = asyncio.create_task(
        heartbeat(
            session.state.ledger,
            call_id=f"{node}:{session.state.cycle_id}",
            node=node,
            round_num=round_num,
            start_monotonic=time.monotonic(),
        )
    )
    try:
        while True:
            if session.control.pause_requested():
                return None
            answered = take()
            if answered is not None:
                return answered
            await asyncio.sleep(_POLL_S)
    finally:
        beat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await beat
