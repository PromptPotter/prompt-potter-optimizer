from __future__ import annotations

import pathlib
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from functools import partial
from typing import TYPE_CHECKING, Any

from pydantic import Field, computed_field

from promptpotter.domain.scoring import UNREAD_PIPELINE_KEYS, PipelineData
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.identity.migration import is_host
from promptpotter.infrastructure.runtime_flags import derive_run_state
from promptpotter.infrastructure.store.layout import inner_sandboxes_dir
from promptpotter.infrastructure.store.measurement_archive import (
    CellRewrite,
    MovedFields,
    answer_line,
    answer_of,
    cold_size,
    rejoined,
)
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import NotFoundError, PayloadInvalidError, graceful
from promptpotter.shared.measurement_context import MeasurementRole

if TYPE_CHECKING:
    from promptpotter.domain.sample import FiledAnswer
    from promptpotter.infrastructure.store.stores import Stores

__all__ = [
    "ArchiveInventory",
    "ArchiveReport",
    "InventoryRow",
    "archive_writers",
    "compact_measurement_archive",
    "inventory_measurement_archive",
    "iter_cycle_ledgers",
    "purge_cold_store",
    "reindex_measurement_archive",
    "restore_measurement_archive",
    "workspace_trees",
]


def workspace_trees(root: pathlib.Path) -> list[pathlib.Path]:
    """Inner sandboxes are a SIBLING tree, not a subtree: a per-level glob silently misses them."""
    if not root.is_dir():
        return []
    trees = [root]
    inner = inner_sandboxes_dir(root)
    if inner.is_dir():
        trees.extend(p for p in inner.iterdir() if p.is_dir())
    return trees


def iter_cycle_ledgers(root: pathlib.Path) -> list[pathlib.Path]:
    return [
        p for tree in workspace_trees(root) for p in sorted(tree.glob("**/.runtime/ledger.jsonl"))
    ]


def archive_writers(root: pathlib.Path) -> int:
    n = 0
    for ledger_path in iter_cycle_ledgers(root):
        if derive_run_state(ledger_path.parent.parent).producer.attached:
            n += 1
    return n


_ELIGIBLE_ROLE = MeasurementRole.PANEL
"""Only an answer a candidate's own walk filed compacts: the origin's and the parent's are the
ones every later campaign replays. An answer under any other role is SKIPPED and counted by role,
never compacted on a guess."""


# `panels.py::_inner_narrated` reads an L4 row's trace.
_READ_ON_L4_ROWS = frozenset({"reasoning_trace"})
assert _READ_ON_L4_ROWS <= UNREAD_PIPELINE_KEYS


def _protected_pipeline_fields(pipeline: PipelineData) -> frozenset[str]:
    return _READ_ON_L4_ROWS if pipeline.inner is not None else frozenset()


def _parsed(lines: Iterable[str]) -> list[tuple[str, FiledAnswer | None]]:
    return [(line, answer_of(line)) for line in lines]


def _in_scope(answer: FiledAnswer, dataset_hash: str | None) -> bool:
    return dataset_hash is None or answer.dataset_hash == dataset_hash


def _scope(stores: Stores, label: str | None) -> str | None:
    if label is None:
        return None
    digest = stores.tenant_datasets.hash_of(label)
    if digest is None:
        raise NotFoundError(f"no dataset is labelled {label!r}, so it scopes no measurement")
    return digest


def _moved(answer: FiledAnswer) -> bool:
    return answer.compaction is not None and answer.purged is None


def _size(lines: Iterable[str]) -> int:
    return sum(len(line.encode("utf-8")) for line in lines)


class ArchiveReport(StrictModel):
    """What a pass did, or would do.

    A model rather than a dataclass because it IS the wire shape: the API answers it directly and
    the browser's type is generated from it, so a preview and an apply cannot be described in two
    different vocabularies. ``applied`` is the only field that separates the two."""

    files_touched: int = 0
    rows_moved: int = 0
    bytes_before: int = 0
    bytes_after: int = 0
    cold_bytes: int = 0
    archive_writers: int = 0
    conflicts: int = 0
    purged: int = 0
    """Answers whose moved payload is gone for good.

    Counted apart from the answers a pass left alone: "this was measured and we dropped its
    payload deliberately" and "this was never compacted" are different facts, and collapsing them
    is how a purge starts reading as if it never happened."""
    rows_skipped_by_role: Mapping[str, int] = Field(default_factory=dict)
    applied: bool = False
    notes: tuple[str, ...] = Field(
        default=(),
        description="What the counts mean where they are not a plain success — the refusal while "
        "a cycle can still append, files refused, answers already purged — worded once for the "
        "terminal and the browser.",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def bytes_freed(self) -> int:
        """Net, and net is the honest number: the payload does not vanish, it moves to the cold
        store, so the hot-side saving is reported against what the cold side cost."""
        return self.bytes_before - self.bytes_after - self.cold_bytes


def _notes(*, conflicts: int = 0, unrestorable: int = 0) -> tuple[str, ...]:
    notes = []
    if conflicts:
        notes.append(
            f"{conflicts} cell file(s) refused — the cold payload names an answer the file no "
            "longer holds, so nothing was touched rather than half of it."
        )
    if unrestorable:
        notes.append(
            f"{unrestorable} answer(s) were purged on purpose — nothing to put back. They still "
            "measure difficulty and still serve a cache hit."
        )
    return tuple(notes)


def _blocked(writers: int) -> ArchiveReport:
    return ArchiveReport(
        archive_writers=writers,
        notes=(
            f"{writers} cycle(s) can still append to the shared archive, so nothing was read or "
            "written — a rewrite could lose a row one is landing. Pause and try again.",
        ),
    )


_AGE_BANDS: tuple[tuple[str, float], ...] = (
    ("0-7d", 7.0),
    ("7-30d", 30.0),
    ("30-90d", 90.0),
    ("90d+", float("inf")),
)
_UNDATED_BAND = "undated"


def _age_band(created_at: str, *, now: datetime) -> str:
    try:
        age_days = (now - datetime.fromisoformat(created_at)).total_seconds() / 86400.0
    except (TypeError, ValueError):
        return _UNDATED_BAND
    return next(band for band, upper in _AGE_BANDS if age_days < upper)


def _role_family(role: str) -> str:
    head, _, tail = role.rpartition("_")
    return head if head and tail.isdigit() else role


class InventoryRow(StrictModel):
    """One cut of the archive — a dataset, a role family, or an age band."""

    key: str
    configurations: int
    answers: int
    hot_bytes: int
    cold_bytes: int


@dataclass(slots=True)
class _Tally:
    configurations: set[str] = field(default_factory=set)
    answers: int = 0
    hot: int = 0
    cold: float = 0.0

    def add(self, *, configuration: str, hot: int, cold: float) -> None:
        self.configurations.add(configuration)
        self.answers += 1
        self.hot += hot
        self.cold += cold

    def row(self, key: str) -> InventoryRow:
        return InventoryRow(
            key=key,
            configurations=len(self.configurations),
            answers=self.answers,
            hot_bytes=self.hot,
            cold_bytes=round(self.cold),
        )


class ArchiveInventory(StrictModel):
    """What the archive HOLDS, cut three ways — never what a pass would do to it.

    Apart from :class:`ArchiveReport` because the two answer different questions: that one is a
    plan over the answers a mode is eligible to touch, this one is a census over every answer
    there is."""

    by_dataset: list[InventoryRow]
    by_role: list[InventoryRow]
    by_age: list[InventoryRow]
    total: InventoryRow
    orphan_index_rows: int = 0
    """Index rows no answer stands behind.

    Such a row can be neither replayed nor read, so it is a claim rather than a measurement.
    `reindex` is what clears them."""
    archive_writers: int = 0
    """Cycles that can still append. REPORTED, never a refusal: this pass writes nothing, and the
    numbers are wanted most while a campaign is still filling the store."""


def _by_bytes(tallies: Mapping[str, _Tally]) -> list[InventoryRow]:
    rows = [tally.row(key) for key, tally in tallies.items()]
    rows.sort(key=lambda r: r.hot_bytes + r.cold_bytes, reverse=True)
    return rows


def _in_band_order(tallies: Mapping[str, _Tally]) -> list[InventoryRow]:
    order = [band for band, _ in _AGE_BANDS] + [_UNDATED_BAND]
    return [tallies[band].row(band) for band in order if band in tallies]


def inventory_measurement_archive(
    stores: Stores,
    *,
    dataset: str | None = None,
) -> ArchiveInventory:
    """Off the cell files: an evidence read hides what lies outside its task's memory fence."""
    archive = stores.archive
    digest = _scope(stores, dataset)
    named: dict[str, list[str]] = {}
    for label, held_hash in sorted(stores.tenant_datasets.labels().items()):
        named.setdefault(held_hash, []).append(label)
    now = datetime.now(UTC)
    by_dataset: dict[str, _Tally] = {}
    by_role: dict[str, _Tally] = {}
    by_age: dict[str, _Tally] = {}
    total = _Tally()
    held: set[tuple[str, str]] = set()

    for file_key in archive.file_keys():
        answers = [(line, a) for line, a in _parsed(archive.detail_lines(file_key)) if a]
        moved = sum(1 for _, answer in answers if _moved(answer))
        cold_each = archive.cold_bytes_on_disk(file_key) / moved if moved else 0.0
        for line, answer in answers:
            if not _in_scope(answer, digest):
                continue
            configuration = answer.config_key
            held.add((configuration, answer.dataset_hash))
            hot = len(line.encode("utf-8"))
            cold = cold_each if _moved(answer) else 0.0
            for table, key in (
                (by_dataset, " · ".join(named.get(answer.dataset_hash, [answer.dataset_hash]))),
                (by_role, _role_family(answer.role)),
                (by_age, _age_band(answer.created_at, now=now)),
            ):
                table.setdefault(key, _Tally()).add(configuration=configuration, hot=hot, cold=cold)
            total.add(configuration=configuration, hot=hot, cold=cold)

    orphans = sum(
        1
        for entry in archive.list_all(dataset_hash=digest)
        if (entry.config_key, entry.dataset_hash) not in held
    )
    return ArchiveInventory(
        by_dataset=_by_bytes(by_dataset),
        by_role=_by_bytes(by_role),
        by_age=_in_band_order(by_age),
        total=total.row(dataset or "all"),
        orphan_index_rows=orphans,
        archive_writers=archive_writers(stores.shared_root),
    )


@dataclass(frozen=True, slots=True)
class _Moved:
    rows: int
    bytes_before: int
    bytes_after: int
    cold_bytes: int


@dataclass(frozen=True, slots=True)
class _Purged:
    rows: int
    freed: int


@dataclass(frozen=True, slots=True)
class _Conflict:
    """The cold payload names an answer the detail file no longer holds, so nothing was touched."""


@dataclass(frozen=True, slots=True)
class _FileOutcome:
    # `None`: the file holds nothing this pass moves.
    change: _Moved | _Purged | _Conflict | None
    skipped: Mapping[str, int] = field(default_factory=dict)
    unrestorable: int = 0


_Transform = Callable[[list[str], list[MovedFields]], tuple[CellRewrite | None, _FileOutcome]]


def _each_file(
    stores: Stores, file_keys: Iterable[str], verb: str, transform: _Transform, *, apply: bool
) -> ArchiveReport:
    def gated(lines: list[str], cold: list[MovedFields]) -> tuple[CellRewrite | None, _FileOutcome]:
        rewrite, outcome = transform(lines, cold)
        return (rewrite if apply else None), outcome

    outcomes: list[_FileOutcome] = []
    for file_key in file_keys:
        with graceful(f"{verb} {file_key}"):
            outcomes.append(stores.archive.rewrite_cell_file(file_key, gated))
    return _folded(outcomes, applied=apply)


def _folded(outcomes: Iterable[_FileOutcome], *, applied: bool) -> ArchiveReport:
    touched = rows = before = after = cold = conflicts = purged = unrestorable = 0
    skipped: Counter[str] = Counter()
    for outcome in outcomes:
        skipped.update(outcome.skipped)
        unrestorable += outcome.unrestorable
        match outcome.change:
            case _Moved() as moved:
                touched += 1
                rows += moved.rows
                before += moved.bytes_before
                after += moved.bytes_after
                cold += moved.cold_bytes
            case _Purged() as gone:
                touched += 1
                rows += gone.rows
                purged += gone.rows
                # Rides `bytes_before` so `bytes_freed` reads positive with no negative cold figure.
                before += gone.freed
            case _Conflict():
                conflicts += 1
            case None:
                pass
    return ArchiveReport(
        files_touched=touched,
        rows_moved=rows,
        bytes_before=before,
        bytes_after=after,
        cold_bytes=cold,
        conflicts=conflicts,
        purged=purged + unrestorable,
        rows_skipped_by_role=dict(skipped),
        applied=applied,
        notes=_notes(conflicts=conflicts, unrestorable=unrestorable),
    )


# A cell FILE spans datasets and roles, so scope is read off each ANSWER: by file, a pass moves another dataset's rows.
def _compacted(
    lines: list[str], cold: list[MovedFields], *, dataset_hash: str | None, stamped_at: str
) -> tuple[CellRewrite | None, _FileOutcome]:
    out: list[str] = []
    moving: list[MovedFields] = []
    skipped: dict[str, int] = {}

    for line, answer in _parsed(lines):
        if answer is None or not _in_scope(answer, dataset_hash) or answer.compaction is not None:
            out.append(line)
            continue
        if answer.role != _ELIGIBLE_ROLE:
            skipped[answer.role] = skipped.get(answer.role, 0) + 1
            out.append(line)
            continue

        pipeline = answer.cell.pipeline
        moved: dict[str, Any] = {
            f: value
            for f in sorted(UNREAD_PIPELINE_KEYS - _protected_pipeline_fields(pipeline))
            if (value := getattr(pipeline, f)) is not None
        }
        if not moved:
            out.append(line)
            continue

        moving.append(MovedFields(answer.answer, PipelineData(**moved)))
        absent: dict[str, Any] = dict.fromkeys(moved)
        kept = replace(answer.cell, pipeline=replace(pipeline, **absent))
        out.append(answer_line(replace(answer, cell=kept, compaction=stamped_at)))

    if not moving:
        return None, _FileOutcome(None, skipped=skipped)
    payload = [*cold, *moving]
    moved_off = _Moved(len(moving), _size(lines), _size(out), cold_size(payload) - cold_size(cold))
    return CellRewrite(out, payload), _FileOutcome(moved_off, skipped=skipped)


def _refuse_unowned_home(stores: Stores) -> None:
    if stores.archive_home.owner == "install" and not is_host(stores.identity):
        raise PayloadInvalidError(
            "This archive is the install's, shared by every account: only whoever runs this box "
            "compacts, restores or purges it.",
            code="archive_home_owned_by_install",
        )


def compact_measurement_archive(
    stores: Stores,
    *,
    dataset: str | None = None,
    apply: bool = False,
) -> ArchiveReport:
    _refuse_unowned_home(stores)
    writers = archive_writers(stores.shared_root)
    if writers:
        return _blocked(writers)
    transform = partial(_compacted, dataset_hash=_scope(stores, dataset), stamped_at=utcnow_iso())
    return _each_file(stores, stores.archive.file_keys(), "compact", transform, apply=apply)


_Split = tuple[dict[str, MovedFields], list[MovedFields]]


def _split_cold(
    answers: list[tuple[str, FiledAnswer | None]], cold: list[MovedFields], dataset_hash: str | None
) -> _Split | None:
    scoped = {a.answer: _in_scope(a, dataset_hash) for _, a in answers if a is not None}
    mine: dict[str, MovedFields] = {}
    rest: list[MovedFields] = []
    for moved in cold:
        if moved.answer not in scoped:
            return None
        if scoped[moved.answer]:
            mine[moved.answer] = moved
        else:
            rest.append(moved)
    return mine, rest


def _restored(
    lines: list[str], cold: list[MovedFields], *, dataset_hash: str | None
) -> tuple[CellRewrite | None, _FileOutcome]:
    answers = _parsed(lines)
    unrestorable = sum(
        1 for _, a in answers if a and a.purged is not None and _in_scope(a, dataset_hash)
    )
    split = _split_cold(answers, cold, dataset_hash)
    if split is None:
        return None, _FileOutcome(_Conflict(), unrestorable=unrestorable)
    mine, rest = split
    if not mine:
        return None, _FileOutcome(None, unrestorable=unrestorable)
    new_lines = [
        answer_line(replace(rejoined(a, mine[a.answer].pipeline), compaction=None))
        if a is not None and a.answer in mine
        else line
        for line, a in answers
    ]
    moved_back = _Moved(len(mine), _size(lines), _size(new_lines), 0)
    return CellRewrite(new_lines, rest), _FileOutcome(moved_back, unrestorable=unrestorable)


def restore_measurement_archive(
    stores: Stores,
    *,
    dataset: str | None = None,
    apply: bool = False,
) -> ArchiveReport:
    _refuse_unowned_home(stores)
    writers = archive_writers(stores.shared_root)
    if writers:
        return _blocked(writers)
    transform = partial(_restored, dataset_hash=_scope(stores, dataset))
    return _each_file(stores, stores.archive.file_keys(), "restore", transform, apply=apply)


def _purged(
    lines: list[str], cold: list[MovedFields], *, dataset_hash: str | None, stamped_at: str
) -> tuple[CellRewrite | None, _FileOutcome]:
    answers = _parsed(lines)
    split = _split_cold(answers, cold, dataset_hash)
    if split is None:
        return None, _FileOutcome(_Conflict())
    mine, rest = split
    if not mine:
        return None, _FileOutcome(None)
    stamped = [
        answer_line(replace(a, purged=stamped_at)) if a is not None and a.answer in mine else line
        for line, a in answers
    ]
    gone = _Purged(len(mine), cold_size(cold) - cold_size(rest))
    return CellRewrite(stamped, rest), _FileOutcome(gone)


def purge_cold_store(
    stores: Stores,
    *,
    dataset: str | None = None,
    apply: bool = False,
) -> ArchiveReport:
    _refuse_unowned_home(stores)
    writers = archive_writers(stores.shared_root)
    if writers:
        return _blocked(writers)
    transform = partial(_purged, dataset_hash=_scope(stores, dataset), stamped_at=utcnow_iso())
    return _each_file(stores, stores.archive.cold_file_keys(), "purge", transform, apply=apply)


def reindex_measurement_archive(stores: Stores) -> dict[str, int]:
    return stores.archive.reindex()
