from __future__ import annotations

import contextlib
import gzip
import json
import os
import threading
import uuid
from collections import OrderedDict
from collections.abc import Iterable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any, ClassVar, NamedTuple

from filelock import BaseFileLock, FileLock, Timeout

from promptpotter.domain.measurement_provenance import REUSABLE_MIN_GRADE, meets_grade
from promptpotter.domain.results_health import is_deprecated
from promptpotter.domain.sample import ArchiveEntry, FiledAnswer
from promptpotter.domain.scoring import UNREAD_PIPELINE_KEYS, MeasuredCell, PipelineData
from promptpotter.infrastructure.store.io import (
    read_bytes_optional,
    read_json_optional,
    unlink_robust,
    write_bytes,
    write_json,
    write_jsonl,
    write_text,
)
from promptpotter.infrastructure.store.layout import MEASUREMENTS_DIR
from promptpotter.infrastructure.store.read_model import (
    append_row,
    file_sig,
    fold_jsonl_from,
)
from promptpotter.shared.hashing import ADDRESS_HEX, stable_hash

ANSWER_KEY = "answer"
_INDEX_FOLD_KEY = "k"
# What an entry says of its dataset and first answer, never of its configuration.
_INDEXED_ONLY = frozenset({"dataset_name", "name", "created_at"})
_CELLS_SUFFIX = ".jsonl"
_COLD_SUFFIX = ".jsonl.gz"
_FILES_MAX_BYTES = 32 << 20


def config_key(node_configs: list[tuple[str, dict[str, Any]]]) -> str:
    return stable_hash(node_configs, length=ADDRESS_HEX)


def _cell_key(node_configs: list[tuple[str, dict[str, Any]]], sample_key: str) -> str:
    return stable_hash([node_configs, sample_key], length=ADDRESS_HEX)


def _fold_key(key: str, dataset_name: str | None) -> str:
    return f"{key}|{dataset_name or ''}"


def _file_keys(entry: ArchiveEntry) -> list[str]:
    chain = entry.node_configs
    return [config_key(chain[:n]) for n in range(1, len(chain) + 1)] or [entry.config_key]


def _file_of(entry: ArchiveEntry, cell: MeasuredCell) -> str:
    terminal = cell.pipeline.terminal_node
    # A failed cell answers for the WHOLE configuration, whichever node it failed at.
    if terminal and not cell.errored:
        for n, (name, _) in enumerate(entry.node_configs, start=1):
            if name == terminal:
                return config_key(entry.node_configs[:n])
    return entry.config_key


def _matches_subset(
    node_configs: list[tuple[str, dict[str, Any]]],
    predicate: dict[str, dict[str, Any]],
) -> bool:
    if not predicate:
        return False
    by_name = dict(node_configs)
    for node_name, subdict in predicate.items():
        cfg = by_name.get(node_name)
        if cfg is None:
            return False
        if subdict and not (subdict.items() <= cfg.items()):
            return False
    return True


def _tail_from(st: os.stat_result, cursor: tuple[int, int] | None) -> int:
    if cursor is None:
        return 0
    inode, offset = cursor
    # The size test too: a rewrite swaps the file, and ext4 reuses a freed inode.
    return offset if st.st_ino == inode and st.st_size >= offset else 0


def answer_of(line: str) -> FiledAnswer | None:
    """``None`` is a line holding no JSON object; an object that is no answer raises."""
    try:
        row = json.loads(line)
    except json.JSONDecodeError:
        return None
    return FiledAnswer.from_wire(row) if isinstance(row, dict) else None


def answer_line(answer: FiledAnswer) -> str:
    return json.dumps(answer.wire(), separators=(",", ":"), ensure_ascii=False) + "\n"


def standing(answers: Iterable[FiledAnswer]) -> dict[int, FiledAnswer]:
    taken: dict[int, tuple[bool, FiledAnswer]] = {}
    for answer in answers:
        sid = answer.cell.sample_id
        live = not (answer.cell.errored or is_deprecated(answer.cell))
        held = taken.get(sid)
        if held is None or live or not held[0]:
            taken[sid] = (live, answer)
    return {sid: answer for sid, (_, answer) in taken.items()}


class MovedFields(NamedTuple):
    """What a compaction took off one answer; ``pipeline`` holds those fields and no other."""

    answer: str
    pipeline: PipelineData


def rejoined(answer: FiledAnswer, moved: PipelineData) -> FiledAnswer:
    """Only FILLS: the answer on disk is the truth for what it still holds."""
    hot = answer.cell.pipeline
    back = {f: getattr(moved, f) for f in UNREAD_PIPELINE_KEYS if getattr(hot, f) is None}
    return replace(answer, cell=replace(answer.cell, pipeline=replace(hot, **back)))


class _CellFile:
    def __init__(self) -> None:
        self._cursor: tuple[int, int] | None = None
        self._stat: tuple[int, int] | None = None
        self._empty()

    def _empty(self) -> None:
        # NEW containers: a reader still iterating the ones it was handed must not see them emptied.
        self.rows: list[FiledAnswer] = []
        self.by_id: dict[str, FiledAnswer] = {}

    @property
    def size(self) -> int:
        return 0 if self._stat is None else self._stat[1]

    def refresh(self, path: Path) -> None:
        try:
            st = path.stat()
        except FileNotFoundError:
            self._cursor = self._stat = None
            self._empty()
            return
        sig = (st.st_mtime_ns, st.st_size)
        if sig == self._stat:
            return
        start = _tail_from(st, self._cursor)
        # The fold returns the newline-aligned offset, so a torn trailing line stays pending.
        folded, offset = fold_jsonl_from(path, ANSWER_KEY, start)
        fresh = {ref: FiledAnswer.from_wire(row) for ref, row in folded.items()}
        if start == 0:
            self._empty()
        self.rows.extend(fresh.values())
        self.by_id.update(fresh)
        self._cursor = (st.st_ino, offset)
        self._stat = sig

    def append(self, path: Path, answers: list[FiledAnswer]) -> None:
        """Takes *answers* into the tail unread: on Windows, opening a just-appended file waits out a scan."""
        self.refresh(path)
        held = 0 if self._cursor is None else self._cursor[1]
        whole = self._stat is None or self._stat[1] == held
        rows = [answer.wire() for answer in answers]
        added = append_row(path, *rows)
        st = path.stat()
        if not (whole and st.st_size == held + added and _tail_from(st, self._cursor) == held):
            return
        # Through JSON, as a reader folds them: a tuple is a list on disk.
        fresh = [
            FiledAnswer.from_wire(json.loads(json.dumps(row, ensure_ascii=False))) for row in rows
        ]
        self.rows.extend(fresh)
        self.by_id.update((answer.answer, answer) for answer in fresh)
        self._cursor = (st.st_ino, st.st_size)
        self._stat = (st.st_mtime_ns, st.st_size)


class MeasurementArchive:
    """Keyed by content, never ``backend_id``: a dataset repointed elsewhere is served its old rows."""

    _open: ClassVar[dict[Path, MeasurementArchive]] = {}
    _open_lock: ClassVar[threading.Lock] = threading.Lock()

    @classmethod
    def at(cls, base_dir: Path) -> MeasurementArchive:
        with cls._open_lock:
            archive = cls._open.get(base_dir)
            if archive is None:
                archive = cls._open[base_dir] = cls(base_dir)
            return archive

    def __init__(self, base_dir: Path):
        self._base_dir = base_dir
        self._lock = threading.RLock()
        self._rows: dict[str, ArchiveEntry] | None = None
        self._stat: tuple[int, int] | None = None
        self._cursor: tuple[int, int] | None = None
        self._files: OrderedDict[str, _CellFile] = OrderedDict()

    @property
    def base_dir(self) -> Path:
        return self._base_dir

    def _store_dir(self) -> Path:
        return self._base_dir / MEASUREMENTS_DIR

    def _index_path(self) -> Path:
        return self._store_dir() / "index.jsonl"

    def _cells_dir(self) -> Path:
        return self._store_dir() / "cells"

    def _configs_dir(self) -> Path:
        return self._store_dir() / "configs"

    def derived_dir(self) -> Path:
        return self._store_dir() / "derived"

    def _cell_path(self, file_key: str) -> Path:
        return self._cells_dir() / f"{file_key}{_CELLS_SUFFIX}"

    def _claim_path(self, node_configs: list[tuple[str, dict[str, Any]]], sample_key: str) -> Path:
        return self._store_dir() / "claims" / _cell_key(node_configs, sample_key)

    def _invalidate(self) -> None:
        self._rows = None
        self._stat = None
        self._cursor = None

    def _live_rows(self) -> dict[str, ArchiveEntry]:
        """Every read stats the file: an in-process L4 inner cycle appends to the same dir."""
        path = self._index_path()
        with self._lock:
            try:
                st = path.stat()
            except FileNotFoundError:
                self._invalidate()
                return {}
            sig = (st.st_mtime_ns, st.st_size)
            if self._rows is not None and sig == self._stat:
                return self._rows
            start = 0 if self._rows is None else _tail_from(st, self._cursor)
            folded, offset = fold_jsonl_from(path, _INDEX_FOLD_KEY, start)
            fresh = {
                fold_key: ArchiveEntry.model_validate(
                    {k: v for k, v in row.items() if k != _INDEX_FOLD_KEY}
                )
                for fold_key, row in folded.items()
            }
            # A NEW dict per change: a reader still iterating the shared one must not see it grow.
            self._rows = {**self._rows, **fresh} if start and self._rows is not None else fresh
            self._cursor = (st.st_ino, offset)
            self._stat = sig
            return self._rows

    def _file(self, file_key: str) -> _CellFile:
        with self._lock:
            held = self._files.get(file_key)
            fresh = held is None
            if held is None:
                held = self._files[file_key] = _CellFile()
            else:
                self._files.move_to_end(file_key)
            held.refresh(self._cell_path(file_key))
            if fresh:
                weight = sum(f.size for f in self._files.values())
                while weight > _FILES_MAX_BYTES and len(self._files) > 1:
                    weight -= self._files.popitem(last=False)[1].size
            return held

    def file_answers(
        self,
        entry: ArchiveEntry,
        graded: Iterable[tuple[MeasuredCell, str]],
        *,
        role: str,
        source: str,
        created_at: str,
    ) -> list[str]:
        """Each cell beside its provenance grade; the addresses come back in that order."""
        by_file: dict[str, list[FiledAnswer]] = {}
        refs: list[str] = []
        for cell, provenance in graded:
            file_key = _file_of(entry, cell)
            ref = f"{file_key}.{uuid.uuid4().hex[:12]}"
            refs.append(ref)
            by_file.setdefault(file_key, []).append(
                FiledAnswer(
                    replace(cell, answer=ref),
                    config_key=entry.config_key,
                    dataset_name=entry.dataset_name,
                    role=role,
                    source=source,
                    provenance=provenance,
                    created_at=created_at,
                )
            )
        if not refs:
            return refs
        with self._lock:
            # First: no answer is ever on disk under a configuration nothing describes.
            self._register(entry.model_copy(update={"name": role, "created_at": created_at}))
            for file_key, filed in by_file.items():
                self._file(file_key).append(self._cell_path(file_key), filed)
        return refs

    def _register(self, entry: ArchiveEntry) -> None:
        config_path = self._configs_dir() / f"{entry.config_key}.json"
        if not config_path.exists():
            write_json(config_path, entry.model_dump(mode="json", exclude=set(_INDEXED_ONLY)))
        fold_key = _fold_key(entry.config_key, entry.dataset_name)
        if fold_key in self._live_rows():
            return
        append_row(self._index_path(), {_INDEX_FOLD_KEY: fold_key, **entry.model_dump(mode="json")})

    def answer(self, ref: str) -> FiledAnswer | None:
        file_key, _, _ = ref.partition(".")
        if not file_key or any(ch in ref for ch in "/\\") or ".." in ref:
            return None
        return self._file(file_key).by_id.get(ref)

    def whole(self, answer: FiledAnswer) -> FiledAnswer:
        """*answer* with what a compaction moved off it; a purged answer comes back as it stands."""
        cold = self.read_cold(answer.answer.partition(".")[0]) or []
        moved = next((m.pipeline for m in cold if m.answer == answer.answer), None)
        return answer if moved is None else rejoined(answer, moved)

    def population(self, entry: ArchiveEntry) -> list[FiledAnswer]:
        with self._lock:
            found = [
                answer
                for file_key in _file_keys(entry)
                for answer in self._file(file_key).rows
                if answer.dataset_name == entry.dataset_name
            ]
        found.sort(key=lambda answer: answer.created_at)
        return found

    def list_all(self, *, dataset_name: str | None = None) -> list[ArchiveEntry]:
        entries = list(self._live_rows().values())
        if dataset_name is None:
            return entries
        return [e for e in entries if e.dataset_name == dataset_name]

    def entry(self, key: str, dataset_name: str | None) -> ArchiveEntry | None:
        return self._live_rows().get(_fold_key(key, dataset_name))

    def cell_signatures(self) -> dict[str, tuple[int, int]]:
        out: dict[str, tuple[int, int]] = {}
        try:
            with os.scandir(self._cells_dir()) as it:
                for e in it:
                    if not e.name.endswith(_CELLS_SUFFIX):
                        continue
                    st = e.stat()
                    out[e.name[: -len(_CELLS_SUFFIX)]] = (st.st_mtime_ns, st.st_size)
        except OSError:
            return out
        return out

    def signature(
        self,
        entry: ArchiveEntry | None = None,
        sigs: dict[str, tuple[int, int]] | None = None,
    ) -> tuple[tuple[str, int, int], ...] | None:
        sigs = self.cell_signatures() if sigs is None else sigs
        keys = sorted(sigs) if entry is None else _file_keys(entry)
        held = tuple((key, *sigs[key]) for key in keys if key in sigs)
        return held or None

    def files_signature(self, file_keys: Iterable[str]) -> tuple[Any, ...]:
        return tuple(file_sig(self._cell_path(key)) for key in sorted(set(file_keys)))

    def _cold_dir(self) -> Path:
        return self._store_dir() / "cold"

    def cold_path(self, file_key: str) -> Path:
        return self._cold_dir() / f"{file_key}{_COLD_SUFFIX}"

    def file_keys(self) -> list[str]:
        return sorted(self.cell_signatures())

    def detail_lines(self, file_key: str) -> list[str]:
        # RAW lines: a rewrite hands every one back, unparseable ones included, in order.
        try:
            with self._cell_path(file_key).open(encoding="utf-8") as fh:
                return fh.readlines()
        except FileNotFoundError:
            return []

    def replace_detail(self, file_key: str, lines: Iterable[str]) -> int:
        content = "".join(lines)
        write_text(self._cell_path(file_key), content)
        return len(content.encode("utf-8"))

    @staticmethod
    def _encode_cold(moved: Iterable[MovedFields]) -> bytes:
        blob = "\n".join(
            json.dumps({"k": m.answer, "pd": m.pipeline.wire()}, separators=(",", ":"))
            for m in moved
        )
        return gzip.compress(blob.encode("utf-8"), 6)

    def cold_size(self, moved: Iterable[MovedFields]) -> int:
        return len(self._encode_cold(moved))

    def cold_bytes_on_disk(self, file_key: str) -> int:
        try:
            return self.cold_path(file_key).stat().st_size
        except OSError:
            return 0

    def write_cold(self, file_key: str, moved: Iterable[MovedFields]) -> int:
        data = self._encode_cold(moved)
        write_bytes(self.cold_path(file_key), data)
        return len(data)

    def read_cold(self, file_key: str) -> list[MovedFields] | None:
        """``None`` = never compacted; ``[]`` = a compaction that moved nothing."""
        raw = read_bytes_optional(self.cold_path(file_key))
        if raw is None:
            return None
        text = gzip.decompress(raw).decode("utf-8")
        held = [json.loads(line) for line in text.splitlines() if line.strip()]
        return [MovedFields(e["k"], PipelineData.from_wire(e["pd"])) for e in held]

    def drop_cold(self, file_key: str) -> int:
        path = self.cold_path(file_key)
        try:
            size = path.stat().st_size
        except OSError:
            return 0
        path.unlink(missing_ok=True)
        return size

    def reindex(self) -> dict[str, int]:
        first: dict[str, ArchiveEntry] = {}
        undescribed = 0
        for file_key in self.file_keys():
            for answer in self._file(file_key).rows:
                fold_key = _fold_key(answer.config_key, answer.dataset_name)
                held = first.get(fold_key)
                if held is not None and held.created_at <= answer.created_at:
                    continue
                described = read_json_optional(self._configs_dir() / f"{answer.config_key}.json")
                if not isinstance(described, dict):
                    undescribed += 1
                    continue
                first[fold_key] = ArchiveEntry.model_validate(
                    {
                        **described,
                        "dataset_name": answer.dataset_name,
                        "name": answer.role,
                        "created_at": answer.created_at,
                    }
                )
        indexed = sorted(first.items(), key=lambda held: (held[1].created_at, held[0]))
        write_jsonl(
            self._index_path(),
            ({_INDEX_FOLD_KEY: k, **entry.model_dump(mode="json")} for k, entry in indexed),
        )
        self._invalidate()
        return {"indexed": len(indexed), "undescribed": undescribed}

    def restamp_dataset(self, old_name: str, new_name: str) -> int:
        touched = {e.config_key for e in self.list_all(dataset_name=old_name)}
        if not touched:
            return 0
        with self._lock:
            for file_key in self.file_keys():
                out: list[str] = []
                moved = False
                for line in self.detail_lines(file_key):
                    answer = answer_of(line)
                    if answer is not None and answer.dataset_name == old_name:
                        line = answer_line(replace(answer, dataset_name=new_name))
                        moved = True
                    out.append(line)
                if moved:
                    self.replace_detail(file_key, out)
            self._files.clear()
        self.reindex()
        return len(touched)

    def measurements_for_config(
        self,
        predicate: dict[str, dict[str, Any]],
        *,
        dataset_name: str | None = None,
        newest: int | None = None,
    ) -> Iterator[FiledAnswer]:
        matching = [
            entry
            for entry in self.list_all(dataset_name=dataset_name)
            if _matches_subset(entry.node_configs, predicate)
        ]
        if newest is not None:
            matching.sort(key=lambda entry: entry.created_at, reverse=True)
            del matching[newest:]
        for entry in matching:
            yield from self.population(entry)


class CellClaim:
    """An OS lock the kernel drops with its holder: no expiry, so none can time a live holder out."""

    def __init__(self, lock: BaseFileLock, row_path: Path) -> None:
        self._lock = lock
        self.row_path = row_path

    def publish(self, cell: MeasuredCell, *, grade: str) -> None:
        if cell.errored or not meets_grade(grade, REUSABLE_MIN_GRADE) or is_deprecated(cell):
            self.release()
            return
        # Windows refuses the replace while a waiter reads a past holder's row of this same cell.
        with contextlib.suppress(PermissionError):
            write_json(self.row_path, cell.wire())

    def release(self) -> None:
        # The row goes first: one standing with the lock free is no live holder's.
        try:
            _drop_row(self.row_path)
        finally:
            if self._lock.is_locked:
                self._lock.release()


def _drop_row(path: Path) -> None:
    # Windows refuses the delete while a waiter reads the row; the next claimer's drop removes it.
    with contextlib.suppress(PermissionError):
        unlink_robust(path)


class ReplayFeed:
    """Drawn from EVERY dataset: a cell is the sample's content under a configuration, never its panel."""

    def __init__(
        self,
        archive: MeasurementArchive,
        node_configs: list[tuple[str, dict[str, Any]]],
    ) -> None:
        self._archive = archive
        self._node_configs = node_configs
        self._file_keys = [config_key(node_configs[:n]) for n in range(1, len(node_configs) + 1)]
        self._seen: dict[str, int] = {}
        self._deprecated: dict[str, bool] = {}

    def advance(self) -> dict[str, FiledAnswer]:
        banked: list[FiledAnswer] = []
        with self._archive._lock:
            for file_key in self._file_keys:
                rows = self._archive._file(file_key).rows
                banked.extend(rows[self._seen.get(file_key, 0) :])
                self._seen[file_key] = len(rows)
        banked.sort(key=lambda answer: answer.created_at)
        fresh: dict[str, FiledAnswer] = {}
        for answer in banked:
            cell = answer.cell
            key = cell.sample_key
            if not key or cell.errored or not meets_grade(answer.provenance, REUSABLE_MIN_GRADE):
                continue
            deprecated = is_deprecated(cell)
            if deprecated and self._deprecated.get(key) is False:
                continue
            self._deprecated[key] = deprecated
            fresh[key] = answer
        return fresh

    def claim(self, sample_key: str) -> CellClaim | None:
        path = self._archive._claim_path(self._node_configs, sample_key)
        lock = FileLock(f"{path}.lock", timeout=0, thread_local=False)
        try:
            lock.acquire()
        except Timeout:
            return None
        claim = CellClaim(lock, path.with_suffix(".json"))
        # A holder unlinks its row before it unlocks, so a row found here outlived its holder.
        _drop_row(claim.row_path)
        return claim

    def cell_key(self, sample_key: str) -> str:
        return _cell_key(self._node_configs, sample_key)

    def claimed_cell(self, sample_key: str) -> MeasuredCell | None:
        path = self._archive._claim_path(self._node_configs, sample_key)
        try:
            row = read_json_optional(path.with_suffix(".json"))
        except PermissionError:
            # Windows: a holder is unlinking or replacing it this instant — the next poll reads again.
            return None
        return None if row is None else MeasuredCell.from_wire(row)


__all__ = [
    "ANSWER_KEY",
    "CellClaim",
    "MeasurementArchive",
    "MovedFields",
    "ReplayFeed",
    "answer_line",
    "answer_of",
    "config_key",
    "rejoined",
    "standing",
]
