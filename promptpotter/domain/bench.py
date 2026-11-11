from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from functools import cached_property
from typing import TYPE_CHECKING, Literal, NamedTuple, Self

from pydantic import Field, model_validator

from promptpotter.domain.paired_reading import (
    READING_STATE_INFO,
    READING_STATE_KIND,
    ArmName,
    CellSet,
    CellSetBasis,
    CellSetName,
    MeasuredLift,
    PairedReading,
    ReadingState,
    ReadingStateKind,
)
from promptpotter.domain.phases import StopOutcome, StopReason, WalkEnd, stop_reason_outcome
from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import CellGrade
from promptpotter.domain.strict_model import FrozenModel, StrictModel
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.hashing import stable_hash
from promptpotter.shared.worded import Worded

if TYPE_CHECKING:
    # `campaign.py` imports this module: the refusal is read here by its member's name.
    from promptpotter.domain.campaign import CampaignRefusal


class DatasetSplit(StrictModel):
    bench: int = Field(
        ge=0,
        description="Distinct samples the split ranks away from the search as the bench set, with "
        "every row that repeats one: no optimizer node ever reads one, and the headline is scored "
        "on them. A row the bank declares `held_out` is held out beside them, never counted here.",
    )
    demo: int = Field(
        0,
        ge=0,
        description="Distinct samples reserved as the demo pool — the rows an individual's "
        "`shots` name by `Sample.key`, rendered into its prompt as query and ground truth, never "
        "scored.",
    )
    tolerance: int = Field(
        0,
        ge=0,
        description="Bench rows a pass may end with no verdict on — a provider fault, or a cell "
        "the formula cannot grade — and still be a reading. Past it the pass reads nothing: a "
        "headline over a population other than the one sent is not the bench score.",
    )


def bench_split(sample_keys: Iterable[str]) -> CellSet:
    return CellSet.of(
        CellSetName.BENCH_SPLIT, CellSetBasis.DECLARED, list(sample_keys), dataset_hash=None
    )


@dataclass(frozen=True)
class BankPartition:
    split: DatasetSplit | None
    search: tuple[Sample, ...]
    bench: tuple[Sample, ...]
    demo: tuple[Sample, ...]

    @property
    def admitted_ids(self) -> frozenset[int] | None:
        """``None`` when nothing is held out, so an L4 cell drawn from a bank still reads every row."""
        return None if self.split is None else frozenset(s.id for s in self.search)

    @property
    def admitted_keys(self) -> frozenset[str] | None:
        """:attr:`admitted_ids` as the δ scale names them."""
        return None if self.split is None else frozenset(s.key for s in self.search)

    @property
    def bank(self) -> tuple[Sample, ...]:
        return (*self.search, *self.bench, *self.demo)

    @property
    def bench_rows(self) -> CellSet:
        return bench_split(s.key for s in self.bench)

    def record(self, *, dataset_hash: str) -> PartitionRecord:
        return PartitionRecord(
            dataset_hash=dataset_hash,
            split=self.split,
            search_ids=[s.id for s in self.search],
            bench_ids=[s.id for s in self.bench],
            bench_rows=self.bench_rows,
            demo_ids=[s.id for s in self.demo],
        )


class PartitionRecord(FrozenModel):
    """The partition a cycle's mint drew, as slots in its bank; a fork reads its parent's off the chain."""

    record_type: Literal["bank_partition"] = "bank_partition"
    dataset_hash: str
    split: DatasetSplit | None
    search_ids: list[int]
    bench_ids: list[int]
    # The held-out content two campaigns are compared on.
    bench_rows: CellSet
    demo_ids: list[int]
    timestamp: str = Field(default_factory=utcnow_iso)

    @cached_property
    def bank_ids(self) -> frozenset[int]:
        return frozenset([*self.search_ids, *self.bench_ids, *self.demo_ids])


def partition_bank(
    bank: Sequence[Sample], split: DatasetSplit | None, *, seed: int
) -> BankPartition:
    """Ranks DISTINCT samples by *seed* and content, so every copy lands on its sample's side."""
    declared = {s.key for s in bank if s.held_out}
    rank = {k: stable_hash([seed, k]) for k in {s.key for s in bank}}

    def ordered(rows: Iterable[Sample]) -> tuple[Sample, ...]:
        return tuple(sorted(rows, key=lambda s: rank[s.key]))

    if split is None:
        if declared:
            raise ValueError(
                f"{len(declared)} samples of this bank are bench-only and no dataset_split is "
                "declared, so the search would draw rows the dataset holds out."
            )
        return BankPartition(split=None, search=ordered(bank), bench=(), demo=())
    if repeats := sorted(s.id for s in bank if not s.held_out and s.key in declared):
        raise ValueError(
            f"rows {repeats} repeat the content of a bench-only row, so one sample would sit on "
            "both sides of the split."
        )
    keys = sorted({s.key for s in bank} - declared, key=rank.__getitem__)
    held = split.bench + split.demo
    if held >= len(keys):
        raise ValueError(
            f"dataset_split holds out {held} of the {len(keys)} distinct samples it ranks in a "
            f"{len(bank)}-row bank (bench {split.bench}, demo {split.demo}), which leaves the "
            "search none to draw."
        )
    bench_keys = set(keys[: split.bench]) | declared
    demo_keys = set(keys[split.bench : held])
    if unlabelled := sorted(s.id for s in bank if s.key in demo_keys and s.ground_truth is None):
        raise ValueError(
            f"demo rows {unlabelled} carry no ground truth, so they cannot render as a shot: a "
            "verifier-graded bank declares no demo pool."
        )
    return BankPartition(
        split=split,
        search=ordered(s for s in bank if s.key not in bench_keys and s.key not in demo_keys),
        bench=tuple(s for s in bank if s.key in bench_keys),
        demo=tuple(s for s in bank if s.key in demo_keys),
    )


def restored_partition(bank: Sequence[Sample], drawn: PartitionRecord) -> BankPartition:
    """*bank* is the one ``drawn.dataset_hash`` names, so a slot is its position."""
    return BankPartition(
        split=drawn.split,
        search=tuple(bank[i] for i in drawn.search_ids),
        bench=tuple(bank[i] for i in drawn.bench_ids),
        demo=tuple(bank[i] for i in drawn.demo_ids),
    )


BenchSubject = Literal["origin", "selected"]

# `manual`: only when asked (`bench`, `grade-bench`); `each_round`: at the end AND every round that selected.
BenchTrigger = Literal["manual", "at_end", "each_round"]


class PassStop(FrozenModel):
    """Why a bench pass holds fewer rows than it was sent on."""

    cause: StopReason | WalkEnd = Field(
        description="The walk's own end, or the run stop that cut the pass: a pause mid-walk, a "
        "spent budget before it was sent."
    )
    warning: str | None = Field(
        description="The backend's dominant warning, where errors ended the pass."
    )

    @property
    def state(self) -> ReadingState:
        """A pause leaves the pass owed, never faulted: a `start-run` sends it again."""
        paused = (
            isinstance(self.cause, StopReason)
            and stop_reason_outcome(self.cause) is StopOutcome.PAUSED
        )
        return ReadingState.PENDING if paused else ReadingState.PASS_STOPPED


BenchColumn = Literal["accuracy", "composite"]

BENCH_HEADLINE: BenchColumn = "accuracy"

COLUMN_GRADE: dict[BenchColumn, CellGrade] = {"accuracy": "fitness", "composite": "objective"}


class BandedValue(FrozenModel):
    value: float
    ci_lo: float | None = Field(
        description="The 95% band on `value`, drawn from the same per-row values; both bounds are "
        "`None` below two rows, which have no spread."
    )
    ci_hi: float | None

    @model_validator(mode="after")
    def _band_is_whole(self) -> Self:
        if (self.ci_lo is None) is not (self.ci_hi is None):
            raise ValueError("a band has both bounds or neither")
        return self


class OwnLevel(FrozenModel):
    """One individual's own level on one set of rows, in both columns; never one side of a pair."""

    accuracy: BandedValue | None = Field(
        description="Mean per-row fitness — the hit rate under a scorer that grades each row 0 "
        "or 1."
    )
    composite: BandedValue | None = Field(
        description="Under the reading scorer's formula, which charges cost and length — so it is "
        "never the change in the hit rate."
    )
    n: int = Field(
        description="Rows carrying a verdict — a miss the prompt caused included: the population "
        "both columns are read over."
    )

    def of(self, column: BenchColumn) -> BandedValue | None:
        value: BandedValue | None = getattr(self, column)
        return value


class BenchReading(OwnLevel):
    """One individual's bench pass, read under a named scorer."""

    round: int = Field(description="The round whose selection this is; 0 is the origin.")
    sp_hash: str = Field(description="The searchpoint scored — the archive's `prompt_fields_id`.")
    headline: BenchColumn = Field(description="Which column the headline reads.")
    level: BandedValue | None = Field(
        description="The headline: this reading in its `headline` column, chosen here so no "
        "surface selects a column of its own. Stamped by :meth:`read` alone."
    )

    @classmethod
    def read(cls, own: OwnLevel, *, round: int, sp_hash: str, headline: BenchColumn) -> Self:
        return cls(
            round=round,
            sp_hash=sp_hash,
            headline=headline,
            level=own.of(headline),
            accuracy=own.accuracy,
            composite=own.composite,
            n=own.n,
        )

    @model_validator(mode="after")
    def _level_is_the_headline_column(self) -> Self:
        if self.level != self.of(self.headline):
            raise ValueError("a bench reading's level is its headline column's")
        return self


class PassOutcome(NamedTuple):
    state: ReadingState
    stop: PassStop | None
    scored: int
    expected: int
    reads_before: int | None


class LineRun(NamedTuple):
    selecting: bool
    # A bench pass's bracket stands open on the cycle, and a process still holds it.
    pass_open: bool
    ending: StopReason | None
    # ``RunStanding.selection``: the origin until a round selects, ``None`` until round 0 closes.
    selection: ArmName | None
    rounds_closed: int


class BenchRefusal(StrEnum):
    """Why a bench pass cannot be asked for now."""

    INNER = "inner"
    NO_MANIFEST = "no_manifest"
    HELD_ELSEWHERE = "held_elsewhere"
    # The campaign's own refusals serve `CampaignRefusal`'s values: one word at both doors.
    NOT_ACTIVE = "campaign_not_active"
    DEFINITION_UNREAD = "definition_unread"
    CONNECTOR_MOVED = "connector_moved"
    NO_HOLDOUT = "no_holdout"
    IN_FLIGHT = "in_flight"
    SELECTING = "selecting"
    ORIGIN_UNSCORED = "origin_unscored"
    GRADED = "graded"
    NO_SELECTION = "no_selection"


BENCH_REFUSAL_INFO: Worded[BenchRefusal, str] = Worded(
    {
        BenchRefusal.INNER: "An inner run is one cell of its outer campaign; no command grades its line.",
        BenchRefusal.NO_MANIFEST: "This campaign has no manifest on disk.",
        BenchRefusal.HELD_ELSEWHERE: (
            "Another cycle (`held_by`) holds this campaign's line, and the bench grades the "
            "campaign's result. Ask for that cycle."
        ),
        BenchRefusal.NOT_ACTIVE: (
            "An archived or deleted campaign scores nothing more; restore an archived one to "
            "grade its line."
        ),
        BenchRefusal.DEFINITION_UNREAD: (
            "This campaign's dataset no longer reads as a definition naming a connector, so "
            "nothing grades its line until it does."
        ),
        BenchRefusal.CONNECTOR_MOVED: (
            "This campaign's dataset now names another connector than the one it was minted on, "
            "so its line grades on neither. Point the dataset back, or start a fresh campaign."
        ),
        BenchRefusal.NO_HOLDOUT: READING_STATE_INFO[ReadingState.NO_HOLDOUT].sentence,
        BenchRefusal.IN_FLIGHT: "A bench pass is grading now; its reading lands when it ends.",
        BenchRefusal.SELECTING: "The run is still selecting. Pause it or let it end first.",
        BenchRefusal.ORIGIN_UNSCORED: (
            "The origin has not been scored, so there is no reference to grade against."
        ),
        BenchRefusal.GRADED: "The line's selection is already graded.",
        BenchRefusal.NO_SELECTION: "No round closed past the origin, so there is no selection to grade.",
    }
)
assert BENCH_REFUSAL_INFO.keys() == set(BenchRefusal)


class BenchStatus(FrozenModel):
    """Where a campaign's bench stands, and whether asking for its pass would be admitted."""

    state: ReadingState
    trigger: BenchTrigger
    can_run: bool = Field(
        description="Whether `bench` / `grade-bench` is admitted now. Every entry point reads "
        "this one answer."
    )
    refusal: BenchRefusal | None = Field(description="Why not, exactly where `can_run` is false.")
    offered: bool = Field(
        description="Whether the grade control shows: admitted, or a pass still waited on. "
        "Never on an inner run, whose line no command grades."
    )
    subject: BenchSubject | None = Field(
        description="Whose pass `state` names, where one pass decides it."
    )
    held_by: str | None = Field(
        description="The cycle holding the campaign's line, where that is another one."
    )
    stop: PassStop | None
    scored: int | None
    expected: int | None
    reads_before: int | None = Field(
        description="Individuals the archive had graded on these held-out rows when the "
        "selection's pass was sent: each one chosen off a headline spent the holdout. `None` "
        "where no pass of the selection stands."
    )

    @model_validator(mode="after")
    def _refusal_fits(self) -> Self:
        if self.can_run is (self.refusal is not None):
            raise ValueError("a refusal is given exactly where the pass cannot be asked for")
        return self


def bench_status(
    *,
    trigger: BenchTrigger,
    bench_size: int,
    on_line: bool,
    inner: bool,
    refused: CampaignRefusal | None,
    held_by: str | None,
    origin: PassOutcome | None,
    selected: PassOutcome | None,
    run: LineRun,
) -> BenchStatus:
    """*refused* is the campaign's own refusal, the one admission refuses a pass with
    (`admission.py::campaign_refusal`)."""
    subject: BenchSubject | None = None
    decided: PassOutcome | None = None
    outcome = None if run.ending is None else stop_reason_outcome(run.ending)
    unsent = (
        ReadingState.NOT_ASKED
        if trigger == "manual"
        else ReadingState.PENDING
        if outcome is None or outcome is StopOutcome.PAUSED
        else ReadingState.RUN_FAILED
        if outcome is StopOutcome.FAILED
        else ReadingState.NOT_ASKED
    )
    if not on_line:
        state = ReadingState.HELD_ELSEWHERE
    elif not bench_size:
        state = ReadingState.NO_HOLDOUT
    elif origin is None:
        state = unsent
    elif origin.state is not ReadingState.READ:
        state, subject, decided = origin.state, "origin", origin
    elif selected is None:
        # Asking again would send nothing: the pass a line with no round is owed is the origin's.
        nothing_to_ask = unsent is ReadingState.NOT_ASKED and not run.rounds_closed
        state = ReadingState.NO_SELECTION if nothing_to_ask else unsent
    else:
        state, decided = selected.state, selected
        subject = None if state is ReadingState.READ else "selected"

    refusal: BenchRefusal | None = None
    if inner:
        refusal = BenchRefusal.INNER
    elif not on_line:
        refusal = BenchRefusal.NO_MANIFEST if held_by is None else BenchRefusal.HELD_ELSEWHERE
    elif refused is not None:
        refusal = BenchRefusal(refused.value)
    elif not bench_size:
        refusal = BenchRefusal.NO_HOLDOUT
    elif run.pass_open:
        refusal = BenchRefusal.IN_FLIGHT
    elif run.selecting:
        refusal = BenchRefusal.SELECTING
    elif run.selection is None:
        refusal = BenchRefusal.ORIGIN_UNSCORED
    elif state is ReadingState.READ:
        refusal = BenchRefusal.GRADED
    elif state is ReadingState.NO_SELECTION:
        refusal = BenchRefusal.NO_SELECTION
    return BenchStatus(
        state=state,
        trigger=trigger,
        can_run=refusal is None,
        refusal=refusal,
        offered=refusal is None
        or (not inner and READING_STATE_KIND[state] is ReadingStateKind.WAITING),
        subject=subject,
        held_by=held_by,
        stop=None if decided is None else decided.stop,
        scored=None if decided is None else decided.scored,
        expected=None if decided is None else decided.expected,
        reads_before=None if selected is None else selected.reads_before,
    )


class CostAbsence(StrEnum):
    """Why a bench lift carries no price."""

    NO_LIFT = "no_lift"
    UNPRICED_TOKENS = "unpriced_tokens"
    ZERO_SPEND = "zero_spend"


COST_ABSENCE_WORDS: Worded[CostAbsence, str] = Worded(
    {
        CostAbsence.NO_LIFT: "no lift to price",
        CostAbsence.UNPRICED_TOKENS: "search tokens unpriced",
        CostAbsence.ZERO_SPEND: "no search spend",
    }
)


class LiftCost(FrozenModel):
    """The headline lift per USD its SEARCH incurred, or why there is none."""

    lift_per_usd: float | None = Field(
        description="Priced on INCURRED spend, never the bill — a replayed cell is billed "
        "nothing, which would price arriving second — and never on the bench's own pass."
    )
    absent: CostAbsence | None

    @model_validator(mode="after")
    def _one_side(self) -> Self:
        if (self.lift_per_usd is None) is (self.absent is None):
            raise ValueError("a lift is priced, or says why it is not")
        return self

    @classmethod
    def of(cls, vs_origin: PairedReading, usd: float | None) -> LiftCost:
        """*usd* is what the search incurred, `None` on unpriced search tokens."""
        absent = (
            CostAbsence.NO_LIFT
            if vs_origin.headline is None
            else CostAbsence.UNPRICED_TOKENS
            if usd is None
            else CostAbsence.ZERO_SPEND
            if usd <= 0.0
            else None
        )
        if absent is not None or vs_origin.headline is None or usd is None:
            return cls(lift_per_usd=None, absent=absent)
        return cls(lift_per_usd=vs_origin.headline.estimate.value / usd, absent=None)


class BenchScope(FrozenModel):
    """One dataset's rows of a bench spanning several, read apart: a level is never averaged
    across datasets."""

    dataset: str | None = Field(
        description="The dataset these rows measure; `None` is the one the cycle is filed under."
    )
    rows: int
    origin: OwnLevel | None = Field(description="`None` where the origin's pass read nothing.")
    selected: OwnLevel | None = Field(description="`None` where the selection's read nothing.")
    vs_origin: PairedReading | None = Field(
        description="`selected` over `origin` on this dataset's rows alone; `None` where either "
        "read nothing."
    )


class GradedBench(NamedTuple):
    origin: BenchReading
    selected: BenchReading


class BenchScore(FrozenModel):
    """The headline: the selection and the origin, scored on a bench set no optimizer node read."""

    bench_size: int
    scorer_id: str = Field(
        description="The grader every number here was read under. A stored copy is a cache of that "
        "reading: a reader under another grader reads the passes again, never this."
    )
    headline: BenchColumn = Field(
        description="Which column the headline reads; `vs_origin.headline` is its lift."
    )
    status: BenchStatus
    origin: BenchReading | None = Field(
        description="The origin's own level; `None` where its pass read nothing."
    )
    selected: BenchReading | None = Field(
        description="The selection's own level — the headline. `None` where its pass read nothing."
    )
    vs_origin: PairedReading = Field(
        description="`selected` over `origin`, paired on the bench rows both scored. "
        "`same_individual` where the origin is the selection: nothing was compared."
    )
    cost: LiftCost
    scopes: list[BenchScope] = Field(
        default_factory=list,
        description="Per dataset, where the bench rows measure several; empty on one. The two "
        "levels above then read no column, since two datasets' levels never average, while "
        "`vs_origin` still pairs every row.",
    )

    @model_validator(mode="after")
    def _read_holds_both(self) -> Self:
        if self.status.state is ReadingState.READ and (
            self.origin is None or self.selected is None
        ):
            raise ValueError("a bench that reads holds the origin's level and the selection's")
        return self

    @property
    def graded(self) -> GradedBench | None:
        """The ONE test of whether a bench carries a headline: both levels, exactly where it read."""
        if self.status.state is not ReadingState.READ:
            return None
        assert self.origin is not None and self.selected is not None
        return GradedBench(self.origin, self.selected)

    def lift(self, column: BenchColumn) -> MeasuredLift | None:
        return self.vs_origin.lift(COLUMN_GRADE[column])
