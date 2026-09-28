"""LEVI's working state between rounds: its archive and what calibration fixed for it. Every round
document banks it whole, so a resume or a fork re-seats the archive off the round it continues from."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from promptpotter.domain.optimizer_state import LEVI_MANIFEST, LeviRoundState, OptimizerState

if TYPE_CHECKING:
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.application.optimizers.nodes import WorkingState
    from promptpotter.domain.optimizer_state import LeviCalibration, LeviElite
    from promptpotter.domain.results import RoundResult
    from promptpotter.infrastructure.ledger import CycleEventLog

__all__ = ["LeviState", "levi_round_state", "levi_state"]


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
        self._take_up(levi_round_state(last))

    def resume(self, ledger: CycleEventLog | None, selected: SelectedOptimizer) -> None:
        return None

    def absorb(self, round_result: RoundResult) -> None:
        self._take_up(levi_round_state(round_result))

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


def levi_round_state(round_result: RoundResult) -> LeviRoundState:
    payload = round_result.optimizer_state.payload
    if not isinstance(payload, LeviRoundState):
        raise TypeError(
            f"a LEVI reader was handed {round_result.optimizer_state.manifest!r}'s optimizer state"
        )
    return payload
