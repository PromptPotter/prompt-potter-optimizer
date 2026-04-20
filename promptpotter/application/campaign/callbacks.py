"""Run listener — directly composed persistence + display + control hooks.

Replaces the prior list-per-channel multicast: each dispatch fans out to
at most two concrete listeners (the persistence emitter and an optional
display) with direct method calls — no list iteration, no attach/bolt-on.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from promptpotter.application.optimization.phases import PhaseEvent
    from promptpotter.application.optimization.results import RoundResult

__all__ = ["RunListener"]


class RunListener:
    """Direct-dispatch listener for optimization loop events.

    ``emitter`` is the persistence hook (writes campaign artifacts).
    ``display`` is an optional UI sink that implements the same event
    methods (``on_phase``, ``on_candidate_scored``, ``on_sample_started``,
    ``on_sample_scored``, ``on_round_complete``). ``control`` is a single
    callable for checkpoint polling (CLI dashboard, notebook buttons). All
    calls are safe when a listener is absent — no ``None`` guards needed at
    the callsite.
    """

    __slots__ = ("control", "display", "emitter")

    def __init__(
        self,
        *,
        emitter: Any = None,
        display: Any = None,
        control: Callable[[str], str | None] | None = None,
    ) -> None:
        self.emitter = emitter
        self.display = display
        self.control = control

    def on_phase(self, event: PhaseEvent) -> None:
        if self.emitter is not None:
            self.emitter.on_phase(event)
        if self.display is not None:
            self.display.on_phase(event)

    def on_candidate_started(
        self,
        idx: int,
        total: int,
        changes_description: str,
        pp_override: dict | None,
    ) -> None:
        if self.emitter is not None:
            self.emitter.on_candidate_started(idx, total, changes_description, pp_override)
        if self.display is not None:
            self.display.on_candidate_started(idx, total, changes_description, pp_override)

    def on_candidate_scored(self, idx: int, total: int, scores: dict) -> None:
        if self.emitter is not None:
            self.emitter.on_candidate_scored(idx, total, scores)
        if self.display is not None:
            self.display.on_candidate_scored(idx, total, scores)

    def on_sample_started(self, ci: int, ct: int, qi: int, qt: int, query_text: str) -> None:
        if self.emitter is not None:
            self.emitter.on_sample_started(ci, ct, qi, qt, query_text)
        if self.display is not None:
            self.display.on_sample_started(ci, ct, qi, qt, query_text)

    def on_sample_scored(self, ci: int, ct: int, qi: int, qt: int, result: dict) -> None:
        if self.emitter is not None:
            self.emitter.on_sample_scored(ci, ct, qi, qt, result)
        if self.display is not None:
            self.display.on_sample_scored(ci, ct, qi, qt, result)

    def on_round_complete(self, rr: RoundResult, l1_stall_count: int) -> None:
        if self.emitter is not None:
            self.emitter.on_round_complete(rr, l1_stall_count)
        if self.display is not None:
            self.display.on_round_complete(rr, l1_stall_count)

    def on_checkpoint(self, name: str) -> str | None:
        if self.control is not None:
            return self.control(name)
        return None
