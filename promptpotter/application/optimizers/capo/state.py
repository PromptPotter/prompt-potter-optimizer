"""CAPO's working state between rounds: the population its selector kept. Every round document
banks it whole, so a resume or a fork re-seats the population off the round it continues from."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from promptpotter.domain.optimizer_state import CAPO_MANIFEST, CapoRoundState, OptimizerState

if TYPE_CHECKING:
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.application.optimizers.nodes import WorkingState
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.results import RoundResult
    from promptpotter.infrastructure.ledger import CycleEventLog

__all__ = ["CapoState", "capo_round_state", "capo_state"]


@dataclass
class CapoState:
    """Empty until round 1 seeds the initial population."""

    population: list[OptSearchPoint] = field(default_factory=list)
    rounds_without_advance: int = 0
    length_norm: int | None = None

    def snapshot(
        self,
        prompt_hashes: dict[str, str],
        *,
        population: list[OptSearchPoint],
        rounds_without_advance: int,
    ) -> OptimizerState:
        return OptimizerState(
            manifest=CAPO_MANIFEST,
            prompt_hashes=prompt_hashes,
            payload=CapoRoundState(
                population=[ind.model_copy(deep=True) for ind in population],
                rounds_without_advance=rounds_without_advance,
                length_norm=self.length_norm,
            ),
        )

    def origin_state(self, selected: SelectedOptimizer) -> OptimizerState:
        return self.snapshot(
            selected.prompt_hashes(),
            population=self.population,
            rounds_without_advance=self.rounds_without_advance,
        )

    def replay(self, last: RoundResult) -> None:
        self._take_up(capo_round_state(last))

    def resume(self, ledger: CycleEventLog | None, selected: SelectedOptimizer) -> None:
        return None

    def absorb(self, round_result: RoundResult) -> None:
        self._take_up(capo_round_state(round_result))

    def standing(self) -> tuple[int, int | None]:
        return self.rounds_without_advance, None

    def _take_up(self, payload: CapoRoundState) -> None:
        self.population = [ind.model_copy(deep=True) for ind in payload.population]
        self.rounds_without_advance = payload.rounds_without_advance
        self.length_norm = payload.length_norm


def capo_state(state: WorkingState) -> CapoState:
    if not isinstance(state, CapoState):
        raise TypeError(f"a CAPO member was handed {type(state).__name__}, not CAPO's state")
    return state


def capo_round_state(round_result: RoundResult) -> CapoRoundState:
    payload = round_result.optimizer_state.payload
    if not isinstance(payload, CapoRoundState):
        raise TypeError(
            f"a CAPO reader was handed {round_result.optimizer_state.manifest!r}'s optimizer state"
        )
    return payload
