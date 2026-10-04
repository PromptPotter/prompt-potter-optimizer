"""The bench's half of a campaign: the held-out partition of the bank, and the score it reads there.
Contract: ``docs/architecture.md`` § The bench score is not an optimizer's selection."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import ConfigDict, Field

from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import CellGrade
from promptpotter.domain.spend import SpendRollup
from promptpotter.domain.strict_model import StrictModel

__all__ = [
    "BENCH_HEADLINE",
    "COLUMN_GRADE",
    "BandedValue",
    "BankPartition",
    "BenchColumn",
    "BenchColumns",
    "BenchPass",
    "BenchPasses",
    "BenchReading",
    "BenchScore",
    "BenchSubject",
    "DatasetSplit",
    "partition_bank",
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
        "`shot_ids` name, rendered into its prompt as query and ground truth, never scored.",
    )
    seed: int = Field(
        0,
        description="Seeds which rows fall where. Membership ranks each row by its content "
        "(`Sample.key`), never its slot, so a reordered bank holds out the same rows.",
    )
    tolerance: int = Field(
        0,
        ge=0,
        description="Bench rows a pass may end with no verdict on — a provider fault, or a cell "
        "the formula cannot grade — and still be a reading. Past it the pass reads nothing: a "
        "headline over a population other than the one sent is not the bench score.",
    )


@dataclass(frozen=True)
class BankPartition:
    split: DatasetSplit | None
    search: tuple[Sample, ...]
    bench: tuple[Sample, ...]
    demo: tuple[Sample, ...]

    @property
    def admitted_ids(self) -> frozenset[int] | None:
        """The ids an optimizer's archive views may read — ``None`` when nothing is held out, so
        an L4 cell drawn from a bank still reads every row its dataset banked."""
        return None if self.split is None else frozenset(s.id for s in self.search)


def partition_bank(bank: Sequence[Sample], split: DatasetSplit | None) -> BankPartition:
    """Each part keeps the bank's own order, so a prefix of the search pool is still the draw
    ``sample_dataset`` promises. No split declared ⇒ the whole bank is the search pool. The split
    ranks DISTINCT samples, so every copy of one lands on the side the sample does. A ``bench_only``
    row is bench beside the ranked ``split.bench`` and is never ranked, so it moves no other row."""
    declared = {s.key for s in bank if s.bench_only}
    if split is None:
        if declared:
            raise ValueError(
                f"{len(declared)} samples of this bank are bench-only and no dataset_split is "
                "declared, so the search would draw rows the dataset holds out."
            )
        return BankPartition(split=None, search=tuple(bank), bench=(), demo=())
    if repeats := sorted(s.id for s in bank if not s.bench_only and s.key in declared):
        raise ValueError(
            f"rows {repeats} repeat the content of a bench-only row, so one sample would sit on "
            "both sides of the split."
        )
    keys = sorted(
        {s.key for s in bank} - declared,
        key=lambda k: hashlib.sha256(f"{split.seed}:{k}".encode()).digest(),
    )
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
        search=tuple(s for s in bank if s.key not in bench_keys and s.key not in demo_keys),
        bench=tuple(s for s in bank if s.key in bench_keys),
        demo=tuple(s for s in bank if s.key in demo_keys),
    )


# Whose pass over the bench set it is: the campaign's origin, or the selection it is graded against.
BenchSubject = Literal["origin", "selected"]


class BenchPass(StrictModel):
    """One individual's pass over the bench set, as the facts it banked — never a grade of them."""

    model_config = ConfigDict(frozen=True)

    round: int
    sp_hash: str
    # `None` where the pass stopped before the gateway filed a run.
    run_id: str | None
    sample_ids: list[int]
    # Why it ended before its last row; `None` once it sent every one.
    stopped: str | None
    # The grader it ran under, which stamped its live reading — a reader re-grades under its own.
    scorer_id: str


class BenchPasses(StrictModel):
    """What a campaign's headline is read off: the origin's pass, the selection's, and how many rows
    either may end with no verdict. ``runner/bench.py::read_bench`` is the one reading of them."""

    model_config = ConfigDict(frozen=True)

    tolerance: int
    origin: BenchPass
    # What the origin's pass incurred, replays priced: set aside at every launch so the selection's
    # pass still fits under the ceiling the search spends against.
    reserve_usd: float
    reserve_tokens: int
    # `None` until the selection is graded: the origin's pass is banked before any search.
    selected: BenchPass | None


BenchColumn = Literal["accuracy", "composite"]

# The column every bench headline is read in, for every optimizer. The other one rides beside it.
BENCH_HEADLINE: BenchColumn = "accuracy"

# Which per-row grade each column is a mean of.
COLUMN_GRADE: dict[BenchColumn, CellGrade] = {"accuracy": "fitness", "composite": "objective"}


class BandedValue(StrictModel):
    model_config = ConfigDict(frozen=True)

    value: float
    ci_lo: float | None = Field(
        description="The 95% band on `value`, drawn from the same per-row values; `None` where "
        "one pass was read twice, which has no spread."
    )
    ci_hi: float | None


class BenchColumns(StrictModel):
    """One bench quantity in both columns, read off the same rows."""

    model_config = ConfigDict(frozen=True)

    accuracy: BandedValue | None = Field(description="The hit rate.")
    composite: BandedValue | None = Field(
        description="Under the reading scorer's formula, which charges cost and length — so it is "
        "never the change in the hit rate."
    )

    def of(self, column: BenchColumn) -> BandedValue | None:
        value: BandedValue | None = getattr(self, column)
        return value


class BenchReading(BenchColumns):
    """One individual's bench pass, read under a named scorer."""

    round: int = Field(description="The round whose selection this is; 0 is the origin.")
    sp_hash: str = Field(description="The searchpoint scored — the archive's `prompt_fields_id`.")
    headline: BenchColumn = Field(description="Which column the headline reads.")
    n_scored: int = Field(
        description="Bench rows carrying a verdict — a miss the prompt caused included — never "
        "fewer than the bench set less its split's `tolerance`."
    )

    @property
    def level(self) -> BandedValue | None:
        return self.of(self.headline)


class BenchScore(StrictModel):
    """The headline: the selection and the origin, scored on a bench set no optimizer node read."""

    model_config = ConfigDict(frozen=True)

    bench_size: int
    scorer_id: str = Field(
        description="The grader every number here was read under. A stored copy is a cache of that "
        "reading: a reader under another grader reads the passes again, never this."
    )
    headline: BenchColumn = Field(
        description="Which column the headline reads, on the readings and on `lift` alike."
    )
    origin: BenchReading | None = Field(
        description="`None` where its pass read nothing; `missing_reason` says why."
    )
    selected: BenchReading | None = Field(
        description="The headline. `None` where its pass read nothing; `missing_reason` says why."
    )
    missing_reason: str | None = Field(
        description="Why a reading above is `None`: each pass that stopped before its last row, "
        "or ended past its split's `tolerance` of rows with no verdict — and, for `selected`, a "
        "line that closed no round and so selected nothing. `None` when both read."
    )
    lift: BenchColumns = Field(
        description="`selected` over `origin`, paired per bench row both scored. A column is "
        "`None` below two shared rows, and 0.0 with no band where the origin is the selection."
    )

    @property
    def headline_lift(self) -> BandedValue | None:
        return self.lift.of(self.headline)

    def lift_per_usd(self, spend: SpendRollup) -> float | None:
        """The headline priced in what its SEARCH incurred, never billed — a replayed cell is billed
        nothing, which would price arriving second — and never the bench's own pass."""
        lift, usd = self.headline_lift, spend.search_incurred_usd
        return None if lift is None or usd is None or usd <= 0.0 else lift.value / usd
