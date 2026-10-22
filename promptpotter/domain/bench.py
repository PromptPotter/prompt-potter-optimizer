from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, NamedTuple, Self, get_args

from pydantic import Field, model_validator

from promptpotter.domain.paired_reading import (
    ArmName,
    CellSet,
    CellSetBasis,
    CellSetName,
    MeasuredLift,
    PairedReading,
    ReadingState,
)
from promptpotter.domain.phases import StopOutcome, StopReason, WalkEnd, stop_reason_outcome
from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import CellGrade, WalkedCell
from promptpotter.domain.spend import SpendRollup
from promptpotter.domain.strict_model import FrozenModel, StrictModel
from promptpotter.shared.hashing import stable_hash

__all__ = [
    "BENCH_HEADLINE",
    "BENCH_STATE_INFO",
    "COLUMN_GRADE",
    "BandedValue",
    "BankPartition",
    "BenchColumn",
    "BenchPass",
    "BenchPasses",
    "BenchReading",
    "BenchScore",
    "BenchStateInfo",
    "BenchStatus",
    "BenchSubject",
    "BenchTrigger",
    "CostAbsence",
    "DatasetSplit",
    "GradedBench",
    "LiftCost",
    "LineRun",
    "OwnLevel",
    "PartitionRecord",
    "PassOutcome",
    "PassStop",
    "bench_split",
    "bench_status",
    "partition_bank",
    "restored_partition",
]


class DatasetSplit(StrictModel):
    bench: int = Field(
        ge=0,
        description="Distinct samples held out as the bench set, with every row that repeats "
        "one: no optimizer node ever reads one, and the headline is scored on them.",
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
    """``cycles/{id}/bank_partition.json``: the partition a run init drew, as slots in its bank."""

    dataset_hash: str
    split: DatasetSplit | None
    search_ids: list[int]
    bench_ids: list[int]
    # The held-out content two campaigns are compared on.
    bench_rows: CellSet
    demo_ids: list[int]

    @property
    def bank_ids(self) -> frozenset[int]:
        return frozenset([*self.search_ids, *self.bench_ids, *self.demo_ids])


def partition_bank(
    bank: Sequence[Sample], split: DatasetSplit | None, *, seed: int
) -> BankPartition:
    """Ranks DISTINCT samples by *seed* and content, so every copy lands on its sample's side."""
    declared = {s.key for s in bank if s.bench_only}
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
    if repeats := sorted(s.id for s in bank if not s.bench_only and s.key in declared):
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
        description="The walk's own end, or the run stop that cut the pass before it was sent."
    )
    warning: str | None = Field(
        description="The backend's dominant warning, where errors ended the pass."
    )


class BenchPass(FrozenModel):
    """One individual's pass over the bench set, as the facts it banked — never a grade of them."""

    round: int
    candidate_id: str
    sp_hash: str
    # In walk order; a row the pass never reached is absent.
    cells: list[WalkedCell]
    # Every row it was sent on, by content (`Sample.key`): the cell set two passes pair on.
    sample_keys: list[str]
    # `None` once it sent every row.
    stop: PassStop | None
    # The grader that stamped its live reading; a reader re-grades under its own.
    scorer_id: str
    # Individuals already graded on these rows when it was sent: a high count reads the headline optimistic.
    reads_before: int
    # By `Sample.key`, each row measuring another dataset than the one the cycle is filed under.
    measured_datasets: dict[str, str] = Field(default_factory=dict)

    def by_dataset(self) -> dict[str | None, list[str]]:
        """``None`` is the dataset the cycle is filed under."""
        out: dict[str | None, list[str]] = {}
        for key in self.sample_keys:
            out.setdefault(self.measured_datasets.get(key), []).append(key)
        return out


class BenchPasses(FrozenModel):
    """``runner/bench.py::read_bench`` is the one reading of these passes."""

    tolerance: int
    origin: BenchPass
    # Set aside so the selection's pass fits under the search's ceiling (``runner/bench.py::reserve_selection_pass``).
    reserve_usd: float
    reserve_tokens: int
    # The latest pass of each individual the line selected, by its id: a pass measures an
    # individual, so a rewind displaces none.
    selections: dict[str, BenchPass]

    def pass_of(self, individual_id: str) -> BenchPass | None:
        if individual_id == self.origin.candidate_id:
            return self.origin
        return self.selections.get(individual_id)


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


class BenchStateInfo(NamedTuple):
    """``sentence`` is SERVED on every status: no surface words a bench state of its own."""

    label: str
    sentence: str
    fault: bool


#: A subset of `ReadingState`: a refusal is a pair's, and one pass over one cell set refuses nothing.
BENCH_STATE_INFO: dict[ReadingState, BenchStateInfo] = {
    ReadingState.READ: BenchStateInfo(
        "graded", "The origin and the selection are graded on the held-out rows.", False
    ),
    ReadingState.NOT_ASKED: BenchStateInfo(
        "not asked",
        "Rows are held out and nobody has asked for the selection's pass: "
        "`python -m promptpotter bench` sends it.",
        False,
    ),
    ReadingState.PENDING: BenchStateInfo(
        "pending", "The line grades its selection when its run ends.", False
    ),
    ReadingState.NOT_HELD: BenchStateInfo(
        "nothing held out", "The campaign's dataset_split holds no bench rows out.", False
    ),
    ReadingState.HELD_ELSEWHERE: BenchStateInfo(
        "held elsewhere",
        "This cycle runs beside the campaign's line, and the bench grades the line's result.",
        False,
    ),
    ReadingState.PASS_STOPPED: BenchStateInfo(
        "pass stopped", "A bench pass stopped before its last row, so it reads nothing.", True
    ),
    ReadingState.PAST_TOLERANCE: BenchStateInfo(
        "past tolerance",
        "A bench pass ended with more rows carrying no verdict than the split's tolerance allows.",
        True,
    ),
    ReadingState.NO_SELECTION: BenchStateInfo(
        "no selection",
        "No round closed past the origin, so there is no selection to grade.",
        False,
    ),
    ReadingState.RUN_FAILED: BenchStateInfo(
        "run failed", "The run failed before the line graded its selection.", True
    ),
}
assert BENCH_STATE_INFO.keys() <= set(ReadingState)


class PassOutcome(NamedTuple):
    state: ReadingState
    stop: PassStop | None
    scored: int
    expected: int
    reads_before: int


class LineRun(NamedTuple):
    selecting: bool
    ending: StopReason | None
    # ``RunStanding.selection``: the origin until a round selects, ``None`` until round 0 closes.
    selection: ArmName | None
    rounds_closed: int


class BenchStatus(FrozenModel):
    """Where a campaign's bench stands, and whether asking for its pass would be admitted."""

    state: ReadingState
    trigger: BenchTrigger
    sentence: str = Field(description="`state` in words, with the rows and the stop behind it.")
    can_grade: bool = Field(
        description="Whether `bench` / `grade-bench` is admitted now. Every entry point reads "
        "this one answer."
    )
    refusal: str | None = Field(description="Why not, exactly where `can_grade` is false.")
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
        if self.can_grade is (self.refusal is not None):
            raise ValueError("a refusal is given exactly where the pass cannot be asked for")
        return self


def bench_status(
    *,
    trigger: BenchTrigger,
    bench_size: int,
    tolerance: int,
    on_line: bool,
    held_by: str | None,
    origin: PassOutcome | None,
    selected: PassOutcome | None,
    run: LineRun,
) -> BenchStatus:
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
        state = ReadingState.NOT_HELD
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

    sentence = BENCH_STATE_INFO[state].sentence
    if decided is not None and state is ReadingState.PAST_TOLERANCE:
        sentence += (
            f" {subject}: {decided.expected - decided.scored} of {decided.expected} rows, past a "
            f"tolerance of {tolerance}."
        )
    elif decided is not None and state is ReadingState.PASS_STOPPED and decided.stop is not None:
        warned = "" if decided.stop.warning is None else f": {decided.stop.warning}"
        sentence += (
            f" {subject}: {decided.stop.cause.value} after {decided.scored} of "
            f"{decided.expected} rows{warned}."
        )

    refusal: str | None = None
    if not on_line:
        refusal = (
            "This campaign has no manifest on disk."
            if held_by is None
            else f"Cycle {held_by} holds this campaign's line, and the bench grades the "
            "campaign's result. Ask for that cycle."
        )
    elif not bench_size:
        refusal = sentence
    elif run.selecting:
        refusal = "The run is still selecting. Pause it or let it end first."
    elif run.selection is None:
        refusal = "The origin has not been scored, so there is no reference to grade against."
    elif state is ReadingState.READ:
        refusal = "The line's selection is already graded."
    elif state is ReadingState.NO_SELECTION:
        refusal = sentence
    return BenchStatus(
        state=state,
        trigger=trigger,
        sentence=sentence,
        can_grade=refusal is None,
        refusal=refusal,
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
    def of(cls, vs_origin: PairedReading, spend: SpendRollup | None) -> LiftCost:
        usd = None if spend is None else spend.search_incurred_usd
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


def _bench_line(
    bench_size: int,
    headline: BenchColumn,
    status: BenchStatus,
    origin: BenchReading | None,
    selected: BenchReading | None,
    vs_origin: PairedReading,
) -> str:
    if status.state is not ReadingState.READ or origin is None or selected is None:
        return status.sentence

    def level(reading: BenchReading) -> str:
        return "—" if reading.level is None else f"{reading.level.value:.3f}"

    # `—` where the selection is the origin: one pass read twice compares nothing.
    read = vs_origin.lift(COLUMN_GRADE[headline])
    lift = "—" if read is None else f"{read.estimate.value:+.3f}"
    for column in get_args(BenchColumn):
        beside = vs_origin.lift(COLUMN_GRADE[column])
        if column != headline and beside is not None:
            lift += f" ({column} {beside.estimate.value:+.3f})"
    return (
        f"{headline} {level(selected)} selected (round {selected.round}) · "
        f"{level(origin)} origin · lift {lift} · {bench_size} held-out rows"
    )


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
    line: str = Field(
        description="The headline on one line — both levels, the lift in each column and the "
        "held-out row count, or `status.sentence` where the bench is not graded. The ONE wording "
        "of it: the completion box, the `bench` verb and every screen print this. Stamped by "
        ":meth:`of` alone."
    )

    @classmethod
    def of(
        cls,
        *,
        bench_size: int,
        scorer_id: str,
        headline: BenchColumn,
        status: BenchStatus,
        origin: BenchReading | None,
        selected: BenchReading | None,
        vs_origin: PairedReading,
        cost: LiftCost,
        scopes: list[BenchScope],
    ) -> Self:
        return cls(
            bench_size=bench_size,
            scorer_id=scorer_id,
            headline=headline,
            status=status,
            origin=origin,
            selected=selected,
            vs_origin=vs_origin,
            cost=cost,
            scopes=scopes,
            line=_bench_line(bench_size, headline, status, origin, selected, vs_origin),
        )

    @model_validator(mode="after")
    def _read_holds_both(self) -> Self:
        if self.status.state is ReadingState.READ and (
            self.origin is None or self.selected is None
        ):
            raise ValueError("a bench that reads holds the origin's level and the selection's")
        if self.line != _bench_line(
            self.bench_size, self.headline, self.status, self.origin, self.selected, self.vs_origin
        ):
            raise ValueError("a bench score's line is the wording of its own readings")
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
