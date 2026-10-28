from __future__ import annotations

import contextlib
import gzip
import json
import os
import re
import threading
import uuid
from collections import OrderedDict
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import replace
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, NamedTuple

from promptpotter.config.paths import MEASUREMENTS_DIR
from promptpotter.domain.measurement_provenance import REUSABLE_MIN_GRADE, meets_grade
from promptpotter.domain.results_health import is_deprecated
from promptpotter.domain.sample import ArchiveConfig, ArchiveEntry, FiledAnswer
from promptpotter.domain.scoring import UNREAD_PIPELINE_KEYS, MeasuredCell, PipelineData
from promptpotter.infrastructure.store.io import (
    HeldLock,
    PairWrite,
    read_bytes_optional,
    read_json_optional,
    read_lines,
    replace_lines,
    rewrite_pair,
    take_lock,
    unlink_robust,
    write_json,
)
from promptpotter.infrastructure.store.read_model import (
    append_row,
    file_sig,
    fold_jsonl_from,
)
from promptpotter.shared.hashing import ADDRESS_HEX, CANONICAL_PIN, stable_hash

ANSWER_KEY = "answer"
_INDEX_FOLD_KEY = "k"
_FILES_MAX_BYTES = 32 << 20


_ADDRESS = re.compile(f"[0-9a-f]{{{ADDRESS_HEX}}}")


def is_address(key: str) -> bool:
    return _ADDRESS.fullmatch(key) is not None


class ArchiveFile(Enum):
    """Every file the archive writes, at ``{folder}/{key}{suffix}`` under ``measurements/``.
    A key is a content address; the unkeyed INDEX is one file, its key ``""``."""

    folder: str
    suffix: str
    keyed: bool

    INDEX = ("", "index.jsonl", False)
    CONFIG = ("configs", ".json", True)
    CELLS = ("cells", ".jsonl", True)
    COLD = ("cold", ".jsonl.gz", True)
    # One walk's claim on a cell: the lock, and the row its holder publishes under it.
    CLAIM_LOCK = ("claims", ".lock", True)
    CLAIM_ROW = ("claims", ".json", True)
    SAMPLE_FOLD = ("sample_folds", ".jsonl", True)

    def __init__(self, folder: str, suffix: str, keyed: bool) -> None:
        self.folder = folder
        self.suffix = suffix
        self.keyed = keyed

    def _admits(self, key: str) -> bool:
        return is_address(key) if self.keyed else key == ""

    def file_name(self, key: str) -> str:
        if not self._admits(key):
            raise ValueError(f"{self.name} files no key {key!r}: a key is a content address")
        return f"{key}{self.suffix}"

    def key_of(self, name: str) -> str | None:
        key = name[: len(name) - len(self.suffix)]
        return key if name.endswith(self.suffix) and self._admits(key) else None


assert all(
    (other.key_of(kind.file_name("0" * ADDRESS_HEX if kind.keyed else "")) is None)
    == (other is not kind)
    for kind in ArchiveFile
    for other in ArchiveFile
    if other.folder == kind.folder
), "two archive kinds name one file, so one kind's walker rewrites the other's"


def config_key(node_configs: list[tuple[str, dict[str, Any]]]) -> str:
    return stable_hash(node_configs, length=ADDRESS_HEX)


assert config_key([("solve", CANONICAL_PIN)]) == "f50c75d3792a48e09028a5e79dacd742", (
    "a moved configuration key strands every banked cell filed under the old one"
)


def _cell_key(node_configs: list[tuple[str, dict[str, Any]]], sample_key: str) -> str:
    return stable_hash([node_configs, sample_key], length=ADDRESS_HEX)


def _fold_key(key: str, dataset_hash: str) -> str:
    return f"{key}|{dataset_hash}"


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


def indexed_entry(row: Mapping[str, Any]) -> ArchiveEntry:
    return ArchiveEntry.model_validate({k: v for k, v in row.items() if k != _INDEX_FOLD_KEY})


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


def standing(answers: Iterable[FiledAnswer]) -> dict[str, FiledAnswer]:
    taken: dict[str, tuple[bool, FiledAnswer]] = {}
    for answer in answers:
        key = answer.cell.sample_key
        live = not (answer.cell.errored or is_deprecated(answer.cell))
        held = taken.get(key)
        if held is None or live or not held[0]:
            taken[key] = (live, answer)
    return {key: answer for key, (_, answer) in taken.items()}


class MovedFields(NamedTuple):
    """What a compaction took off one answer; ``pipeline`` holds those fields and no other."""

    answer: str
    pipeline: PipelineData


def rejoined(answer: FiledAnswer, moved: PipelineData) -> FiledAnswer:
    """Only FILLS: the answer on disk is the truth for what it still holds."""
    hot = answer.cell.pipeline
    back = {f: getattr(moved, f) for f in UNREAD_PIPELINE_KEYS if getattr(hot, f) is None}
    return replace(answer, cell=replace(answer.cell, pipeline=replace(hot, **back)))


class CellRewrite(NamedTuple):
    """A cell file's detail lines and its WHOLE cold payload after a rewrite; the last entry per answer wins."""

    lines: list[str]
    cold: list[MovedFields]


def _by_answer(moved: Iterable[MovedFields]) -> list[MovedFields]:
    return list({m.answer: m for m in moved}.values())


def _encode_cold(moved: list[MovedFields]) -> bytes | None:
    if not moved:
        return None
    blob = "\n".join(
        json.dumps({"k": m.answer, "pd": m.pipeline.wire()}, separators=(",", ":")) for m in moved
    )
    return gzip.compress(blob.encode("utf-8"), 6)


def _decode_cold(raw: bytes | None) -> list[MovedFields]:
    if raw is None:
        return []
    text = gzip.decompress(raw).decode("utf-8")
    held = [json.loads(line) for line in text.splitlines() if line.strip()]
    return [MovedFields(e["k"], PipelineData.from_wire(e["pd"])) for e in held]


def cold_size(moved: Iterable[MovedFields]) -> int:
    return len(_encode_cold(_by_answer(moved)) or b"")


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
        landed = append_row(path, *rows)
        st = path.stat()
        if not (
            whole
            and landed.start == held
            and st.st_size == landed.stop
            and _tail_from(st, self._cursor) == held
        ):
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

    def folder(self, kind: ArchiveFile) -> Path:
        return self._store_dir() / kind.folder

    def path(self, kind: ArchiveFile, key: str = "") -> Path:
        return self.folder(kind) / kind.file_name(key)

    def _named(self, kind: ArchiveFile) -> Iterator[tuple[str, os.DirEntry[str]]]:
        """The one enumeration: each file `path` builds for *kind*, beside its key."""
        try:
            with os.scandir(self.folder(kind)) as it:
                for entry in it:
                    key = kind.key_of(entry.name)
                    if key is not None and entry.is_file():
                        yield key, entry
        except OSError:
            return

    def keys(self, kind: ArchiveFile) -> list[str]:
        return sorted(key for key, _ in self._named(kind))

    def unwalked(self) -> list[Path]:
        """Every file here no `keys` names, at any depth; `store/io.py`'s dot-named locks and temp files aside."""
        named = {self.path(kind, key) for kind in ArchiveFile for key in self.keys(kind)}
        return sorted(
            path
            for top, _, names in os.walk(self._store_dir())
            for name in names
            if name[0] != "." and (path := Path(top) / name) not in named
        )

    def _index_path(self) -> Path:
        return self.path(ArchiveFile.INDEX)

    def cell_path(self, file_key: str) -> Path:
        return self.path(ArchiveFile.CELLS, file_key)

    def config(self, key: str) -> ArchiveConfig | None:
        raw = read_json_optional(self.path(ArchiveFile.CONFIG, key))
        return None if raw is None else ArchiveConfig.model_validate(raw)

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
            fresh = {fold_key: indexed_entry(row) for fold_key, row in folded.items()}
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
            held.refresh(self.cell_path(file_key))
            if fresh:
                weight = sum(f.size for f in self._files.values())
                while weight > _FILES_MAX_BYTES and len(self._files) > 1:
                    weight -= self._files.popitem(last=False)[1].size
            return held

    def answers(self, file_key: str) -> list[FiledAnswer]:
        with self._lock:
            return list(self._file(file_key).rows)

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
                    dataset_hash=entry.dataset_hash,
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
                self._file(file_key).append(self.cell_path(file_key), filed)
        return refs

    def _register(self, entry: ArchiveEntry) -> None:
        config_path = self.path(ArchiveFile.CONFIG, entry.config_key)
        if not config_path.exists():
            write_json(
                config_path, entry.model_dump(mode="json", include=set(ArchiveConfig.model_fields))
            )
        fold_key = _fold_key(entry.config_key, entry.dataset_hash)
        if fold_key in self._live_rows():
            return
        append_row(self._index_path(), {_INDEX_FOLD_KEY: fold_key, **entry.model_dump(mode="json")})

    def answer(self, ref: str) -> FiledAnswer | None:
        file_key, _, _ = ref.partition(".")
        if not is_address(file_key):
            return None
        return self._file(file_key).by_id.get(ref)

    def whole(self, answer: FiledAnswer) -> FiledAnswer:
        """*answer* with what a compaction moved off it; a purged answer comes back as it stands."""
        cold = self.read_cold(answer.answer.partition(".")[0])
        moved = next((m.pipeline for m in cold if m.answer == answer.answer), None)
        return answer if moved is None else rejoined(answer, moved)

    def population(self, entry: ArchiveEntry) -> list[FiledAnswer]:
        with self._lock:
            found = [
                answer
                for file_key in _file_keys(entry)
                for answer in self._file(file_key).rows
                if answer.dataset_hash == entry.dataset_hash
            ]
        found.sort(key=lambda answer: answer.created_at)
        return found

    def measured(self, entry: ArchiveEntry, sample_keys: frozenset[str]) -> list[FiledAnswer]:
        """The configuration's answers on *sample_keys*, under every bank that measured them."""
        with self._lock:
            found = [
                answer
                for file_key in _file_keys(entry)
                for answer in self._file(file_key).rows
                if answer.cell.sample_key in sample_keys
            ]
        found.sort(key=lambda answer: answer.created_at)
        return found

    def list_all(self, *, dataset_hash: str | None = None) -> list[ArchiveEntry]:
        entries = list(self._live_rows().values())
        if dataset_hash is None:
            return entries
        return [e for e in entries if e.dataset_hash == dataset_hash]

    def entry(self, key: str, dataset_hash: str) -> ArchiveEntry | None:
        return self._live_rows().get(_fold_key(key, dataset_hash))

    def cell_signatures(self) -> dict[str, tuple[int, int]]:
        out: dict[str, tuple[int, int]] = {}
        with contextlib.suppress(OSError):
            for key, entry in self._named(ArchiveFile.CELLS):
                st = entry.stat()
                out[key] = (st.st_mtime_ns, st.st_size)
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

    def version(self) -> tuple[object, ...]:
        """Stats only: moves on an index append, a cell file's write and a compaction."""
        return (
            str(self._base_dir),
            file_sig(self._index_path()),
            self.signature(),
            file_sig(self.folder(ArchiveFile.COLD)),
        )

    def files_signature(self, file_keys: Iterable[str]) -> tuple[Any, ...]:
        return tuple(file_sig(self.cell_path(key)) for key in sorted(set(file_keys)))

    def cold_path(self, file_key: str) -> Path:
        return self.path(ArchiveFile.COLD, file_key)

    def detail_lines(self, file_key: str) -> list[str]:
        return read_lines(self.cell_path(file_key))

    def rewrite_cell_file[T](
        self,
        file_key: str,
        transform: Callable[[list[str], list[MovedFields]], tuple[CellRewrite | None, T]],
    ) -> T:
        """*transform* takes RAW detail lines and the cold payload and must hand back every line it keeps.
        The cold file bridges through the union of both payloads: it grows before the lines swap and
        shrinks after, so every moved field is in one file or the other at every instant."""

        def paired(lines: list[str], held: bytes | None) -> tuple[PairWrite | None, T]:
            cold = _decode_cold(held)
            rewrite, result = transform(lines, cold)
            if rewrite is None:
                return None, result
            union = _by_answer([*cold, *rewrite.cold])
            bridge = held if union == cold else _encode_cold(union)
            after = _by_answer(rewrite.cold)
            final = bridge if after == union else _encode_cold(after)
            return PairWrite(rewrite.lines, bridge, final), result

        return rewrite_pair(self.cell_path(file_key), self.cold_path(file_key), paired)

    def cold_bytes_on_disk(self, file_key: str) -> int:
        try:
            return self.cold_path(file_key).stat().st_size
        except OSError:
            return 0

    def read_cold(self, file_key: str) -> list[MovedFields]:
        return _decode_cold(read_bytes_optional(self.cold_path(file_key)))

    def reindex(self) -> dict[str, int]:
        counts = replace_lines(self._index_path(), self._built_index)
        self._invalidate()
        return counts

    def _built_index(self) -> tuple[list[str], dict[str, int]]:
        first: dict[str, ArchiveEntry] = {}
        undescribed = 0
        for file_key in self.keys(ArchiveFile.CELLS):
            for answer in self._file(file_key).rows:
                fold_key = _fold_key(answer.config_key, answer.dataset_hash)
                held = first.get(fold_key)
                if held is not None and held.created_at <= answer.created_at:
                    continue
                described = self.config(answer.config_key)
                if described is None:
                    undescribed += 1
                    continue
                first[fold_key] = ArchiveEntry(
                    **described.model_dump(),
                    dataset_hash=answer.dataset_hash,
                    name=answer.role,
                    created_at=answer.created_at,
                )
        indexed = sorted(first.items(), key=lambda held: (held[1].created_at, held[0]))
        lines = [
            json.dumps({_INDEX_FOLD_KEY: k, **entry.model_dump(mode="json")}, ensure_ascii=False)
            + "\n"
            for k, entry in indexed
        ]
        return lines, {"indexed": len(indexed), "undescribed": undescribed}

    def measurements_for_config(
        self,
        predicate: dict[str, dict[str, Any]],
        *,
        dataset_hash: str | None = None,
        newest: int | None = None,
    ) -> Iterator[FiledAnswer]:
        matching = [
            entry
            for entry in self.list_all(dataset_hash=dataset_hash)
            if _matches_subset(entry.node_configs, predicate)
        ]
        if newest is not None:
            matching.sort(key=lambda entry: entry.created_at, reverse=True)
            del matching[newest:]
        for entry in matching:
            yield from self.population(entry)


class CellClaim:
    """An OS lock the kernel drops with its holder: no expiry, so none can time a live holder out."""

    def __init__(self, lock: HeldLock, row_path: Path) -> None:
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
            self._lock.retire()


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
        key = self.cell_key(sample_key)
        lock = take_lock(self._archive.path(ArchiveFile.CLAIM_LOCK, key), wait_s=0)
        if lock is None:
            return None
        claim = CellClaim(lock, self._archive.path(ArchiveFile.CLAIM_ROW, key))
        # A holder unlinks its row before it unlocks, so a row found here outlived its holder.
        _drop_row(claim.row_path)
        return claim

    def cell_key(self, sample_key: str) -> str:
        return _cell_key(self._node_configs, sample_key)

    def claimed_cell(self, sample_key: str) -> MeasuredCell | None:
        path = self._archive.path(ArchiveFile.CLAIM_ROW, self.cell_key(sample_key))
        try:
            row = read_json_optional(path)
        except PermissionError:
            # Windows: a holder is unlinking or replacing it this instant — the next poll reads again.
            return None
        return None if row is None else MeasuredCell.from_wire(row)
