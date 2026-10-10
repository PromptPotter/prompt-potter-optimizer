from __future__ import annotations

import json
import pathlib
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import Field, computed_field

from promptpotter.domain.scoring import (
    ABANDONED_ROW_KEYS,
    UNREAD_PIPELINE_KEYS,
    measured_facts,
)
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.runtime_flags import derive_run_state
from promptpotter.infrastructure.store.layout import inner_sandboxes_dir
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import graceful
from promptpotter.shared.measurement_context import MeasurementRole

if TYPE_CHECKING:
    from promptpotter.infrastructure.store.measurement_archive import MeasurementArchive
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


_MOVABLE_ROW_FIELDS: frozenset[str] = ABANDONED_ROW_KEYS

_ELIGIBLE_ROLE = MeasurementRole.PANEL
"""Only an answer a candidate's own walk filed compacts: the origin's and the parent's are the
ones every later campaign replays. An answer under any other role is SKIPPED and counted by role,
never compacted on a guess."""

_COMPACTED = "compaction"
_PURGED = "purged"


def _protected_pipeline_fields(row: Mapping[str, Any]) -> frozenset[str]:
    """``panels.py::_inner_narrated`` needs ``reasoning_trace`` beside ``mean_round_delta``."""
    pd = row.get("pipeline_data")
    if isinstance(pd, dict) and "mean_round_delta" in pd:
        return frozenset({"reasoning_trace"})
    return frozenset()


def _parsed(lines: Iterable[str]) -> Iterator[tuple[str, dict[str, Any] | None]]:
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            row = None
        yield line, row if isinstance(row, dict) else None


def _in_scope(row: Mapping[str, Any], dataset: str | None) -> bool:
    return dataset is None or row.get("dataset_name") == dataset


def _dumped(row: Mapping[str, Any]) -> str:
    return json.dumps(row, separators=(",", ":"), default=str) + "\n"


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
    """Off the cell files: an evidence read hides a controlled line's answers behind its scope."""
    archive = stores.archive
    now = datetime.now(UTC)
    by_dataset: dict[str, _Tally] = {}
    by_role: dict[str, _Tally] = {}
    by_age: dict[str, _Tally] = {}
    total = _Tally()
    held: set[tuple[str, str]] = set()

    for file_key in archive.file_keys():
        rows = [(line, row) for line, row in _parsed(archive.detail_lines(file_key)) if row]
        moved = sum(1 for _, row in rows if _COMPACTED in row and _PURGED not in row)
        cold_each = archive.cold_bytes_on_disk(file_key) / moved if moved else 0.0
        for line, row in rows:
            if not _in_scope(row, dataset):
                continue
            configuration = str(row.get("config_key") or "")
            dataset_name = str(row.get("dataset_name") or "")
            held.add((configuration, dataset_name))
            hot = len(line.encode("utf-8"))
            cold = cold_each if _COMPACTED in row and _PURGED not in row else 0.0
            for table, key in (
                (by_dataset, dataset_name or "<unstamped>"),
                (by_role, _role_family(str(row.get("role") or "<unlabelled>"))),
                (by_age, _age_band(str(row.get("created_at") or ""), now=now)),
            ):
                table.setdefault(key, _Tally()).add(configuration=configuration, hot=hot, cold=cold)
            total.add(configuration=configuration, hot=hot, cold=cold)

    orphans = sum(
        1
        for entry in archive.list_all(dataset_name=dataset)
        if (entry["config_key"], str(entry.get("dataset_name") or "")) not in held
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
class _FilePlan:
    lines: list[str]
    cold: list[dict[str, Any]]
    before: int
    after: int


# A cell FILE spans datasets and roles, so scope is read off each ANSWER: by file, a pass moves another dataset's rows.
def _plan_compaction(
    lines: list[str], *, dataset: str | None, stamped_at: str, skipped: dict[str, int]
) -> _FilePlan | None:
    out: list[str] = []
    cold: list[dict[str, Any]] = []

    for line, row in _parsed(lines):
        if row is None or not _in_scope(row, dataset) or _COMPACTED in row:
            out.append(line)
            continue
        role = str(row.get("role") or "")
        if role != _ELIGIBLE_ROLE:
            skipped[role or "<unlabelled>"] = skipped.get(role or "<unlabelled>", 0) + 1
            out.append(line)
            continue

        protected = _protected_pipeline_fields(row)
        moved_row = {f: row.pop(f) for f in sorted(_MOVABLE_ROW_FIELDS) if f in row}
        moved_pd: dict[str, Any] = {}
        pd = row.get("pipeline_data")
        if isinstance(pd, dict):
            for f in sorted(UNREAD_PIPELINE_KEYS - protected):
                if f in pd:
                    moved_pd[f] = pd.pop(f)

        if not moved_row and not moved_pd:
            out.append(line)
            continue

        entry: dict[str, Any] = {"k": row["answer"]}
        if moved_row:
            entry["row"] = moved_row
        if moved_pd:
            entry["pd"] = moved_pd
        cold.append(entry)
        row[_COMPACTED] = stamped_at
        out.append(_dumped(row))

    if not cold:
        return None
    return _FilePlan(lines=out, cold=cold, before=_size(lines), after=_size(out))


def compact_measurement_archive(
    stores: Stores,
    *,
    dataset: str | None = None,
    apply: bool = False,
) -> ArchiveReport:
    writers = archive_writers(stores.shared_root)
    if writers:
        return _blocked(writers)

    archive = stores.archive
    stamped_at = utcnow_iso()
    touched = rows = before = after = cold_bytes = 0
    skipped: dict[str, int] = {}

    for file_key in archive.file_keys():
        with graceful(f"compact {file_key}"):
            plan = _plan_compaction(
                archive.detail_lines(file_key),
                dataset=dataset,
                stamped_at=stamped_at,
                skipped=skipped,
            )
            if plan is None:
                continue
            before += plan.before
            after += plan.after
            rows += len(plan.cold)
            touched += 1
            payload = [*(archive.read_cold(file_key) or []), *plan.cold]
            held = archive.cold_bytes_on_disk(file_key)
            if apply:
                # Cold first: a crash between the two reads as a no-op; the reverse loses the fields.
                cold_bytes += archive.write_cold(file_key, payload) - held
                archive.replace_detail(file_key, plan.lines)
            else:
                cold_bytes += archive.cold_size(payload) - held

    return ArchiveReport(
        files_touched=touched,
        rows_moved=rows,
        bytes_before=before,
        bytes_after=after,
        cold_bytes=cold_bytes,
        rows_skipped_by_role=skipped,
        applied=apply,
    )


def _split_cold(
    lines: list[str], cold: list[dict[str, Any]], dataset: str | None
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]] | None:
    scoped = {
        row["answer"]: _in_scope(row, dataset)
        for _, row in _parsed(lines)
        if row and "answer" in row
    }
    mine: dict[str, dict[str, Any]] = {}
    rest: list[dict[str, Any]] = []
    for entry in cold:
        answer = str(entry.get("k") or "")
        if answer not in scoped:
            return None
        if scoped[answer]:
            mine[answer] = entry
        else:
            rest.append(entry)
    return mine, rest


def _restored(row: dict[str, Any], entry: Mapping[str, Any]) -> dict[str, Any]:
    moved_row = entry.get("row")
    if isinstance(moved_row, dict):
        row.update(measured_facts(moved_row))
    moved_pd = entry.get("pd")
    if isinstance(moved_pd, dict):
        pd = row.get("pipeline_data")
        if not isinstance(pd, dict):
            pd = {}
            row["pipeline_data"] = pd
        pd.update(moved_pd)
    row.pop(_COMPACTED, None)
    return row


def _swap_cold(archive: MeasurementArchive, file_key: str, rest: list[dict[str, Any]]) -> None:
    if rest:
        archive.write_cold(file_key, rest)
    else:
        archive.drop_cold(file_key)


def restore_measurement_archive(
    stores: Stores,
    *,
    dataset: str | None = None,
    apply: bool = False,
) -> ArchiveReport:
    writers = archive_writers(stores.shared_root)
    if writers:
        return _blocked(writers)

    archive = stores.archive
    touched = rows = before = after = conflicts = purged = 0

    for file_key in archive.file_keys():
        with graceful(f"restore {file_key}"):
            lines = archive.detail_lines(file_key)
            purged += sum(
                1 for _, row in _parsed(lines) if row and _PURGED in row and _in_scope(row, dataset)
            )
            cold = archive.read_cold(file_key)
            if cold is None:
                continue
            split = _split_cold(lines, cold, dataset)
            if split is None:
                conflicts += 1
                continue
            mine, rest = split
            if not mine:
                continue
            new_lines = [
                _dumped(_restored(row, mine[row["answer"]]))
                if row is not None and row.get("answer") in mine
                else line
                for line, row in _parsed(lines)
            ]
            before += _size(lines)
            after += _size(new_lines)
            rows += len(mine)
            touched += 1
            if apply:
                archive.replace_detail(file_key, new_lines)
                _swap_cold(archive, file_key, rest)

    return ArchiveReport(
        files_touched=touched,
        rows_moved=rows,
        bytes_before=before,
        bytes_after=after,
        conflicts=conflicts,
        purged=purged,
        applied=apply,
        notes=_notes(conflicts=conflicts, unrestorable=purged),
    )


def purge_cold_store(
    stores: Stores,
    *,
    dataset: str | None = None,
    apply: bool = False,
) -> ArchiveReport:
    writers = archive_writers(stores.shared_root)
    if writers:
        return _blocked(writers)

    archive = stores.archive
    stamped_at = utcnow_iso()
    touched = freed = rows = conflicts = 0
    for file_key in archive.file_keys():
        cold = archive.read_cold(file_key)
        if cold is None:
            continue
        with graceful(f"purge {file_key}"):
            lines = archive.detail_lines(file_key)
            split = _split_cold(lines, cold, dataset)
            if split is None:
                conflicts += 1
                continue
            mine, rest = split
            if not mine:
                continue
            touched += 1
            rows += len(mine)
            freed += archive.cold_bytes_on_disk(file_key) - (archive.cold_size(rest) if rest else 0)
            if apply:
                # Stamp before dropping: a crash between leaves a false "gone" `restore` corrects.
                archive.replace_detail(
                    file_key,
                    [
                        _dumped({**row, _PURGED: stamped_at})
                        if row is not None and row.get("answer") in mine
                        else line
                        for line, row in _parsed(lines)
                    ],
                )
                _swap_cold(archive, file_key, rest)

    # `freed` rides `bytes_before` so `bytes_freed` reads positive with no negative cold figure.
    return ArchiveReport(
        files_touched=touched,
        bytes_before=freed,
        rows_moved=rows,
        conflicts=conflicts,
        purged=rows,
        applied=apply,
        notes=_notes(conflicts=conflicts),
    )


def reindex_measurement_archive(stores: Stores) -> dict[str, int]:
    return stores.archive.reindex()
