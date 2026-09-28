"""LEVI's working state between rounds: its archive and what calibration fixed for it. Every round
document banks it whole, so a resume or a fork re-seats the archive off the round it continues from."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.optimizer_state import OptimizerState, RoundPayload
from promptpotter.domain.strict_model import StrictModel

if TYPE_CHECKING:
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.application.optimizers.nodes import WorkingState
    from promptpotter.domain.results import RoundResult
    from promptpotter.infrastructure.ledger import CycleEventLog

__all__ = [
    "LEVI_MANIFEST",
    "DescriptorStats",
    "LeviCalibration",
    "LeviElite",
    "LeviRoundState",
    "LeviState",
    "levi_state",
]

LeviManifest = Literal["levi"]
LEVI_MANIFEST: LeviManifest = "levi"


class DescriptorStats(StrictModel):
    """Welford's running count, mean and squared-deviation sum per descriptor dimension."""

    count: int
    mean: list[float]
    m2: list[float]


class LeviCalibration(StrictModel):
    """What LEVI's calibration round fixes for the run: the proxy and the archive's Voronoi cells."""

    # Sample keys, in the order every later round walks them.
    proxy: list[str]
    centroids: list[list[float]]
    stats: DescriptorStats


class LeviElite(StrictModel):
    """One occupied cell of LEVI's archive: the best individual mapped to it."""

    cell: int
    # The mean per-cell objective over the proxy: LEVI's f, which the cell keeps the best of.
    score: float
    # The round whose rows measured it, which is where its feedback is read back from.
    round: int
    individual: OptSearchPoint


class LeviRoundState(RoundPayload, manifest=LEVI_MANIFEST):
    """LEVI's payload: its CVT-MAP-Elites archive and what calibration fixed for it."""

    # ``None`` on the origin's document: round 1 is the calibration round that sets it.
    calibration: LeviCalibration | None
    elites: list[LeviElite]
    rounds_without_advance: int


@dataclass
class LeviState:
    """Empty until round 1, the calibration round, seeds the archive."""

    calibration: LeviCalibration | None = None
    elites: list[LeviElite] = field(default_factory=list)
    rounds_without_advance: int = 0

    def snapshot(
        self,
        prompt_hashes: dict[str, str],
        *,
        calibration: LeviCalibration | None,
        elites: list[LeviElite],
        rounds_without_advance: int,
    ) -> OptimizerState:
        return OptimizerState(
            manifest=LEVI_MANIFEST,
            prompt_hashes=prompt_hashes,
            payload=LeviRoundState(
                calibration=calibration.model_copy(deep=True) if calibration else None,
                elites=[e.model_copy(deep=True) for e in elites],
                rounds_without_advance=rounds_without_advance,
            ),
        )

    def origin_state(self, selected: SelectedOptimizer) -> OptimizerState:
        return self.snapshot(
            selected.prompt_hashes(),
            calibration=self.calibration,
            elites=self.elites,
            rounds_without_advance=self.rounds_without_advance,
        )

    def replay(self, last: RoundResult) -> None:
        self._take_up(last.optimizer_state.payload_as(LeviRoundState))

    def resume(self, ledger: CycleEventLog | None, selected: SelectedOptimizer) -> None:
        return None

    def absorb(self, round_result: RoundResult) -> None:
        self._take_up(round_result.optimizer_state.payload_as(LeviRoundState))

    def standing(self) -> tuple[int, int | None]:
        return self.rounds_without_advance, None

    def _take_up(self, payload: LeviRoundState) -> None:
        self.calibration = (
            payload.calibration.model_copy(deep=True) if payload.calibration else None
        )
        self.elites = [e.model_copy(deep=True) for e in payload.elites]
        self.rounds_without_advance = payload.rounds_without_advance


def levi_state(state: WorkingState) -> LeviState:
    if not isinstance(state, LeviState):
        raise TypeError(f"a LEVI member was handed {type(state).__name__}, not LEVI's state")
    return state
