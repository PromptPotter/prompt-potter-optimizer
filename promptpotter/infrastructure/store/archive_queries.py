"""Where a run files answers and its memory scope is applied; maintenance rewrites the archive itself."""

from __future__ import annotations

from collections.abc import Iterable
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from pydantic import ConfigDict, Field

from promptpotter.domain.results import CellFold
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.io import write_jsonl
from promptpotter.infrastructure.store.measurement_archive import (
    ANSWER_KEY,
    answer_facts,
    standing,
)
from promptpotter.infrastructure.store.read_model import iter_jsonl
from promptpotter.shared.measurement_context import MeasurementRole

if TYPE_CHECKING:
    from pathlib import Path

    from promptpotter.domain.scoring import WalkedCell
    from promptpotter.infrastructure.store.stores import Stores

__all__ = [
    "SampleFoldRow",
    "bench_reads",
    "list_populations",
    "load_population",
    "memory_scoped",
    "note_walked",
    "population_signatures",
    "read_answer",
    "sample_fold_rows",
    "scope_memory_to_own_answers",
    "walked_answers",
    "write_sample_fold",
]


# CACHE (`ReplayFeed`) is NEVER filtered; MEMORY reads fence a controlled line to its own walks.
_OWN_ANSWERS: ContextVar[set[str] | None] = ContextVar("own_answers", default=None)


def scope_memory_to_own_answers(answers: set[str]) -> None:
    """Called once at the runner seam of a controlled line, INSIDE its own task."""
    _OWN_ANSWERS.set(set(answers))


def memory_scoped() -> bool:
    return _OWN_ANSWERS.get() is not None


def note_walked(answer: str) -> None:
    if (own := _OWN_ANSWERS.get()) is not None:
        own.add(answer)


def _remembered(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    own = _OWN_ANSWERS.get()
    return rows if own is None else [row for row in rows if row[ANSWER_KEY] in own]


def read_answer(stores: Stores, answer: str) -> dict[str, Any] | None:
    """A fresh dict: a reader grades it in place."""
    stored = stores.archive.answer(answer)
    return None if stored is None else dict(stored)


def walked_answers(stores: Stores, cells: Iterable[WalkedCell]) -> list[dict[str, Any]]:
    """``sample_id`` is the WALK's: an answer filed by another dataset holds that dataset's slot."""
    rows: list[dict[str, Any]] = []
    for _, sample_id, answer, _ in cells:
        if (stored := stores.archive.answer(answer)) is not None:
            rows.append({**answer_facts(stored), "sample_id": sample_id})
    return rows


def bench_reads(stores: Stores, *, dataset_name: str, sample_ids: frozenset[int]) -> int:
    """Never memory-scoped: a holdout was spent by every read, seen or not."""
    graded: set[str] = set()
    for entry in stores.archive.list_all(dataset_name=dataset_name):
        if any(
            row.get("role") == MeasurementRole.BENCH and row.get("sample_id") in sample_ids
            for row in stores.archive.population(entry)
        ):
            graded.add(str(entry.get("prompt_fields_id") or entry["config_key"]))
    return len(graded)


def load_population(stores: Stores, entry: dict[str, Any]) -> dict[str, Any] | None:
    """A fresh dict per row: a reader grades them in place."""
    rows = _remembered(stores.archive.population(entry))
    if not rows:
        return None
    return {**entry, "measurements": [dict(row) for row in standing(rows).values()]}


def list_populations(stores: Stores, *, dataset_name: str | None = None) -> list[dict[str, Any]]:
    entries = stores.archive.list_all(dataset_name=dataset_name)
    if not memory_scoped():
        return entries
    return [e for e in entries if _remembered(stores.archive.population(e))]


def population_signatures(
    stores: Stores, *, dataset_name: str | None = None
) -> dict[str, list[list[Any]]]:
    sigs = stores.archive.cell_signatures()
    out: dict[str, list[list[Any]]] = {}
    for entry in stores.archive.list_all(dataset_name=dataset_name):
        held = stores.archive.signature(entry, sigs) or ()
        out[entry["config_key"]] = [list(part) for part in held]
    return out


def _sample_fold_path(stores: Stores, dataset_name: str) -> Path:
    return stores.archive.derived_dir() / f"sample_fold__{dataset_name}.jsonl"


class SampleFoldRow(StrictModel):
    model_config = ConfigDict(frozen=True)

    config_key: str
    sp: str
    fk: str
    sig: list[list[Any]]
    # ``(sample_id, objective, provenance)`` per graded cell.
    graded: list[tuple[int, float, str]]
    unscoreable: bool = False
    # ``(sample_id, hit, degraded, failure_mode)`` per cell, in walk order.
    cells: list[tuple[int, bool, bool, str | None]] = Field(default_factory=list)
    # ``(sample_id, query, ground_truth)`` per sample this row was first to introduce.
    new_samples: list[tuple[int, str, str]] = Field(default_factory=list)
    reading: CellFold | None = None


def sample_fold_rows(stores: Stores, *, dataset_name: str) -> list[SampleFoldRow]:
    """A controlled line reads none: the fold is every campaign's."""
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
