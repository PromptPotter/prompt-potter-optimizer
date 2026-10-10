from __future__ import annotations

import contextlib
import gzip
import json
import os
import threading
import uuid
from collections import OrderedDict
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, ClassVar, NamedTuple

from filelock import BaseFileLock, FileLock, Timeout

from promptpotter.domain.measurement_provenance import REUSABLE_MIN_GRADE, meets_grade
from promptpotter.domain.results_health import is_deprecated
from promptpotter.domain.sample import Measurement
from promptpotter.domain.scoring import MeasuredCell, PipelineData, measured_facts
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
    iter_jsonl,
)
from promptpotter.shared.errors import is_error_result
from promptpotter.shared.hashing import ADDRESS_HEX, stable_hash

ANSWER_KEY = "answer"
PROVENANCE_KEYS = frozenset(
    {
        "config_key",
        "dataset_name",
        "role",
        "source",
        "provenance",
        "created_at",
        "compaction",
        "purged",
    }
)
_INDEX_FOLD_KEY = "k"
_CELLS_SUFFIX = ".jsonl"
_COLD_SUFFIX = ".jsonl.gz"
_FILES_MAX_BYTES = 32 << 20


def config_key(node_configs: list[tuple[str, dict[str, Any]]]) -> str:
    return stable_hash(node_configs, length=ADDRESS_HEX)


def _cell_key(node_configs: list[tuple[str, dict[str, Any]]], sample_key: str) -> str:
    return stable_hash([node_configs, sample_key], length=ADDRESS_HEX)


def _chain(entry: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [
        (pair[0], pair[1])
        for pair in entry.get("node_configs") or []
        if isinstance(pair, list | tuple) and len(pair) == 2 and isinstance(pair[1], dict)
    ]


def _file_keys(entry: dict[str, Any]) -> list[str]:
    chain = _chain(entry)
    if not chain:
        return [entry["config_key"]]
    return [config_key(chain[:n]) for n in range(1, len(chain) + 1)]


def _file_of(entry: dict[str, Any], row: dict[str, Any]) -> str:
    chain = _chain(entry)
    terminal = PipelineData.from_wire(row.get("pipeline_data") or {}).terminal_node
    # A failed cell answers for the WHOLE configuration, whichever node it failed at.
    if terminal and not is_error_result(row):
        for n, (name, _) in enumerate(chain, start=1):
            if name == terminal:
                return config_key(chain[:n])
    return config_key(chain) if chain else entry["config_key"]


def _matches_subset(
    run_node_configs: list[Any],
    predicate: dict[str, dict[str, Any]],
) -> bool:
    if not predicate:
        return False
    by_name: dict[str, dict[str, Any]] = {}
    for stored_pair in run_node_configs:
        if not (isinstance(stored_pair, list | tuple) and len(stored_pair) == 2):
            continue
        n_have, c_have = stored_pair
        if isinstance(c_have, dict):
            by_name[n_have] = c_have
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


def _entry_matches_dataset(entry: dict[str, Any], dataset_name: str | None) -> bool:
    return dataset_name is None or entry.get("dataset_name") == dataset_name


def answer_facts(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k not in PROVENANCE_KEYS}


def standing(answers: Iterable[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    taken: dict[int, tuple[bool, dict[str, Any]]] = {}
    for row in answers:
        sid = row.get("sample_id")
        if not isinstance(sid, int):
            continue
        facts = MeasuredCell.from_wire(row)
        live = not (facts.errored or is_deprecated(facts))
        held = taken.get(sid)
        if held is None or live or not held[0]:
            taken[sid] = (live, row)
    return {sid: row for sid, (_, row) in taken.items()}


class ReplayableRow(NamedTuple):
    """The row's ``sample_id`` names a slot in ``dataset_name`` and nowhere else."""

    dataset_name: str | None
    row: dict[str, Any]


class _CellFile:
    def __init__(self) -> None:
        self._cursor: tuple[int, int] | None = None
        self._stat: tuple[int, int] | None = None
        self._empty()

    def _empty(self) -> None:
        # NEW containers: a reader still iterating the ones it was handed must not see them emptied.
        self.rows: list[dict[str, Any]] = []
        self.by_id: dict[str, dict[str, Any]] = {}

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
        fresh, offset = fold_jsonl_from(path, ANSWER_KEY, start)
        if start == 0:
            self._empty()
        self.rows.extend(fresh.values())
        self.by_id.update(fresh)
        self._cursor = (st.st_ino, offset)
        self._stat = sig

    def append(self, path: Path, rows: list[dict[str, Any]]) -> None:
        """Takes *rows* into the tail unread: on Windows, opening a just-appended file waits out a scan."""
        self.refresh(path)
        held = 0 if self._cursor is None else self._cursor[1]
        whole = self._stat is None or self._stat[1] == held
        added = append_row(path, *rows)
        st = path.stat()
        if not (whole and st.st_size == held + added and _tail_from(st, self._cursor) == held):
            return
        # Through JSON, as a reader folds them: a tuple is a list on disk.
        fresh = [json.loads(json.dumps(row, ensure_ascii=False)) for row in rows]
        self.rows.extend(fresh)
        self.by_id.update((row[ANSWER_KEY], row) for row in fresh)
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
        self._rows: dict[str, dict[str, Any]] | None = None
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

    def _live_rows(self) -> dict[str, dict[str, Any]]:
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
            fresh, offset = fold_jsonl_from(path, _INDEX_FOLD_KEY, start)
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

    def file_answers(self, entry: dict[str, Any], rows: Iterable[dict[str, Any]]) -> list[str]:
        by_file: dict[str, list[dict[str, Any]]] = {}
        refs: list[str] = []
        for row in rows:
            file_key = _file_of(entry, row)
            ref = f"{file_key}.{uuid.uuid4().hex[:12]}"
            refs.append(ref)
            by_file.setdefault(file_key, []).append(
                {
                    **measured_facts(row),
                    ANSWER_KEY: ref,
                    "config_key": entry["config_key"],
                    "dataset_name": entry.get("dataset_name"),
                }
            )
        if not refs:
            return refs
        with self._lock:
            # First: no answer is ever on disk under a configuration nothing describes.
            self._register(entry, by_file)
            for file_key, filed in by_file.items():
                self._file(file_key).append(self._cell_path(file_key), filed)
        return refs

    def _register(self, entry: dict[str, Any], by_file: dict[str, list[dict[str, Any]]]) -> None:
        key = entry["config_key"]
        config_path = self._configs_dir() / f"{key}.json"
        described = {k: v for k, v in entry.items() if k not in ("dataset_name", "name")}
        if not config_path.exists():
            write_json(config_path, described)
        fold_key = f"{key}|{entry.get('dataset_name') or ''}"
        if fold_key in self._live_rows():
            return
        first = next(iter(by_file.values()))[0]
        append_row(
            self._index_path(),
            {
                _INDEX_FOLD_KEY: fold_key,
                **described,
                "dataset_name": entry.get("dataset_name"),
                "name": first.get("role") or "",
                "created_at": first.get("created_at") or "",
            },
        )

    def answer(self, ref: str) -> dict[str, Any] | None:
        file_key, _, _ = ref.partition(".")
        if not file_key or any(ch in ref for ch in "/\\") or ".." in ref:
            return None
        return self._file(file_key).by_id.get(ref)

    def population(self, entry: dict[str, Any]) -> list[dict[str, Any]]:
        dataset_name = entry.get("dataset_name")
        with self._lock:
            found = [
                row
                for file_key in _file_keys(entry)
                for row in self._file(file_key).rows
                if row.get("dataset_name") == dataset_name
            ]
        found.sort(key=lambda row: str(row.get("created_at") or ""))
        return found

    def list_all(self, *, dataset_name: str | None = None) -> list[dict[str, Any]]:
        entries = list(self._live_rows().values())
        if dataset_name is None:
            return entries
        return [e for e in entries if _entry_matches_dataset(e, dataset_name)]

    def entry(self, key: str, dataset_name: str | None) -> dict[str, Any] | None:
        return self._live_rows().get(f"{key}|{dataset_name or ''}")

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
        entry: dict[str, Any] | None = None,
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
    def _encode_cold(rows: Iterable[dict[str, Any]]) -> bytes:
        blob = "\n".join(json.dumps(r, separators=(",", ":"), default=str) for r in rows)
        return gzip.compress(blob.encode("utf-8"), 6)

    def cold_size(self, rows: Iterable[dict[str, Any]]) -> int:
        return len(self._encode_cold(rows))

    def cold_bytes_on_disk(self, file_key: str) -> int:
        try:
            return self.cold_path(file_key).stat().st_size
        except OSError:
            return 0

    def write_cold(self, file_key: str, rows: Iterable[dict[str, Any]]) -> int:
        data = self._encode_cold(rows)
        write_bytes(self.cold_path(file_key), data)
        return len(data)

    def read_cold(self, file_key: str) -> list[dict[str, Any]] | None:
        """``None`` = never compacted; ``[]`` = a compaction that moved nothing."""
        raw = read_bytes_optional(self.cold_path(file_key))
        if raw is None:
            return None
        text = gzip.decompress(raw).decode("utf-8")
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def drop_cold(self, file_key: str) -> int:
        path = self.cold_path(file_key)
        try:
            size = path.stat().st_size
        except OSError:
            return 0
        path.unlink(missing_ok=True)
        return size

    def reindex(self) -> dict[str, int]:
        first: dict[str, dict[str, Any]] = {}
        undescribed = 0
        for file_key in self.file_keys():
            for row in iter_jsonl(self._cell_path(file_key)):
                key, dataset_name = row.get("config_key"), row.get("dataset_name")
                if not isinstance(key, str):
                    continue
                fold_key = f"{key}|{dataset_name or ''}"
                held = first.get(fold_key)
                at = str(row.get("created_at") or "")
                if held is not None and held["created_at"] <= at:
                    continue
                described = read_json_optional(self._configs_dir() / f"{key}.json")
                if not isinstance(described, dict):
                    undescribed += 1
                    continue
                first[fold_key] = {
                    _INDEX_FOLD_KEY: fold_key,
                    **described,
                    "dataset_name": dataset_name,
                    "name": row.get("role") or "",
                    "created_at": at,
                }
        indexed = sorted(first.values(), key=lambda e: (e["created_at"], e[_INDEX_FOLD_KEY]))
        write_jsonl(self._index_path(), indexed)
        self._invalidate()
        return {"indexed": len(indexed), "undescribed": undescribed}

    def restamp_dataset(self, old_name: str, new_name: str) -> int:
        touched = {e["config_key"] for e in self.list_all(dataset_name=old_name)}
        if not touched:
            return 0
        with self._lock:
            for file_key in self.file_keys():
                lines = self.detail_lines(file_key)
                out: list[str] = []
                moved = False
                for line in lines:
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        out.append(line)
                        continue
                    if isinstance(row, dict) and row.get("dataset_name") == old_name:
                        row["dataset_name"] = new_name
                        line = json.dumps(row, ensure_ascii=False) + "\n"
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
    ) -> Iterator[Measurement]:
        if not predicate:
            return
        matching = [
            entry
            for entry in self.list_all(dataset_name=dataset_name)
            if (stored := entry.get("node_configs")) and _matches_subset(stored, predicate)
        ]
        if newest is not None:
            matching.sort(key=lambda entry: entry.get("created_at") or "", reverse=True)
            del matching[newest:]
        for entry in matching:
            chain = _chain(entry)
            for row in self.population(entry):
                yield Measurement(
                    answer=row[ANSWER_KEY],
                    config_key=entry["config_key"],
                    sample_id=int(row.get("sample_id", -1)),
                    node_configs=chain,
                    row=answer_facts(row),
                    created_at=str(row.get("created_at") or ""),
                )


def _reusable(row: dict[str, Any], grade: str) -> bool:
    return not is_error_result(row) and meets_grade(grade, REUSABLE_MIN_GRADE)


class CellClaim:
    """An OS lock the kernel drops with its holder: no expiry, so none can time a live holder out."""

    def __init__(self, lock: BaseFileLock, row_path: Path) -> None:
        self._lock = lock
        self.row_path = row_path

    def publish(self, row: dict[str, Any], *, grade: str) -> None:
        if not _reusable(row, grade) or is_deprecated(MeasuredCell.from_wire(row)):
            self.release()
            return
        # Windows refuses the replace while a waiter reads a past holder's row of this same cell.
        with contextlib.suppress(PermissionError):
            write_json(self.row_path, measured_facts(row))

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

    def advance(self) -> dict[str, ReplayableRow]:
        banked: list[dict[str, Any]] = []
        with self._archive._lock:
            for file_key in self._file_keys:
                rows = self._archive._file(file_key).rows
                banked.extend(rows[self._seen.get(file_key, 0) :])
                self._seen[file_key] = len(rows)
        banked.sort(key=lambda row: str(row.get("created_at") or ""))
        fresh: dict[str, ReplayableRow] = {}
        for row in banked:
            key = row.get("sample_key")
            if not key or not _reusable(row, str(row.get("provenance") or "C")):
                continue
            deprecated = is_deprecated(MeasuredCell.from_wire(row))
            if deprecated and self._deprecated.get(key) is False:
                continue
            self._deprecated[key] = deprecated
            fresh[key] = ReplayableRow(row.get("dataset_name"), answer_facts(row))
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

    def claimed_row(self, sample_key: str) -> dict[str, Any] | None:
        path = self._archive._claim_path(self._node_configs, sample_key)
        try:
            return read_json_optional(path.with_suffix(".json"))
        except PermissionError:
            # Windows: a holder is unlinking or replacing it this instant — the next poll reads again.
            return None


__all__ = [
    "ANSWER_KEY",
    "PROVENANCE_KEYS",
    "CellClaim",
    "MeasurementArchive",
    "ReplayFeed",
    "ReplayableRow",
    "answer_facts",
    "config_key",
    "standing",
]
