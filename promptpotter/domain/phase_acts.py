"""A phase act's facts, said once on its `PhaseRecord`; every renderer words them at render."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from promptpotter.domain.bench import BenchSubject
from promptpotter.domain.paired_reading import ArmName
from promptpotter.domain.run_standing import RunStanding
from promptpotter.domain.scoring import Scorer
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.domain.verification import VerifyStrategy
from promptpotter.shared.composite import to_short_formula
from promptpotter.shared.measurement_context import MeasurementRole


class CellFormula(FrozenModel):
    """What a cell's worth is read by; a short form exists only for the default, never an override."""

    formula: str
    overridden: bool

    @classmethod
    def of(cls, scorer: Scorer) -> CellFormula:
        return cls(formula=scorer.cell_formula, overridden=bool(scorer.per_cell))

    @property
    def short(self) -> str | None:
        return None if self.overridden else to_short_formula(self.formula)


class PreflightNote(FrozenModel):
    title: str
    detail: str


class InitOpened(FrozenModel):
    """The formula rides here so the mask editor has one to reference DURING origin scoring."""

    act: Literal["init_opened"] = "init_opened"
    warnings: tuple[PreflightNote, ...] = ()
    formula: CellFormula


class InitClosed(FrozenModel):
    """``resumed_from_round`` is the NEXT L1 round; ``cached_rounds`` counts round artifacts."""

    act: Literal["init_closed"] = "init_closed"
    origin_fitness: float | None
    cycle_id: str
    samples: int
    bench_samples: int
    resumed_from_round: int
    cached_rounds: int
    framing_keys: int
    formula: CellFormula


class ProposeOpened(FrozenModel):
    """``standing`` and ``note`` are the optimizer's own words (``RoundOpening``)."""

    act: Literal["propose_opened"] = "propose_opened"
    node: str
    # Null where no rounds axis binds; 0 is origin only.
    budget_rounds: int | None
    standing: str
    # The round the parent last ended, whose head it is: its prompt and its level are that round's.
    parent_round: int
    arms: int | None
    note: str
    model: str | None
    run_standing: RunStanding | None


class ProposeClosed(FrozenModel):
    """Its candidates are the round's `ArmRecord`s, appended with it."""

    act: Literal["propose_closed"] = "propose_closed"
    replayed: bool
    proposer: str


class MeasureOpened(FrozenModel):
    act: Literal["measure_opened"] = "measure_opened"
    candidates: int
    cells: int


class PassOpened(FrozenModel):
    """Every ``sample_scored`` up to the matching exit is one of its rows; round 0 is the origin."""

    act: Literal["pass_opened"] = "pass_opened"
    verification: MeasurementRole
    # A bench pass's place on the line; `None` on a verify.
    subject: BenchSubject | None
    arm: ArmName
    sp_hash: str
    rows: int
    strategy: VerifyStrategy | None


# A step's fact, whole: its renderer words and cuts it; ``None`` is unmeasured.
type StepFact = str | int | float | None


class StepOpened(FrozenModel):
    """An optimizer step's facts on its fire, by name: generic, so an optimizer stays a plugin."""

    act: Literal["step_opened"] = "step_opened"
    node: str
    activity: str
    facts: dict[str, StepFact]


class StepClosed(FrozenModel):
    """``facts`` is empty where the step adopted nothing; ``audit`` names the node whose call the
    round's audit holds."""

    act: Literal["step_closed"] = "step_closed"
    facts: dict[str, StepFact]
    audit: str | None


type PhaseAct = Annotated[
    InitOpened
    | InitClosed
    | ProposeOpened
    | ProposeClosed
    | MeasureOpened
    | PassOpened
    | StepOpened
    | StepClosed,
    Field(discriminator="act"),
]
