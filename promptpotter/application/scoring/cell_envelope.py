"""The per-cell wall-clock envelope — the ONE bound on the SUM of a measured cell's awaits, and
the give-back that keeps it measuring the cell rather than the box."""

from __future__ import annotations

import asyncio
import logging
from functools import partial
from typing import TYPE_CHECKING

from promptpotter.infrastructure.llm.rate_limit import set_throttle_stall_sink
from promptpotter.shared.errors import CellUnscoreableError

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import TracebackType

logger = logging.getLogger(__name__)

__all__ = ["CellEnvelope"]


def _noop(_seconds: float) -> None:
    return None


class CellEnvelope:
    """Async context manager bounding one cell's total wall clock. ``budget_s=None`` enters and
    exits doing nothing, so an undeclared backend costs the seam a branch rather than a code path.

    Expiry raises :class:`CellUnscoreableError` — the measurement was CUT, so there is no
    trajectory to grade and a truncated one must never reach the formula."""

    def __init__(self, budget_s: float | None, *, label: str) -> None:
        self.budget_s = budget_s
        self.label = label
        self.unworked = 0.0
        self._announced = False
        self._deadline = None if budget_s is None else asyncio.timeout(budget_s)

    @property
    def on_suspend(self) -> Callable[[float], None]:
        """The give-back the caller hands its heartbeat — the only channel that sees a machine
        sleep. Read BEFORE ``__aenter__`` so the heartbeat task outlives the timeout scope."""
        if self._deadline is None:
            return _noop
        return partial(self._give_back, cause="the machine suspended")

    def _give_back(self, seconds: float, *, cause: str) -> None:
        deadline = self._deadline
        when = None if deadline is None else deadline.when()
        if deadline is None or when is None:
            return
        try:
            deadline.reschedule(when + seconds)
        except RuntimeError:
            # The scope has not started or already exited; nothing to extend.
            return
        self.unworked += seconds
        budget = self.budget_s or 0.0
        logger.debug(
            "cell %s: +%.1fs envelope (%s); %.0fs given back of a %.0fs budget",
            self.label,
            seconds,
            cause,
            self.unworked,
            budget,
        )
        # Past its whole budget in waiting, the envelope is no longer measuring this cell. Said
        # once: the give-back is working as intended, the volume is not.
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
        if self._deadline is None:
            return self
        # Bound in the task the cell is measured in, so one cell's stall never credits a sibling's
        # envelope; the backend's own work inherits this context copy.
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
        if self._deadline is None:
            return False
        timed_out = False
        try:
            await self._deadline.__aexit__(exc_type, exc, tb)
        except TimeoutError:
            timed_out = True
        finally:
            set_throttle_stall_sink(None)
        # A cancel the timeout did not convert is someone else's — a pause, a Ctrl+C — and keeps
        # travelling even when the deadline had fired too.
        if not timed_out and exc_type is not None and issubclass(exc_type, asyncio.CancelledError):
            return False
        if timed_out or self._deadline.expired():
            # `asyncio.timeout` raises only when a CancelledError comes back up, so a chain that
            # answers cancellation with a normal return would make the guard vanish silently.
            raise CellUnscoreableError(
                f"cell {self.label} ran past its {self.budget_s:.0f}s wall-clock envelope and was "
                f"cancelled ({self.unworked:.0f}s of it already given back as time the cell was "
                "not allowed to spend)"
            )
        return False
