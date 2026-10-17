"""Where a run files answers and its memory scope is applied; maintenance rewrites the archive itself."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from contextvars import ContextVar
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

from pydantic import ConfigDict, Field

from promptpotter.domain.results import CellFold
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.campaign_store.ledger_scan import scan_ledger_answers
from promptpotter.infrastructure.store.io import write_jsonl
from promptpotter.infrastructure.store.measurement_archive import standing
from promptpotter.infrastructure.store.read_model import LedgerSpan, iter_jsonl
from promptpotter.shared.clock import epoch_seconds
from promptpotter.shared.measurement_context import MeasurementRole

if TYPE_CHECKING:
    from promptpotter.domain.sample import ArchiveEntry, FiledAnswer
    from promptpotter.domain.scoring import MeasuredCell, WalkedCell
    from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
    from promptpotter.infrastructure.store.read_model import Moment
    from promptpotter.infrastructure.store.stores import Stores

__all__ = [
    "CampaignWalks",
    "FiledBy",
    "SampleFoldRow",
    "as_of_moment",
    "bench_reads",
    "campaign_fenced",
    "fence_memory",
    "list_populations",
    "load_population",
    "memory_scoped",
    "population_signatures",
    "sample_fold_rows",
    "walked_answers",
    "write_sample_fold",
]


class FiledBy(NamedTuple):
    """Every campaign's answers, as the archive held them at *moment*."""

    moment: Moment


class CampaignWalks(NamedTuple):
    """One campaign's walks, read when memory is: a cycle it forks after the fence is its own too."""

    campaigns: CampaignStore
    campaign_id: str
    moment: Moment | None

    def spans(self) -> list[LedgerSpan]:
        held = self.campaigns.campaign_walk_spans(self.campaign_id)
        return held if self.moment is None else self.moment.spans(held)


# MEMORY reads fence an arm, or an instrument's cell, to its campaign's walks, and any other task
# re-read at a past moment to what was filed by then.
_FENCE: ContextVar[CampaignWalks | FiledBy] = ContextVar("memory_fence")


def fence_memory(fence: CampaignWalks | FiledBy) -> None:
    """Inside the task it scopes."""
    _FENCE.set(fence)


def memory_scoped() -> bool:
    return _FENCE.get(None) is not None


def campaign_fenced() -> bool:
    """An arm, or an instrument's cell: no other campaign is its memory."""
    return isinstance(_FENCE.get(None), CampaignWalks)


def as_of_moment() -> Moment | None:
    """The moment this task re-reads the world at — its ledgers as its archive; ``None`` is now."""
    fence = _FENCE.get(None)
    return None if fence is None else fence.moment


def _remembered() -> Callable[[FiledAnswer], bool] | None:
    """An open span grows with its ledger, so a walk is remembered once its record lands."""
    fence = _FENCE.get(None)
    if fence is None:
        return None
    if isinstance(fence, FiledBy):
        instant = fence.moment.instant
        return lambda answer: (at := epoch_seconds(answer.created_at)) is not None and at <= instant
    own = scan_ledger_answers(fence.spans())
    return lambda answer: answer.answer in own


def walked_answers(stores: Stores, cells: Iterable[WalkedCell]) -> list[MeasuredCell]:
    """``sample_id`` is the WALK's: an answer filed by another dataset holds that dataset's slot."""
    walked: list[MeasuredCell] = []
    for _, sample_id, answer, _ in cells:
        if (stored := stores.archive.answer(answer)) is not None:
            walked.append(replace(stored.cell, sample_id=sample_id))
    return walked


def bench_reads(stores: Stores, *, dataset_name: str, sample_ids: frozenset[int]) -> int:
    """Never memory-scoped: a holdout was spent by every read, seen or not."""
    graded: set[str] = set()
    for entry in stores.archive.list_all(dataset_name=dataset_name):
        if any(
            answer.role == MeasurementRole.BENCH and answer.cell.sample_id in sample_ids
            for answer in stores.archive.population(entry)
        ):
            graded.add(entry.individual)
    return len(graded)


def load_population(stores: Stores, entry: ArchiveEntry) -> list[FiledAnswer]:
    answers = stores.archive.population(entry)
    if (remembered := _remembered()) is not None:
        answers = [answer for answer in answers if remembered(answer)]
    return list(standing(answers).values())


def list_populations(stores: Stores, *, dataset_name: str | None = None) -> list[ArchiveEntry]:
    entries = stores.archive.list_all(dataset_name=dataset_name)
    if (remembered := _remembered()) is None:
        return entries
    return [e for e in entries if any(remembered(a) for a in stores.archive.population(e))]


def population_signatures(
    stores: Stores, *, dataset_name: str | None = None
) -> dict[str, list[list[Any]]]:
    sigs = stores.archive.cell_signatures()
    out: dict[str, list[list[Any]]] = {}
    for entry in stores.archive.list_all(dataset_name=dataset_name):
        held = stores.archive.signature(entry, sigs) or ()
        out[entry.config_key] = [list(part) for part in held]
    return out


def _sample_fold_path(stores: Stores, dataset_name: str) -> Path:
    return stores.archive.derived_dir() / f"sample_fold__{dataset_name}.jsonl"


class SampleFoldRow(StrictModel):
    model_config = ConfigDict(frozen=True)

    config_key: str
    sp: str
    fk: str
    sig: list[list[Any]]
    # ``(Sample.key, objective, provenance)`` per graded cell.
    graded: list[tuple[str, float, str]]
    unscoreable: bool = False
    # ``(sample_id, hit, degraded, failure_mode)`` per cell, in walk order.
    cells: list[tuple[int, bool, bool, str | None]] = Field(default_factory=list)
    # ``(sample_id, query, ground_truth)`` per sample this row was first to introduce.
    new_samples: list[tuple[int, str, str]] = Field(default_factory=list)
    reading: CellFold | None = None


def sample_fold_rows(stores: Stores, *, dataset_name: str) -> list[SampleFoldRow]:
    """A fenced task reads none: the fold is every campaign's, at every moment."""
    if memory_scoped():
        return []
    return [
        SampleFoldRow.model_validate(row)
        for row in iter_jsonl(_sample_fold_path(stores, dataset_name))
    ]


def write_sample_fold(stores: Stores, *, dataset_name: str, rows: Iterable[SampleFoldRow]) -> None:
    if memory_scoped():
        return
    write_jsonl(
        _sample_fold_path(stores, dataset_name), (row.model_dump(mode="json") for row in rows)
    )
