from __future__ import annotations

import asyncio
import contextlib
import logging
from functools import partial
from typing import TYPE_CHECKING

from promptpotter.infrastructure.llm.send_pacing import (
    SendBudget,
    set_throttle_stall_sink,
    under_budget,
)
from promptpotter.shared.errors import CellHaltedError

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import TracebackType

logger = logging.getLogger(__name__)

__all__ = ["CellEnvelope"]


def _noop(_seconds: float) -> None:
    return None


class CellEnvelope:
    """Expiry raises ``CellHaltedError``: the cell was CUT, so no repair may re-buy a truncated trajectory."""

    def __init__(self, budget_s: float | None, *, attempts: int, label: str) -> None:
        self.budget_s = budget_s
        self.label = label
        self._budget = SendBudget(budget_s, attempts=attempts)
        self._announced = False
        self._deadline = self._budget.clock
        self._bound = contextlib.ExitStack()

    @property
    def unworked(self) -> float:
        return self._budget.given_back

    @property
    def on_suspend(self) -> Callable[[float], None]:
        """Read BEFORE ``__aenter__`` so the heartbeat task outlives the timeout scope."""
        if self._deadline is None:
            return _noop
        return partial(self._give_back, cause="the machine suspended")

    def _give_back(self, seconds: float, *, cause: str) -> None:
        before = self.unworked
        self._budget.give_back(seconds)
        if self.unworked == before:
            return
        budget = self.budget_s or 0.0
        logger.debug(
            "cell %s: +%.1fs envelope (%s); %.0fs given back of a %.0fs budget",
            self.label,
            seconds,
            cause,
            self.unworked,
            budget,
        )
        if not self._announced and self.unworked > budget:
            self._announced = True
            logger.warning(
                "cell %s has now spent longer waiting (%.0fs) than its entire %.0fs wall-clock "
                "envelope — the box is oversubscribed, not the cell slow",
                self.label,
                self.unworked,
                budget,
            )

    async def __aenter__(self) -> CellEnvelope:
        # Bound in the cell's own task, so its resends and stall never touch a sibling's.
        self._bound.enter_context(under_budget(self._budget))
        if self._deadline is None:
            return self
        set_throttle_stall_sink(
            partial(self._give_back, cause="queued behind the shared rate limiter")
        )
        await self._deadline.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        self._bound.close()
        if self._deadline is None:
            return False
        timed_out = False
        try:
            await self._deadline.__aexit__(exc_type, exc, tb)
        except TimeoutError:
            timed_out = True
        finally:
            set_throttle_stall_sink(None)
        # A cancel the timeout did not convert is someone else's (a pause, a Ctrl+C) and keeps travelling.
        if not timed_out and exc_type is not None and issubclass(exc_type, asyncio.CancelledError):
            return False
        # `expired()` too: `asyncio.timeout` raises only when a CancelledError comes back up.
        if timed_out or self._deadline.expired():
            raise CellHaltedError(
                f"cell {self.label} ran past its {self.budget_s:.0f}s wall-clock envelope and was "
                f"cancelled ({self.unworked:.0f}s of it already given back as time the cell was "
                "not allowed to spend)",
                # A cancelled call hands back no bill: an agent spending outside our client is lost.
                spent={},
            )
        return False
