"""Append-only JSONL read model — a save is one ``O_APPEND`` write, a read one last-wins fold. These
are the ONLY primitives for derived-index persistence; a second mechanism doing this job is a bug.

A POLLED read never re-reads what it already folded. :class:`LedgerIndex` holds a file's folds and
feeds them only the bytes appended since, and :func:`derived` holds anything else computed off a
file until that file's :func:`file_sig` moves. Neither trusts what it wrote itself: every read
stats first, and a file that was replaced (a new inode), shrank or went back in time is refolded
from zero — every rewriter here goes tmp + ``os.replace``, so a rewrite is always a new inode."""

from __future__ import annotations

import json
import re
import threading
from collections import OrderedDict
from collections.abc import Callable, Hashable, Iterable, Iterator, Sequence
from pathlib import Path
from typing import Any, ClassVar, Protocol, cast

from filelock import FileLock

from promptpotter.config.settings import LOCK_TIMEOUT
from promptpotter.infrastructure.store.io import append_jsonl, ensure_parent_dir, write_jsonl


def iter_jsonl(path: Path, *, record_types: frozenset[str] | None = None) -> list[dict[str, Any]]:
    """Every JSON object in *path*, in file order; a blank, corrupt or half-written trailing line degrades
    to "not there". *record_types* screens a line WITHOUT parsing it — a substring probe never misses."""
    probes = tuple(f'"{t}"' for t in record_types) if record_types else ()
    rows: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                # Probe the raw line BEFORE stripping or parsing: on a ledger the skipped
                # lines are the overwhelming majority, so anything spent per line before the
                # test is spent on rows nobody wants.
                if probes:
                    for probe in probes:
                        if probe in raw:
                            break
                    else:
                        continue
                line = raw.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except FileNotFoundError:
        return []
    return rows


def fold_jsonl(path: Path, key: str) -> dict[str, dict[str, Any]]:
    """Fold *path* into ``{row[key]: row}``, last line wins, first-seen order preserved."""
    out: dict[str, dict[str, Any]] = {}
    for row in iter_jsonl(path):
        k = row.get(key)
        if isinstance(k, str):
            out[k] = row
    return out


def _complete_lines(path: Path, start: int) -> Iterator[bytes]:
    """Each newline-terminated line from byte *start*. A torn trailing line is not yielded, so a
    reader's cursor never lands mid-record and the line is re-read once its newline lands."""
    with open(path, "rb") as fh:
        fh.seek(start)
        for raw in fh:
            if not raw.endswith(b"\n"):
                return
            yield raw


def _record_of(raw: bytes) -> dict[str, Any] | None:
    line = raw.strip()
    if not line:
        return None
    try:
        rec = json.loads(line)
    except ValueError:
        return None
    return rec if isinstance(rec, dict) else None


def fold_jsonl_from(path: Path, key: str, offset: int) -> tuple[dict[str, dict[str, Any]], int]:
    """Fold only the bytes from *offset* on, returning the next offset: the end of the last
    COMPLETE line, so a crash-truncated tail is re-read, never dropped."""
    out: dict[str, dict[str, Any]] = {}
    try:
        for raw in _complete_lines(path, offset):
            offset += len(raw)
            row = _record_of(raw)
            if row is not None and isinstance(k := row.get(key), str):
                out[k] = row
    except FileNotFoundError:
        return {}, offset
    return out, offset


def _lock_for(path: Path) -> FileLock:
    """The append/compact interlock for one log: ``<path>.lock``, parent ensured."""
    lock_path = path.with_suffix(path.suffix + ".lock")
    ensure_parent_dir(lock_path)
    return FileLock(str(lock_path), timeout=LOCK_TIMEOUT)


def append_row(path: Path, row: dict[str, Any]) -> None:
    """Append one upsert row — one ``O_APPEND`` write, no read, no rewrite. Held under the log's lock only
    to serialise against a concurrent :func:`compact`, which truncates and replaces."""
    with _lock_for(path):
        append_jsonl(path, row)


Signature = tuple[int, int, int]
"""``(st_ino, st_size, st_mtime_ns)`` — a file's identity and extent, in one stat."""


def file_sig(path: Path) -> Signature | None:
    """What a memo over *path* is keyed on; ``None`` where the file is absent."""
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    return st.st_ino, st.st_size, st.st_mtime_ns


class LedgerFold[V](Protocol):
    """One derivation over a ledger's records, fed in file order.

    ``probes`` screens a raw line WITHOUT parsing it, exactly as :func:`iter_jsonl`'s
    ``record_types`` does, so ``feed`` re-asserts the type on the parsed record; empty means every
    line. ``value`` hands out a snapshot a later ``feed`` cannot mutate."""

    probes: ClassVar[frozenset[str]]

    def feed(self, offset: int, rec: dict[str, Any]) -> None: ...

    def value(self) -> V: ...


LEDGER_INDEX_MAX = 256
DERIVED_MAX = 4096

_Roster = tuple[Callable[[], LedgerFold[Any]], ...]


class LedgerIndex:
    """A file's folds, kept current by reading only what was appended since the last read.

    ``offset`` handed to a fold is the PHYSICAL 0-based line index — a blank or unparseable line
    still consumes one — which is the space ``CycleEventLog.append`` assigns and a live SSE frame's
    ``sequence`` joins on. A torn trailing line is not folded until its newline lands."""

    _registry: ClassVar[OrderedDict[tuple[Path, _Roster], LedgerIndex]] = OrderedDict()
    _registry_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self, path: Path, roster: Sequence[Callable[[], LedgerFold[Any]]]) -> None:
        self._path = path
        self._roster: _Roster = tuple(roster)
        self._lock = threading.Lock()
        self._reset()

    @classmethod
    def of(cls, path: Path, roster: _Roster) -> LedgerIndex:
        """The process-wide index of *path* under *roster* — a module constant, so every reader
        of one roster shares one cursor."""
        key = (path, roster)
        with cls._registry_lock:
            index = cls._registry.get(key)
            if index is None:
                index = cls._registry[key] = cls(path, roster)
                while len(cls._registry) > LEDGER_INDEX_MAX:
                    cls._registry.popitem(last=False)
            else:
                cls._registry.move_to_end(key)
            return index

    def _reset(self) -> None:
        self._folds = [make() for make in self._roster]
        self._by_type = {type(fold): fold for fold in self._folds}
        probes = sorted({p for fold in self._folds for p in fold.probes})
        self._screens_all = any(not fold.probes for fold in self._folds)
        self._screen = re.compile(b'"(' + b"|".join(re.escape(p.encode()) for p in probes) + b')"')
        self._sig: Signature | None = None
        self._bytes = 0
        self._lines = 0

    def view[V](self, fold: Callable[[], LedgerFold[V]]) -> V:
        """*fold*'s value over the file as it stands now."""
        with self._lock:
            self._refresh()
            return cast("LedgerFold[V]", self._by_type[cast("type[Any]", fold)]).value()

    def _refresh(self) -> None:
        sig = file_sig(self._path)
        if sig == self._sig:
            return
        appended = (
            sig is not None
            and self._sig is not None
            and sig[0] == self._sig[0]
            and sig[1] >= self._bytes
            and sig[2] >= self._sig[2]
        )
        if not appended:
            self._reset()
        if sig is None:
            return
        try:
            for raw in _complete_lines(self._path, self._bytes):
                self._feed(raw)
                self._lines += 1
                self._bytes += len(raw)
        except FileNotFoundError:
            self._reset()
            return
        self._sig = sig

    def _feed(self, raw: bytes) -> None:
        hits = {m.decode() for m in self._screen.findall(raw)}
        if not hits and not self._screens_all:
            return
        rec = _record_of(raw)
        if rec is None:
            return
        for fold in self._folds:
            if not fold.probes or not hits.isdisjoint(fold.probes):
                fold.feed(self._lines, rec)


_DERIVED: OrderedDict[Hashable, tuple[Hashable, Any]] = OrderedDict()
_DERIVED_LOCK = threading.Lock()


def derived[T](key: Hashable, *, sig: Hashable | None, compute: Callable[[], T]) -> T | None:
    """*compute*'s answer for *key*, recomputed only when *sig* moves; ``None`` — and the entry
    dropped — where *sig* is ``None``, the file it stands for being gone. The answer is shared
    between callers, so it is read-only. *compute* runs outside the lock: two threads may both
    compute one key, and either answer is the right one."""
    if sig is None:
        with _DERIVED_LOCK:
            _DERIVED.pop(key, None)
        return None
    with _DERIVED_LOCK:
        hit = _DERIVED.get(key)
        if hit is not None and hit[0] == sig:
            _DERIVED.move_to_end(key)
            return cast("T", hit[1])
    value = compute()
    with _DERIVED_LOCK:
        _DERIVED[key] = (sig, value)
        _DERIVED.move_to_end(key)
        while len(_DERIVED) > DERIVED_MAX:
            _DERIVED.popitem(last=False)
    return value


HOLD_TRAIL = frozenset({"spend_hold", "token_usage"})
"""The record types an open-hold walk reads: a hold opens one, the bill naming it closes it."""


def open_holds(records: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Every hold on this trail that no bill closed, by hold id — the ONE fold of it.

    Two readers ask it, and they ask for different reasons: an account sums what its ledgers may
    still owe, a run's book asks what IT left open. Folded twice, the two could come to disagree
    about what "still open" means while both keep reporting money. What they may legitimately
    differ on is the LIVENESS question afterwards — whether an open hold is a send still out or
    one whose bill will never come — and that one is the caller's, asked at its own scope."""
    open_: dict[str, dict[str, Any]] = {}
    for rec in records:
        track_hold(open_, rec)
    return open_


def track_hold(open_: dict[str, dict[str, Any]], rec: dict[str, Any]) -> None:
    """One step of :func:`open_holds`, for a reader that folds the trail a record at a time."""
    if rec.get("record_type") == "spend_hold":
        open_[str(rec.get("hold_id"))] = rec
    elif (hold_id := rec.get("hold_id")) is not None:
        open_.pop(str(hold_id), None)


def compact(path: Path, key: str, *, factor: int = 2) -> bool:
    """Rewrite *path* keeping only the live row per *key*, once it has grown past *factor*× the live set;
    no-op when absent or already tight. Under the lock, so a concurrent append cannot be lost."""
    with _lock_for(path):
        rows = iter_jsonl(path)
        live: dict[str, dict[str, Any]] = {}
        for row in rows:
            k = row.get(key)
            if isinstance(k, str):
                live[k] = row
        if len(rows) <= factor * len(live):
            return False
        write_jsonl(path, live.values())
        return True


__all__ = [
    "HOLD_TRAIL",
    "LedgerFold",
    "LedgerIndex",
    "Signature",
    "append_row",
    "compact",
    "derived",
    "file_sig",
    "fold_jsonl",
    "fold_jsonl_from",
    "iter_jsonl",
    "open_holds",
    "track_hold",
]
