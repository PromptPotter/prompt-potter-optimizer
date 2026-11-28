"""A phase act's facts, said once on its `PhaseRecord`; every renderer words them at render."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from promptpotter.domain.bench import BenchSubject
from promptpotter.domain.cell_fold import CellFold
from promptpotter.domain.paired_reading import ArmName, MeasurandUnit
from promptpotter.domain.run_standing import RunStanding
from promptpotter.domain.scoring import Scorer
from promptpotter.domain.spend import usd_text
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


class _Note(FrozenModel):
    """A preflight check's facts; its `title` and `detail` word them at render."""

    @property
    def title(self) -> str:
        raise NotImplementedError

    @property
    def detail(self) -> str:
        raise NotImplementedError


class RoundOverBank(_Note):
    check: Literal["sp_budget_exceeds_dataset"] = "sp_budget_exceeds_dataset"
    asked: int
    bank: int

    @property
    def title(self) -> str:
        return f"per-round eval budget ({self.asked}) exceeds bank size ({self.bank})"

    @property
    def detail(self) -> str:
        m = self.bank
        return (
            f"The bank (full train split) has only {m} samples, so every round scores on all {m} "
            f"and the sampler has nothing to select from. Lower its per-round budget to below "
            f"{m}, or grow the dataset."
        )


class OptimizerBelowTarget(_Note):
    check: Literal["optimizer_below_target"] = "optimizer_below_target"
    optimizer: str
    bigger: tuple[str, ...]

    @property
    def title(self) -> str:
        return f"optimizer LLM ({self.optimizer}) is smaller than the target ({', '.join(self.bigger)})"

    @property
    def detail(self) -> str:
        return (
            "The optimizer is the strong model that improves the pipeline; running it on a model "
            "smaller than the target it optimizes is usually an accidental inversion. Raise the "
            "proposing node's `model` — in the manifest under `promptpotter/assets/optimizers/`, "
            "or this campaign's `optimization.nodes` — to a larger tier."
        )


class CouplingActive(_Note):
    check: Literal["config_coupling"] = "config_coupling"
    name: str
    severity: str
    knobs: tuple[str, ...]
    relation: str
    consequence: str

    @property
    def title(self) -> str:
        return f"config coupling [{self.severity}]: {', '.join(self.knobs)}"

    @property
    def detail(self) -> str:
        return f"{self.relation} {self.consequence}"


class Unframed(_Note):
    """``chosen``: `task_framing: off`, the framing ablation; else no framing was written."""

    check: Literal["unframed"] = "unframed"
    chosen: bool

    @property
    def title(self) -> str:
        if self.chosen:
            return "running UNFRAMED on purpose — `task_framing: off`"
        return "no task framing — L1 will not be told what this dataset is"

    @property
    def detail(self) -> str:
        if self.chosen:
            return (
                "The framing ablation: no target render splices the dataset's task framing and "
                "l1_generate's task_intent slot renders as the static template alone. Compare "
                "this run only against framed runs of the same dataset, never pool with them."
            )
        return (
            "`task_context` carries every field the operator wrote about the task, and it is the "
            "only panel feeding l1_generate's task_intent slot. Empty, that slot renders as the "
            "static template alone: the generator proposes edits knowing the failing samples and "
            "nothing about what the pipeline is for. Ship a `task.md` in the dataset's "
            "definition — the first mint decomposes its body — or give the mint a task text."
        )


class CapUnderRound(_Note):
    """A USD cap under what one round can cost, priced on a billed cell where one is archived."""

    check: Literal["spend_cap_below_round"] = "spend_cap_below_round"
    optimizer: str
    cap: float
    need: float
    cells: int
    search: int
    price: float
    billed: bool

    @property
    def costing(self) -> str:
        priced = (
            f"a cell of this dataset has billed {usd_text(self.price)} on these models"
            if self.billed
            else f"the spend book admits a cell at up to {usd_text(self.price)}"
        )
        return (
            f"One round of {self.optimizer} can measure {self.cells} cells off the {self.search} "
            f"search rows, and {priced}"
        )

    @property
    def title(self) -> str:
        cap, need = usd_text(self.cap), usd_text(self.need)
        if self.billed:
            return f"spend cap {cap} is under one round's expected cost ({need})"
        return f"spend cap {cap} is under one round's ceiling ({need})"

    @property
    def detail(self) -> str:
        need, advice = (
            usd_text(self.need),
            "Raise its `budget.usd`, or narrow the round in `optimization.nodes`.",
        )
        if self.billed:
            return (
                f"{self.costing}, so a full round is expected to cost {need} and this run to stop "
                f"on `spend_budget` inside round 1 unless an eliminator cuts arms short. {advice}"
            )
        return (
            f"{self.costing}, so only a cap of {need} is certain to close a round. A cell usually "
            "bills well under its bound and an eliminator cuts arms short, so this run may still "
            f"close rounds — or stop on `spend_budget` inside round 1 with none closed. {advice}"
        )


class OriginAlreadyRun(_Note):
    """A cycle id is content-addressed: another campaign holding it ran the identical origin."""

    check: Literal["origin_already_run"] = "origin_already_run"
    campaigns: tuple[str, ...]

    @property
    def title(self) -> str:
        return f"identical origin already run in {', '.join(self.campaigns)}"

    @property
    def detail(self) -> str:
        return (
            "A fresh campaign re-measures the identical seed. Resuming one of those continues it "
            "instead; a fresh campaign is for an origin you have CHANGED (optimizer prompt, "
            "config, or dataset)."
        )


class InstrumentNovel(_Note):
    check: Literal["instrument_novel"] = "instrument_novel"
    instrument: str
    campaigns: int
    fingerprints: int

    @property
    def title(self) -> str:
        return (
            f"instrument {self.instrument} is new — none of {self.campaigns} prior campaigns replay"
        )

    @property
    def detail(self) -> str:
        return (
            f"Those campaigns carry {self.fingerprints} other fingerprint(s), so no banked cell "
            "replays: this run re-measures its origin and can be compared to none of them. "
            "Expected while the engine is being rewritten; it is also the reason a panel does "
            "not accumulate across runs."
        )


class NoRanker(_Note):
    """The converse is deliberately not warned: a ranker with no labels is no fault."""

    check: Literal["no_terminal_ranker"] = "no_terminal_ranker"

    @property
    def title(self) -> str:
        return "the pipeline has no terminal ranker"

    @property
    def detail(self) -> str:
        return (
            "No node emits a ranked list, so every sample will score NO_RESULT against a real "
            "label: check node_role on the final node."
        )


class SurveyUnread(_Note):
    """Campaigns the survey of earlier runs could not read: their rounds inform nothing here."""

    check: Literal["survey_unread"] = "survey_unread"
    campaigns: tuple[str, ...]
    reason: str

    @property
    def title(self) -> str:
        return f"the survey of earlier runs skipped {', '.join(self.campaigns)}"

    @property
    def detail(self) -> str:
        return f"Their rounds could not be read ({self.reason}), so no block this run earns reads them."


PreflightNote = Annotated[
    RoundOverBank
    | OptimizerBelowTarget
    | CouplingActive
    | Unframed
    | CapUnderRound
    | OriginAlreadyRun
    | InstrumentNovel
    | NoRanker
    | SurveyUnread,
    Field(discriminator="check"),
]


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


class CountFact(FrozenModel):
    kind: Literal["count"] = "count"
    value: int


class FitnessFact(FrozenModel):
    """In the grading scorer's unit; a composite fitness has none."""

    kind: Literal["fitness"] = "fitness"
    value: float
    unit: MeasurandUnit | None = None


def fold_fitness(fold: CellFold | None) -> FitnessFact | None:
    """*fold*'s fitness in its scorer's unit; ``None`` is unmeasured."""
    if fold is None or fold.fitness is None:
        return None
    return FitnessFact(value=fold.fitness, unit=fold.fitness_unit)


class TextFact(FrozenModel):
    """Whole, as banked: a rule's name, a plan, a rationale; empty is one none gave."""

    kind: Literal["text"] = "text"
    value: str


class SettingsFact(FrozenModel):
    """Knobs to their values, unjoined: a temperature stays a number."""

    kind: Literal["settings"] = "settings"
    value: dict[str, int | float | str]


# A step's fact, by its kind, which its renderer words; ``None`` is unmeasured, never said.
type StepFact = Annotated[
    CountFact | FitnessFact | TextFact | SettingsFact, Field(discriminator="kind")
]


class ProposeOpened(FrozenModel):
    """``facts`` are the walk's lead's (``RoundOpening``)."""

    act: Literal["propose_opened"] = "propose_opened"
    node: str
    # Null where no rounds axis binds; 0 is origin only.
    budget_rounds: int | None
    # The round the parent last ended, whose head it is: its prompt and its level are that round's.
    parent_round: int
    arms: int | None
    facts: dict[str, StepFact | None]
    model: str | None
    run_standing: RunStanding | None


class ProposeClosed(FrozenModel):
    """Its candidates are the round's `ArmRecord`s, appended with it."""

    act: Literal["propose_closed"] = "propose_closed"
    replayed: bool


# The row choice a round's race ran: ``cold`` is ``informative`` on a ruler that tells every row
# alike, so the sampler's order ran.
RowsRan = Literal["random", "informative", "cold"]


class MeasureOpened(FrozenModel):
    act: Literal["measure_opened"] = "measure_opened"
    candidates: int
    cells: int
    rows: RowsRan


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


class StepOpened(FrozenModel):
    """An optimizer step's facts on its fire, by name: generic, so an optimizer stays a plugin."""

    act: Literal["step_opened"] = "step_opened"
    node: str
    activity: str
    facts: dict[str, StepFact | None]


class StepClosed(FrozenModel):
    """``facts`` is empty where the step adopted nothing; ``audit`` names the node whose call the
    round's audit holds."""

    act: Literal["step_closed"] = "step_closed"
    facts: dict[str, StepFact | None]
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
