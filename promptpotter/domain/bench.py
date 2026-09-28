"""The bench's half of a campaign: the held-out partition of the bank, and the score it reads there.
Contract: ``docs/architecture.md`` § The bench score is not an optimizer's selection."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import ConfigDict, Field

from promptpotter.domain.sample import Sample
from promptpotter.domain.strict_model import StrictModel

__all__ = [
    "BankPartition",
    "BenchReading",
    "BenchScore",
    "DatasetSplit",
    "partition_bank",
]


class DatasetSplit(StrictModel):
    bench: int = Field(
        ge=0,
        description="Rows held out as the bench set: no optimizer node ever reads one, and the "
        "headline is scored on them.",
    )
    demo: int = Field(
        0,
        ge=0,
        description="Rows reserved as the demo pool — the rows an individual's `shot_ids` name, "
        "rendered into its prompt as query and ground truth, never scored.",
    )
    seed: int = Field(
        0,
        description="Seeds which rows fall where. Membership ranks each row by its content "
        "(`Sample.key`), never its slot, so a reordered bank holds out the same rows.",
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
    ``sample_dataset`` promises. No split declared ⇒ the whole bank is the search pool."""
    if split is None:
        return BankPartition(split=None, search=tuple(bank), bench=(), demo=())
    held = split.bench + split.demo
    if held >= len(bank):
        raise ValueError(
            f"dataset_split holds out {held} of a {len(bank)}-row bank (bench {split.bench}, "
            f"demo {split.demo}), which leaves the search no rows to draw."
        )
    ranked = sorted(bank, key=lambda s: hashlib.sha256(f"{split.seed}:{s.key}".encode()).digest())
    bench_ids = {s.id for s in ranked[: split.bench]}
    demo_ids = {s.id for s in ranked[split.bench : held]}
    if unlabelled := sorted(s.id for s in bank if s.id in demo_ids and s.ground_truth is None):
        raise ValueError(
            f"demo rows {unlabelled} carry no ground truth, so they cannot render as a shot: a "
            "verifier-graded bank declares no demo pool."
        )
    return BankPartition(
        split=split,
        search=tuple(s for s in bank if s.id not in bench_ids and s.id not in demo_ids),
        bench=tuple(s for s in bank if s.id in bench_ids),
        demo=tuple(s for s in bank if s.id in demo_ids),
    )


class BenchReading(StrictModel):
    """One individual scored on the whole bench set under the campaign's formula."""

    model_config = ConfigDict(frozen=True)

    round: int = Field(description="The round whose selection this is; 0 is the origin.")
    sp_hash: str = Field(description="The searchpoint scored — the archive's `prompt_fields_id`.")
    accuracy: float | None
    composite_fitness: float | None = Field(
        description="Under the campaign's formula — the number the headline reads."
    )
    ci_lo: float | None = Field(
        description="The 95% band on `composite_fitness`, drawn from the same per-row values."
    )
    ci_hi: float | None
    n_scored: int = Field(description="Bench rows that carry a verdict; an errored row never does.")
    run_id: str = Field(description="The archive run its bench rows were filed under.")
    stopped: str | None = Field(
        description="`skip` where the operator ended the pass before its last bench row, or "
        "`None` when it scored every one."
    )


class BenchScore(StrictModel):
    """The headline: the selection and the origin, scored on a bench set no optimizer node read."""

    model_config = ConfigDict(frozen=True)

    bench_size: int
    origin: BenchReading | None = Field(
        description="`None` where its pass stopped short; `missing_reason` says why."
    )
    selected: BenchReading | None = Field(
        description="The headline. `None` where its pass stopped short; `missing_reason` says why."
    )
    missing_reason: str | None = Field(
        description="Why a reading above is `None`: each pass that stopped short, with the stop "
        "and the error it stopped on. `None` when both passes read."
    )
    lift: float | None = Field(
        description="`selected` over `origin` in `composite_fitness`, paired per bench row both "
        "scored; `None` below two shared rows, and 0.0 where the origin is the selection."
    )
    lift_ci_lo: float | None
    lift_ci_hi: float | None
