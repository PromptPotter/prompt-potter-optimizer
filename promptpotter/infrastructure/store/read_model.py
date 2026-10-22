from __future__ import annotations

import json
import re
import threading
from abc import ABC, abstractmethod
from bisect import bisect_right
from collections import OrderedDict
from collections.abc import Callable, Hashable, Iterable, Iterator, Sequence
from itertools import islice
from pathlib import Path
from typing import Any, ClassVar, NamedTuple, Protocol, cast, get_args

from promptpotter.domain.run_records import SpendHoldRecord, TokenUsageRecord
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.io import append_line, open_text_robust
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


def append_row(path: Path, *rows: dict[str, Any]) -> range:
    lines = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
    return append_line(path, lines.encode("utf-8"))


Signature = tuple[int, int, int]


def file_sig(path: Path) -> Signature | None:
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    return st.st_ino, st.st_size, st.st_mtime_ns


RecordClasses = tuple[type[StrictModel], ...]


class LedgerFold[R: StrictModel, V](Protocol):
    """`feed` takes a line VALIDATED as one of `records`; `value` is a snapshot."""

    records: ClassVar[RecordClasses]

    def feed(self, offset: int, record: R) -> None: ...

    def value(self) -> V: ...


class LineFold[V](Protocol):
    """`feed` takes EVERY line as written, which is what reads an envelope alone; `value` is a snapshot."""

    def feed(self, offset: int, line: dict[str, Any]) -> None: ...

    def value(self) -> V: ...


def _by_record_type(folds: Iterable[LedgerFold[Any, Any]]) -> dict[str, type[StrictModel]]:
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


def _registered[K, I](registry: OrderedDict[K, I], key: K, make: Callable[[], I]) -> I:
    index = registry.get(key)
    if index is None:
        index = registry[key] = make()
        while len(registry) > LEDGER_INDEX_MAX:
            registry.popitem(last=False)
    else:
        registry.move_to_end(key)
    return index


class _LedgerTail(ABC):
    """A fold's `offset` is the PHYSICAL 0-based line index: what `CycleEventLog.append` assigns and an SSE `sequence` joins on."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._reset()

    def _reset(self) -> None:
        self._stopped: dict[type[Any], Exception] = {}
        self._sig: Signature | None = None
        self._bytes = 0
        self._lines = 0

    @abstractmethod
    def _feed(self, raw: bytes) -> None: ...

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

    def _take(self, raw: bytes, at: range) -> None:
        with self._lock:
            if self._sig is None or self._bytes != at.start:
                return
            self._feed(raw)
            self._lines += 1
            self._bytes = at.stop
            sig = file_sig(self._path)
            # Another process may have appended past ours already: a stale signature has the next view read it.
            if sig is not None and sig[0] == self._sig[0] and sig[1] == at.stop:
                self._sig = sig


def appended_here(path: Path, raw: bytes, at: range) -> None:
    with LedgerIndex._registry_lock:
        indexes: list[_LedgerTail] = [i for p, i in LedgerIndex._registry.items() if p == path]
    with LineIndex._registry_lock:
        indexes += [i for (p, _), i in LineIndex._registry.items() if p == path]
    for index in indexes:
        index._take(raw, at)


class LedgerIndex(_LedgerTail):
    """ONE per ledger, holding every typed fold any roster named: a line is screened and validated once."""

    _registry: ClassVar[OrderedDict[Path, LedgerIndex]] = OrderedDict()
    _screened: ClassVar[list[Callable[[], LedgerFold[Any, Any]]]] = []
    _registry_lock: ClassVar[threading.Lock] = threading.Lock()

    @classmethod
    def of(cls, path: Path, roster: Sequence[Callable[[], LedgerFold[Any, Any]]]) -> LedgerIndex:
        with cls._registry_lock:
            cls._screened.extend(fold for fold in roster if fold not in cls._screened)
            return _registered(cls._registry, path, lambda: cls(path))

    def _reset(self) -> None:
        super()._reset()
        self._folds = [make() for make in self._screened]
        self._by_type: dict[type[Any], LedgerFold[Any, Any]] = {
            type(fold): fold for fold in self._folds
        }
        self._models = _by_record_type(self._folds)
        probes = sorted(p.encode() for p in self._models)
        self._screen = re.compile(b'"(' + b"|".join(re.escape(p) for p in probes) + b')"')
        self._prefixes: dict[
            tuple[Callable[[], LedgerFold[Any, Any]], int], LedgerFold[Any, Any]
        ] = {}

    def view[V](self, fold: Callable[[], LedgerFold[Any, V]], until: int | None = None) -> V:
        with self._lock:
            if fold not in self._by_type:
                self._reset()
            self._refresh()
            if (stopped := self._stopped.get(cast("type[Any]", fold))) is not None:
                raise stopped
            if until is None or until >= self._lines:
                return cast("LedgerFold[Any, V]", self._by_type[cast("type[Any]", fold)]).value()
            prefix = self._prefixes.get((fold, until))
            if prefix is None:
                prefix = self._prefixes[(fold, until)] = self._fold_prefix(fold, until)
            return cast("LedgerFold[Any, V]", prefix).value()

    def _fold_prefix(
        self, fold: Callable[[], LedgerFold[Any, Any]], until: int
    ) -> LedgerFold[Any, Any]:
        made = fold()
        models = _by_record_type([made])
        probes = tuple(f'"{p}"'.encode() for p in models)
        for line, raw in enumerate(islice(_complete_lines(self._path, 0), until)):
            if not any(p in raw for p in probes):
                continue
            if (rec := _record_of(raw)) is None:
                continue
            if (model := _model_of(models, rec)) is not None:
                made.feed(line, model.model_validate(rec))
        return made

    def _feed(self, raw: bytes) -> None:
        if self._screen.search(raw) is None:
            return
        rec = _record_of(raw)
        model = None if rec is None else _model_of(self._models, rec)
        if model is None:
            return
        record: StrictModel | None = None
        for fold in self._folds:
            if type(fold) in self._stopped or model not in fold.records:
                continue
            try:
                if record is None:
                    record = model.model_validate(rec)
                fold.feed(self._lines, record)
            except Exception as exc:
                self._stopped[type(fold)] = exc


class LineIndex(_LedgerTail):
    """One per (ledger, roster): a line fold is fed every line, so it shares no screened index."""

    _registry: ClassVar[
        OrderedDict[tuple[Path, tuple[Callable[[], LineFold[Any]], ...]], LineIndex]
    ] = OrderedDict()
    _registry_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self, path: Path, roster: Sequence[Callable[[], LineFold[Any]]]) -> None:
        self._roster = tuple(roster)
        super().__init__(path)

    @classmethod
    def of(cls, path: Path, roster: tuple[Callable[[], LineFold[Any]], ...]) -> LineIndex:
        with cls._registry_lock:
            return _registered(cls._registry, (path, roster), lambda: cls(path, roster))

    def _reset(self) -> None:
        super()._reset()
        self._folds: dict[type[Any], LineFold[Any]] = {
            type(fold): fold for fold in (make() for make in self._roster)
        }

    def view[V](self, fold: Callable[[], LineFold[V]]) -> V:
        with self._lock:
            self._refresh()
            if (stopped := self._stopped.get(cast("type[Any]", fold))) is not None:
                raise stopped
            return cast("LineFold[V]", self._folds[cast("type[Any]", fold)]).value()

    def _feed(self, raw: bytes) -> None:
        rec = _record_of(raw)
        if rec is None:
            return
        for kind, fold in self._folds.items():
            if kind in self._stopped:
                continue
            try:
                fold.feed(self._lines, rec)
            except Exception as exc:
                self._stopped[kind] = exc


class _Clock:
    def __init__(self) -> None:
        self._at: list[float] = []

    def feed(self, offset: int, line: dict[str, Any]) -> None:
        last = self._at[-1] if self._at else float("-inf")
        own = epoch_seconds(line.get("timestamp"))
        self._at.extend([last] * (offset - len(self._at)))
        self._at.append(last if own is None else max(last, own))

    def value(self) -> tuple[float, ...]:
        return tuple(self._at)


class Moment(NamedTuple):
    ledger: Path
    offset: int

    @property
    def instant(self) -> float:
        """The wall clock its ledger had reached at its offset: what another store is cut at."""
        own = LineIndex.of(self.ledger, (_Clock,)).view(_Clock)
        return own[min(self.offset, len(own) - 1)] if own else float("-inf")

    def span(self, ledger: Path) -> LedgerSpan:
        if ledger == self.ledger:
            return LedgerSpan(ledger, self.offset + 1)
        other = LineIndex.of(ledger, (_Clock,)).view(_Clock)
        return LedgerSpan(ledger, bisect_right(other, self.instant))

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


HoldTrailRecord = SpendHoldRecord | TokenUsageRecord
HOLD_TRAIL: RecordClasses = get_args(HoldTrailRecord)


class HeldSends:
    """A usage record with neither a bill nor a rate's price closes its hold in tokens alone: the money stays held."""

    def __init__(self) -> None:
        self.open: dict[str, SpendHoldRecord] = {}
        self.unpriced: dict[str, float | None] = {}
        self._bounds: dict[str, float | None] = {}

    def track(self, record: HoldTrailRecord) -> None:
        if isinstance(record, SpendHoldRecord):
            self.open[record.hold_id] = record
            self._bounds[record.hold_id] = record.cost_usd
        elif (hold_id := record.hold_id) is not None:
            self.open.pop(hold_id, None)
            # A bill names a hold of another trail where a nested run's copy is carried onto its root.
            if hold_id in self._bounds and record.bill_or_rate_usd is None:
                self.unpriced[hold_id] = self._bounds[hold_id]


def held_tokens(hold: SpendHoldRecord) -> int:
    """An uncapped reply holds no tokens: such a send is admitted only where no token ceiling stands."""
    return 0 if hold.output_tokens is None else hold.input_tokens + hold.output_tokens
