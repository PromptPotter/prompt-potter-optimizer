"""Where a run files answers and its memory scope is applied; maintenance rewrites the archive itself."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple, assert_never

from pydantic import Field

from promptpotter.domain.cell_fold import CellFold
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.infrastructure.store.campaign_store.ledger_scan import scan_ledger_answers
from promptpotter.infrastructure.store.io import write_jsonl
from promptpotter.infrastructure.store.measurement_archive import standing
from promptpotter.infrastructure.store.read_model import LedgerSpan, iter_jsonl
from promptpotter.shared.measurement_context import MeasurementRole

if TYPE_CHECKING:
    from promptpotter.domain.ruler import RulerReach
    from promptpotter.domain.sample import ArchiveEntry, Bank, FiledAnswer
    from promptpotter.domain.scoring import MeasuredCell, WalkedCell
    from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
    from promptpotter.infrastructure.store.stores import Stores


class CampaignWalks(NamedTuple):
    """One campaign's walks, read when memory is: a cycle it forks after the scope is its own too."""

    campaigns: CampaignStore
    campaign_id: str

    def spans(self) -> list[LedgerSpan]:
        return self.campaigns.campaign_walk_spans(self.campaign_id)


class OpenAt(NamedTuple):
    """Optimize: every campaign's answers as filed, and the ruler version at each round's open."""


class ArmOf(NamedTuple):
    """Compare: its campaign's walks alone, and the ruler its head-to-head froze at open plus its
    own campaign's lines since."""

    walks: CampaignWalks
    ruler_version: int


class CellOf(NamedTuple):
    """An L4 cell remembers its campaign's walks alone and reads its spawner's round reach."""

    walks: CampaignWalks
    ruler: RulerReach


type MemoryScope = OpenAt | ArmOf | CellOf

# Unbound reads the archive whole: a served read outside every run.
_UNBOUND = OpenAt()
_SCOPE: ContextVar[MemoryScope] = ContextVar("memory_scope", default=_UNBOUND)


def bind_memory_scope(scope: MemoryScope) -> None:
    """Inside the task it scopes."""
    _SCOPE.set(scope)


def memory_scope() -> MemoryScope:
    return _SCOPE.get()


def _remembered() -> Callable[[FiledAnswer], bool] | None:
    """An open span grows with its ledger, so a walk is remembered once its record lands."""
    match scope := _SCOPE.get():
        case OpenAt():
            return None
        case ArmOf(walks=walks) | CellOf(walks=walks):
            own = scan_ledger_answers(walks.spans())
            return lambda answer: answer.answer in own
        case _:
            assert_never(scope)


def every_campaign_now() -> bool:
    """Whether this task's memory is the whole archive as it stands: the one a shared fold serves."""
    match scope := _SCOPE.get():
        case OpenAt():
            return True
        case ArmOf() | CellOf():
            return False
        case _:
            assert_never(scope)


def walked_answers(stores: Stores, cells: Iterable[WalkedCell]) -> list[MeasuredCell]:
    return [
        stored.cell
        for _, answer, _ in cells
        if (stored := stores.archive.answer(answer)) is not None
    ]


def _configurations_on(stores: Stores, bank: Bank, keys: frozenset[str]) -> list[ArchiveEntry]:
    """One entry per configuration filed under any bank measuring *keys*: history is by content."""
    banks = {bank.dataset_hash} | stores.tenant_datasets.banks_sharing(keys)
    first: dict[str, ArchiveEntry] = {}
    for entry in stores.archive.list_all():
        if entry.dataset_hash in banks:
            first.setdefault(entry.config_key, entry)
    return list(first.values())


def bench_reads(stores: Stores, *, bank: Bank, sample_keys: frozenset[str]) -> int:
    """Never memory-scoped, under either purpose: a holdout was spent by every read, on every bank."""
    graded: set[str] = set()
    for entry in _configurations_on(stores, bank, sample_keys):
        if any(
            answer.role == MeasurementRole.BENCH
            for answer in stores.archive.measured(entry, sample_keys)
        ):
            graded.add(entry.individual)
    return len(graded)


def load_population(stores: Stores, entry: ArchiveEntry, bank: Bank) -> list[FiledAnswer]:
    """The configuration's answers on *bank*'s rows, whichever bank measured them."""
    answers = stores.archive.measured(entry, frozenset(bank.slot_of))
    if (remembered := _remembered()) is not None:
        answers = [answer for answer in answers if remembered(answer)]
    return list(standing(answers).values())


def list_populations(stores: Stores, bank: Bank) -> list[ArchiveEntry]:
    entries = _configurations_on(stores, bank, frozenset(bank.slot_of))
    if _remembered() is None:
        return entries
    return [e for e in entries if load_population(stores, e, bank)]


def population_signatures(stores: Stores, bank: Bank) -> dict[str, list[list[Any]]]:
    sigs = stores.archive.cell_signatures()
    out: dict[str, list[list[Any]]] = {}
    for entry in _configurations_on(stores, bank, frozenset(bank.slot_of)):
        held = stores.archive.signature(entry, sigs) or ()
        out[entry.config_key] = [list(part) for part in held]
    return out


class SampleFoldRow(FrozenModel):
    config_key: str
    sp: str
    fk: str
    sig: list[list[Any]]
    # ``(Sample.key, objective, provenance)`` per graded cell.
    graded: list[tuple[str, float, str]]
    unscoreable: bool = False
    # ``(slot, hit, degraded, failure_mode)`` per cell, in walk order; a slot in the fold's own bank.
    cells: list[tuple[int, bool, bool, str | None]] = Field(default_factory=list)
    # ``(slot, query, ground_truth)`` per sample this row was first to introduce.
    new_samples: list[tuple[int, str, str]] = Field(default_factory=list)
    reading: CellFold | None = None


def read_sample_fold(path: Path) -> list[SampleFoldRow]:
    return [SampleFoldRow.model_validate(row) for row in iter_jsonl(path)]


def sample_fold_rows(stores: Stores, *, dataset_hash: str) -> list[SampleFoldRow]:
    """Every campaign's, as it stands: a task remembering less reads none."""
    if not every_campaign_now():
        return []
    return read_sample_fold(stores.archive.sample_fold_path(dataset_hash))


def write_sample_fold(stores: Stores, *, dataset_hash: str, rows: Iterable[SampleFoldRow]) -> None:
    if not every_campaign_now():
        return
    write_jsonl(
        stores.archive.sample_fold_path(dataset_hash),
        (row.model_dump(mode="json") for row in rows),
    )
