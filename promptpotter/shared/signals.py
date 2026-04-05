"""Graceful 2-phase interrupt handler for async batch operations.

Leaf module — no domain model or service dependencies.
"""

from __future__ import annotations

import asyncio
import signal
from contextlib import contextmanager
from dataclasses import dataclass


@dataclass
class InterruptState:
    """Mutable flag container for graceful interrupt tracking."""

    stop_requested: bool = False


@contextmanager
def graceful_interrupt():
    """Context manager: 1st Ctrl+C sets flag, 2nd cancels current task.

    Yields an ``InterruptState`` whose ``.stop_requested`` is set on the
    first SIGINT.  A second SIGINT calls ``Task.cancel()`` on the current
    asyncio task (safe on Windows — no IOCP interaction).
    """
    state = InterruptState()
    batch_task: asyncio.Task | None = asyncio.current_task()

    def _handler(_signum: int, _frame: object) -> None:
        if state.stop_requested:
            if batch_task is not None and not batch_task.done():
                batch_task.cancel()
            return
        state.stop_requested = True

    old_handler = signal.signal(signal.SIGINT, _handler)
    try:
        yield state
    finally:
        signal.signal(signal.SIGINT, old_handler)
