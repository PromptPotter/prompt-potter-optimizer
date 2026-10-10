from __future__ import annotations

import json
import os
import re
import threading
from bisect import bisect_right
from collections import OrderedDict
from collections.abc import Callable, Hashable, Iterable, Iterator, Sequence
from itertools import islice
from pathlib import Path
from typing import Any, ClassVar, NamedTuple, Protocol, cast

from filelock import FileLock

from promptpotter.config.settings import LOCK_TIMEOUT
from promptpotter.domain.spend import bill_or_rate_usd
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.io import (
    append_line,
    ensure_parent_dir,
    open_text_robust,
)
from promptpotter.shared.clock import epoch_seconds


def iter_jsonl(path: Path, *, record_types: frozenset[str] | None = None) -> list[dict[str, Any]]:
    probes = tuple(f'"{t}"' for t in record_types) if record_types else ()
    rows: list[dict[str, Any]] = []
    try:
        with open_text_robust(path) as fh:
            for raw in fh:
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
    out: dict[str, dict[str, Any]] = {}
    for row in iter_jsonl(path):
        k = row.get(key)
        if isinstance(k, str):
            out[k] = row
    return out


def _complete_lines(path: Path, start: int) -> Iterator[bytes]:
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
    lock_path = path.with_suffix(path.suffix + ".lock")
    ensure_parent_dir(lock_path)
    return FileLock(str(lock_path), timeout=LOCK_TIMEOUT)


def append_row(path: Path, *rows: dict[str, Any]) -> int:
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    # Locked only against a concurrent compaction, which truncates and replaces.
    with _lock_for(path):
        append_line(path, "\n".join(lines))
    return sum(len(line.encode("utf-8")) + len(os.linesep) for line in lines)


Signature = tuple[int, int, int]


def file_sig(path: Path) -> Signature | None:
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    return st.st_ino, st.st_size, st.st_mtime_ns


RecordClasses = tuple[type[StrictModel], ...]


class LedgerFold[V](Protocol):
    """`feed` takes a line VALIDATED as one of `records`; an empty roster takes every line as written, which is what reads an envelope alone. `value` is a snapshot."""

    records: ClassVar[RecordClasses]

    def feed(self, offset: int, record: Any) -> None: ...

    def value(self) -> V: ...


def _by_record_type(folds: Iterable[LedgerFold[Any]]) -> dict[str, type[StrictModel]]:
    return {
        model.model_fields["record_type"].default: model for fold in folds for model in fold.records
    }


def _model_of(
    models: dict[str, type[StrictModel]], rec: dict[str, Any]
) -> type[StrictModel] | None:
    kind = rec.get("record_type")
    return models.get(kind) if isinstance(kind, str) else None


class LedgerSpan(NamedTuple):
    path: Path
    until: int | None = None


LEDGER_INDEX_MAX = 256
DERIVED_MAX = 4096

_Roster = tuple[Callable[[], LedgerFold[Any]], ...]


class LedgerIndex:
    """A fold's `offset` is the PHYSICAL 0-based line index: what `CycleEventLog.append` assigns and an SSE `sequence` joins on."""

    _registry: ClassVar[OrderedDict[tuple[Path, _Roster | None], LedgerIndex]] = OrderedDict()
    _screened: ClassVar[list[Callable[[], LedgerFold[Any]]]] = []
    _registry_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(
        self, path: Path, roster: Sequence[Callable[[], LedgerFold[Any]]] | None = None
    ) -> None:
        self._path = path
        self._own = None if roster is None else tuple(roster)
        self._lock = threading.Lock()
        self._reset()

    @classmethod
    def of(cls, path: Path, roster: _Roster) -> LedgerIndex:
        shared = all(getattr(fold, "records", None) for fold in roster)
        key = (path, None if shared else roster)
        with cls._registry_lock:
            if shared:
                cls._screened.extend(fold for fold in roster if fold not in cls._screened)
            index = cls._registry.get(key)
            if index is None:
                index = cls._registry[key] = cls(path, key[1])
                while len(cls._registry) > LEDGER_INDEX_MAX:
                    cls._registry.popitem(last=False)
            else:
                cls._registry.move_to_end(key)
            return index

    def _reset(self) -> None:
        roster = tuple(self._screened) if self._own is None else self._own
        self._folds = [make() for make in roster]
        self._by_type = {type(fold): fold for fold in self._folds}
        self._stopped: dict[type[Any], Exception] = {}
        self._models = _by_record_type(self._folds)
        self._screens_all = any(not fold.records for fold in self._folds)
        probes = sorted(p.encode() for p in self._models)
        self._screen = re.compile(b'"(' + b"|".join(re.escape(p) for p in probes) + b')"')
        self._prefixes: dict[tuple[Callable[[], LedgerFold[Any]], int], LedgerFold[Any]] = {}
        self._sig: Signature | None = None
        self._bytes = 0
        self._lines = 0

    def view[V](self, fold: Callable[[], LedgerFold[V]], until: int | None = None) -> V:
        with self._lock:
            if fold not in self._by_type:
                self._reset()
            self._refresh()
            if (stopped := self._stopped.get(cast("type[Any]", fold))) is not None:
                raise stopped
            if until is None or until >= self._lines:
                return cast("LedgerFold[V]", self._by_type[cast("type[Any]", fold)]).value()
            prefix = self._prefixes.get((fold, until))
            if prefix is None:
                prefix = self._prefixes[(fold, until)] = self._fold_prefix(fold, until)
            return cast("LedgerFold[V]", prefix).value()

    def _fold_prefix(self, fold: Callable[[], LedgerFold[Any]], until: int) -> LedgerFold[Any]:
        made = fold()
        models = _by_record_type([made])
        probes = tuple(f'"{p}"'.encode() for p in models)
        for line, raw in enumerate(islice(_complete_lines(self._path, 0), until)):
            if probes and not any(p in raw for p in probes):
                continue
            if (rec := _record_of(raw)) is None:
                continue
            if not models:
                made.feed(line, rec)
            elif (model := _model_of(models, rec)) is not None:
                made.feed(line, model.model_validate(rec))
        return made

    def _refresh(self) -> None:
        sig = file_sig(self._path)
        if sig == self._sig:
            return
        # A rewrite is always a new inode: every rewriter here goes tmp + `os.replace`.
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
        if not self._screens_all and self._screen.search(raw) is None:
            return
        rec = _record_of(raw)
        if rec is None:
            return
        model = _model_of(self._models, rec)
        record: StrictModel | None = None
        for fold in self._folds:
            if type(fold) in self._stopped:
                continue
            try:
                if not fold.records:
                    fold.feed(self._lines, rec)
                elif model is not None and model in fold.records:
                    if record is None:
                        record = model.model_validate(rec)
                    fold.feed(self._lines, record)
            except Exception as exc:
                self._stopped[type(fold)] = exc


class _Clock:
    records: ClassVar[RecordClasses] = ()

    def __init__(self) -> None:
        self._at: list[float] = []

    def feed(self, offset: int, rec: dict[str, Any]) -> None:
        last = self._at[-1] if self._at else float("-inf")
        own = epoch_seconds(rec.get("timestamp"))
        self._at.extend([last] * (offset - len(self._at)))
        self._at.append(last if own is None else max(last, own))

    def value(self) -> tuple[float, ...]:
        return tuple(self._at)


class Moment(NamedTuple):
    ledger: Path
    offset: int

    def span(self, ledger: Path) -> LedgerSpan:
        if ledger == self.ledger:
            return LedgerSpan(ledger, self.offset + 1)
        own = LedgerIndex.of(self.ledger, (_Clock,)).view(_Clock)
        if not own:
            return LedgerSpan(ledger, 0)
        instant = own[min(self.offset, len(own) - 1)]
        other = LedgerIndex.of(ledger, (_Clock,)).view(_Clock)
        return LedgerSpan(ledger, bisect_right(other, instant))

    def spans(self, chain: Sequence[LedgerSpan]) -> list[LedgerSpan]:
        out: list[LedgerSpan] = []
        for held in chain:
            then = self.span(held.path).until
            assert then is not None
            out.append(LedgerSpan(held.path, then if held.until is None else min(held.until, then)))
        return out


_DERIVED: OrderedDict[Hashable, tuple[Hashable, Any]] = OrderedDict()
_DERIVED_LOCK = threading.Lock()


def derived[T](key: Hashable, *, sig: Hashable | None, compute: Callable[[], T]) -> T | None:
    """The answer is shared between callers, so read-only; *compute* runs outside the lock and may run twice."""
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


def _usd(raw: object) -> float | None:
    return float(raw) if isinstance(raw, int | float) and not isinstance(raw, bool) else None


def usage_row_figures(rec: dict[str, Any]) -> tuple[float | None, float | None]:
    return _usd(rec.get("cost_usd")), _usd(rec.get("rate_priced_usd"))


def usage_row_usd(rec: dict[str, Any]) -> float | None:
    return bill_or_rate_usd(*usage_row_figures(rec))


class HeldSends:
    """A usage record with neither a bill nor a rate's price closes its hold in tokens alone: the money stays held."""

    def __init__(self) -> None:
        self.open: dict[str, dict[str, Any]] = {}
        self.unpriced: dict[str, Any] = {}
        self._bounds: dict[str, Any] = {}

    def track(self, rec: dict[str, Any]) -> None:
        if (hold_id := rec.get("hold_id")) is None:
            return
        hold_id = str(hold_id)
        if rec.get("record_type") == "spend_hold":
            self.open[hold_id] = rec
            self._bounds[hold_id] = rec.get("cost_usd")
            return
        self.open.pop(hold_id, None)
        # A bill names a hold of another trail where a nested run's copy is carried onto its root.
        if hold_id in self._bounds and usage_row_usd(rec) is None:
            self.unpriced[hold_id] = self._bounds[hold_id]


def held_tokens(hold: dict[str, Any]) -> int:
    """An uncapped reply holds no tokens: such a send is admitted only where no token ceiling stands."""
    reply = hold["output_tokens"]
    return 0 if reply is None else int(hold["input_tokens"]) + int(reply)


__all__ = [
    "HOLD_TRAIL",
    "HeldSends",
    "LedgerFold",
    "LedgerIndex",
    "LedgerSpan",
    "Moment",
    "RecordClasses",
    "Signature",
    "append_row",
    "derived",
    "file_sig",
    "fold_jsonl",
    "fold_jsonl_from",
    "held_tokens",
    "iter_jsonl",
    "usage_row_figures",
    "usage_row_usd",
]
