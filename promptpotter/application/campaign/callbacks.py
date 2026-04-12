"""Multicast progress callbacks for the optimization loop.

Each channel is a list of listeners; ``merge()`` concatenates two callback
bundles.  Dispatch methods are always safe to call — no ``None`` guards
needed at call sites.  ``on_checkpoint`` short-circuits on the first
non-``None`` result.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from promptpotter.application.optimization.phases import PhaseEvent
    from promptpotter.application.optimization.results import RoundResult

__all__ = ["RunCallbacks"]


class RunCallbacks:
    """Multicast progress callbacks for the feedback cycle."""

    def __init__(
        self,
        *,
        on_round_complete: Callable[[RoundResult, int], None] | None = None,
        on_candidate_scored: Callable[[int, int, dict], None] | None = None,
        on_sample_scored: Callable[[int, int, int, int, dict], None] | None = None,
        on_phase: Callable[[PhaseEvent], None] | None = None,
        on_checkpoint: Callable[[str], str | None] | None = None,
    ) -> None:
        def _wrap(fn: Any) -> list:
            return [fn] if fn else []

        self._round = _wrap(on_round_complete)
        self._candidate = _wrap(on_candidate_scored)
        self._sample = _wrap(on_sample_scored)
        self._phase = _wrap(on_phase)
        self._checkpoint = _wrap(on_checkpoint)

    def merge(self, other: RunCallbacks) -> RunCallbacks:
        """Return a new RunCallbacks with self's listeners first, then other's."""
        merged = RunCallbacks()
        merged._round = self._round + other._round
        merged._candidate = self._candidate + other._candidate
        merged._sample = self._sample + other._sample
        merged._phase = self._phase + other._phase
        merged._checkpoint = self._checkpoint + other._checkpoint
        return merged

    def on_round_complete(self, rr: RoundResult, stall_count: int) -> None:
        for fn in self._round:
            fn(rr, stall_count)

    def on_candidate_scored(self, idx: int, total: int, scores: dict) -> None:
        for fn in self._candidate:
            fn(idx, total, scores)

    def on_sample_scored(self, ci: int, ct: int, qi: int, qt: int, result: dict) -> None:
        for fn in self._sample:
            fn(ci, ct, qi, qt, result)

    def on_phase(self, event: PhaseEvent) -> None:
        for fn in self._phase:
            fn(event)

    def on_checkpoint(self, name: str) -> str | None:
        for fn in self._checkpoint:
            result = fn(name)
            if result:
                return result
        return None
