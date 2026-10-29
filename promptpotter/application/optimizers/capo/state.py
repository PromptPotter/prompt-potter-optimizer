"""The population CAPO's selector keeps is the bench's (``OptimizerState.population``), banked beside this."""

from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.domain.optimizer_state import StateSlice
from promptpotter.domain.scoring import ROW_GRADES

if TYPE_CHECKING:
    from promptpotter.domain.scoring import GradedCell


class CapoObjective(StateSlice, kind="capo_objective"):
    """What the race and the population rank on (§4), set by the initial population, which writes it first."""

    length_penalty: float
    # The longest initial prompt's scored length, the length term's divisor.
    length_norm: int

    def of(self, cell: GradedCell, chars: int) -> float:
        """Unclamped, so a long prompt can take a cell below zero."""
        fitness = float(ROW_GRADES["fitness"].read(cell))
        return fitness - self.length_penalty * chars / self.length_norm
