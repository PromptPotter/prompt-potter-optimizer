"""GEPA's working state between rounds: the pool scored on the Pareto set and the next parent,
banked whole on every round document so a resume or a fork re-seats the front."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.optimizer_state import OptimizerState, RoundPayload
from promptpotter.domain.strict_model import StrictModel

if TYPE_CHECKING:
    from collections.abc import Sequence

    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.application.optimizers.nodes import WorkingState
    from promptpotter.domain.results import RoundResult
    from promptpotter.infrastructure.ledger import CycleEventLog

__all__ = ["GEPA_MANIFEST", "GepaCandidate", "GepaRoundState", "GepaState", "gepa_state"]

GepaManifest = Literal["gepa"]
GEPA_MANIFEST: GepaManifest = "gepa"


class GepaCandidate(StrictModel):
    """One member of GEPA's candidate pool and its row of the score matrix: its campaign objective
    on each Pareto-set cell, by sample key."""

    individual: OptSearchPoint
    scores: dict[str, float]


class GepaRoundState(RoundPayload, manifest=GEPA_MANIFEST):
    """GEPA's payload: its candidate pool scored on the Pareto set, and the parent the next round
    mutates."""

    # Sample keys, in the order every round walks them; empty until round 1 draws the split.
    pareto_set: list[str]
    # Empty on the origin's document: round 1 seats the origin with its Pareto-set scores.
    pool: list[GepaCandidate]
    # Drawn at each round's close; ``None`` until round 1 closes, the origin being the only parent.
    parent_id: str | None
    rounds_without_advance: int


@dataclass
class GepaState:
    """Empty until round 1 draws the Pareto set and seats the origin in the pool."""

    pareto_set: list[str] = field(default_factory=list)
    pool: list[GepaCandidate] = field(default_factory=list)
    parent_id: str | None = None
    rounds_without_advance: int = 0
    # This round's children: each one's parent and that parent's objective per minibatch cell, set
    # by the proposer for the gate to read. The gate's decision record banks it; this never is.
    bars: dict[str, tuple[str, dict[str, float]]] = field(default_factory=dict)

    def snapshot(
        self,
        prompt_hashes: dict[str, str],
        *,
        pareto_set: list[str],
        pool: list[GepaCandidate],
        parent_id: str | None,
        rounds_without_advance: int,
    ) -> OptimizerState:
        return OptimizerState(
            manifest=GEPA_MANIFEST,
            prompt_hashes=prompt_hashes,
            payload=GepaRoundState(
                pareto_set=list(pareto_set),
                pool=[c.model_copy(deep=True) for c in pool],
                parent_id=parent_id,
                rounds_without_advance=rounds_without_advance,
            ),
        )

    def origin_state(self, selected: SelectedOptimizer) -> OptimizerState:
        return self.snapshot(
            selected.prompt_hashes(),
            pareto_set=self.pareto_set,
            pool=self.pool,
            parent_id=self.parent_id,
            rounds_without_advance=self.rounds_without_advance,
        )

    def replay(self, last: RoundResult) -> None:
        self._take_up(last.optimizer_state.payload_as(GepaRoundState))

    def resume(
        self, ledger: CycleEventLog | None, selected: SelectedOptimizer, *, before_round: int
    ) -> None:
        return None

    def absorb(self, round_result: RoundResult) -> None:
        self._take_up(round_result.optimizer_state.payload_as(GepaRoundState))

    def standing(self, rounds: Sequence[RoundResult]) -> tuple[int, int | None]:
        return self.rounds_without_advance, None

    def _take_up(self, payload: GepaRoundState) -> None:
        self.pareto_set = list(payload.pareto_set)
        self.pool = [c.model_copy(deep=True) for c in payload.pool]
        self.parent_id = payload.parent_id
        self.rounds_without_advance = payload.rounds_without_advance
        self.bars = {}


def gepa_state(state: WorkingState) -> GepaState:
    if not isinstance(state, GepaState):
        raise TypeError(f"a GEPA member was handed {type(state).__name__}, not GEPA's state")
    return state
