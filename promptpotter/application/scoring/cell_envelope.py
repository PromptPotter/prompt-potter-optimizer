from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from types import TracebackType

from promptpotter.infrastructure.llm.send_pacing import SendBudget
from promptpotter.infrastructure.run_scope import scoped
from promptpotter.shared.errors import CellHaltedError


class CellEnvelope:
    """Expiry raises ``CellHaltedError``: the cell was CUT, so no repair may re-buy a truncated trajectory."""

    def __init__(self, budget_s: float, *, attempts: int, label: str) -> None:
        self.budget_s = budget_s
        self.label = label
        self._budget = SendBudget(budget_s, attempts=attempts)
        self._deadline = self._budget.clock
        self._bound = contextlib.ExitStack()

    @property
    def unworked(self) -> float:
        return self._budget.given_back

    @property
    def on_suspend(self) -> Callable[[float], None]:
        """Read BEFORE ``__aenter__`` so the heartbeat task outlives the timeout scope; what it
        gives back is the cell's `unworked_s`."""
        return self._budget.give_back

    async def __aenter__(self) -> CellEnvelope:
        # Bound in the cell's own task, so its resends and stall never touch a sibling's.
        self._bound.enter_context(
            scoped(
                send_budget=self._budget,
                stall_sink=self._budget.give_back,
            )
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
        timed_out = False
        try:
            await self._deadline.__aexit__(exc_type, exc, tb)
        except TimeoutError:
            timed_out = True
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
