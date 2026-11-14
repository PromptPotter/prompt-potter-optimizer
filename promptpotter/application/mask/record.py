from __future__ import annotations

from pydantic import Field

from promptpotter.domain.cycle_paths import Lens
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.records.run_records import ResumeCheckpointRecord
from promptpotter.domain.results import RoundResult
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.shared.worded import Worded

#: The `score:` formulas a picklist offers by name, keyed as the `lens=` it sends; any other is
#: the operator's own expression.
SCORE_LENS_LABELS: Worded[str, str] = Worded({Lens("score", "fitness").spelling: "Fitness"})


class MaskReading(FrozenModel):
    composite_fitness: float
    fitness: float | None
    # `fitness` is the mean over exactly these.
    n_scored: int


class MaskCandidate(FrozenModel):
    """``is_eligible`` is a recorded fact, invariant under a scoring swap."""

    key: ArmKey
    reading: MaskReading | None = None
    is_selected: bool = False
    is_eligible: bool = True
    # An ``EliminationGate`` value; ``None`` ran to completion.
    abort: str | None = None


class MaskRound(FrozenModel):
    """``parent`` is round ``N-1``'s winner under the record's scorer; under a SAMPLE-SET mask, ``load.py::_parent``."""

    cycle_id: str
    round: int
    candidates: list[MaskCandidate] = Field(default_factory=list)
    parent: MaskReading | None = None
    # Carried only under ``with_replay``.
    round_data: RoundResult | None = None
    decisions: list[ResumeCheckpointRecord] = Field(default_factory=list)


class MaskCycle(FrozenModel):
    """``rounds`` is the cycle's whole line; ``branch_round`` the parent round its start was proposed in."""

    cycle_id: str
    parent_cycle_id: str | None = None
    branch_round: int | None = None
    rounds: list[MaskRound] = Field(default_factory=list)


class MaskRecord(FrozenModel):
    cycles: list[MaskCycle] = Field(default_factory=list)
    # The `per_cell` formula a fork applying the lens carries: a `dials:` lens realized.
    criterion: str | None = None


class SpineCycle(FrozenModel):
    """Every term is a round-CLOSE fact on the ledger, so a rewind is decided without opening one round file."""

    cycle_id: str
    parent_cycle_id: str | None = None
    cut_round: int | None = None
    # Subset-invariant, so it alone averages up a lineage whose rounds scored different subsets.
    # Every round the cycle closed is a node; ``None`` where its level does not difference.
    theta_by_round: dict[int, float | None]
    # The individual each node's line stands on once it closed: a held round's is its parent.
    pick_by_round: dict[int, str]
