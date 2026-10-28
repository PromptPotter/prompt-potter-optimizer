from __future__ import annotations

from pydantic import Field

from promptpotter.domain.optimizer_state import RoundOutput, StateSlice
from promptpotter.domain.strict_model import FrozenModel


class ParetoSet(StateSlice, kind="pareto_set"):
    # Sample keys, in the order every round walks them; empty until round 1 draws the split.
    keys: list[str] = Field(default_factory=list)


class ParetoScores(StateSlice, kind="pareto_scores"):
    # By id then sample key, in the population's order; empty on the origin's document: round 1 seats it.
    scores: dict[str, dict[str, float]] = Field(default_factory=dict)
    # Drawn at each round's close; ``None`` until round 1 closes, the origin being the only parent.
    parent_id: str | None = None


class ReflectionModules(StateSlice, kind="reflection_modules"):
    """A child takes its parent's advanced pointer (the reference's `RoundRobinReflectionComponentSelector`)."""

    # By candidate id, an index into the chain's prompt nodes; absent is the first.
    modules: dict[str, int] = Field(default_factory=dict)


class MinibatchBar(FrozenModel):
    parent_id: str
    # By sample key, the parent's objective on each minibatch cell.
    objectives: dict[str, float]


class MinibatchBars(RoundOutput, kind="minibatch_bars"):
    """By arm id, the bar that arm alone is tested against, as the reference's proposals each carry their parent's minibatch scores; required, so written first."""

    bars: dict[str, MinibatchBar]
