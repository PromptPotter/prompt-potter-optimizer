"""The bench's half of a campaign: the held-out partition of the bank, and the score it reads there.
Contract: ``docs/architecture.md`` § The bench score is not an optimizer's selection."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import ConfigDict, Field

from promptpotter.domain.sample import Sample
from promptpotter.domain.spend import SpendRollup
from promptpotter.domain.strict_model import StrictModel

__all__ = [
    "BankPartition",
    "BenchPass",
    "BenchPasses",
    "BenchReading",
    "BenchScore",
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
    ranks DISTINCT samples, so every copy of one lands on the side the sample does."""
    if split is None:
        return BankPartition(split=None, search=tuple(bank), bench=(), demo=())
    keys = sorted(
        {s.key for s in bank}, key=lambda k: hashlib.sha256(f"{split.seed}:{k}".encode()).digest()
    )
    held = split.bench + split.demo
    if held >= len(keys):
        raise ValueError(
            f"dataset_split holds out {held} of the {len(keys)} distinct samples in a "
            f"{len(bank)}-row bank (bench {split.bench}, demo {split.demo}), which leaves the "
            "search none to draw."
        )
    bench_keys = set(keys[: split.bench])
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
    selected: BenchPass


class BenchReading(StrictModel):
    """One individual's bench pass, read under a named scorer."""

    model_config = ConfigDict(frozen=True)

    round: int = Field(description="The round whose selection this is; 0 is the origin.")
    sp_hash: str = Field(description="The searchpoint scored — the archive's `prompt_fields_id`.")
    accuracy: float | None
    composite_fitness: float | None = Field(
        description="Under the reading scorer's formula — the number the headline reads."
    )
    ci_lo: float | None = Field(
        description="The 95% band on `composite_fitness`, drawn from the same per-row values."
    )
    ci_hi: float | None
    n_scored: int = Field(
        description="Bench rows carrying a verdict — a miss the prompt caused included — never "
        "fewer than the bench set less its split's `tolerance`."
    )


class BenchScore(StrictModel):
    """The headline: the selection and the origin, scored on a bench set no optimizer node read."""

    model_config = ConfigDict(frozen=True)

    bench_size: int
    scorer_id: str = Field(
        description="The grader every number here was read under. A stored copy is a cache of that "
        "reading: a reader under another grader reads the passes again, never this."
    )
    origin: BenchReading | None = Field(
        description="`None` where its pass read nothing; `missing_reason` says why."
    )
    selected: BenchReading | None = Field(
        description="The headline. `None` where its pass read nothing; `missing_reason` says why."
    )
    missing_reason: str | None = Field(
        description="Why a reading above is `None`: each pass that stopped before its last row, "
        "or ended past its split's `tolerance` of rows with no verdict. `None` when both read."
    )
    lift: float | None = Field(
        description="`selected` over `origin` in `composite_fitness`, paired per bench row both "
        "scored; `None` below two shared rows, and 0.0 where the origin is the selection."
    )
    lift_ci_lo: float | None
    lift_ci_hi: float | None

    def lift_per_usd(self, spend: SpendRollup) -> float | None:
        """The headline priced in what its SEARCH incurred, never billed — a replayed cell is billed
        nothing, which would price arriving second — and never the bench's own pass."""
        usd = spend.search_incurred_usd
        return None if self.lift is None or usd is None or usd <= 0.0 else self.lift / usd
