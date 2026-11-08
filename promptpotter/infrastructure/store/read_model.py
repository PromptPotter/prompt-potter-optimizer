from __future__ import annotations

import copy
import json
import re
import threading
from abc import ABC, abstractmethod
from bisect import bisect_right
from collections import OrderedDict
from collections.abc import Callable, Hashable, Iterable, Sequence
from itertools import islice
from pathlib import Path
from typing import Any, ClassVar, NamedTuple, Protocol, cast, get_args, runtime_checkable

from pydantic import ValidationError

from promptpotter.domain.optimizer_state import UnregisteredStatePartError
from promptpotter.domain.run_records import (
    RECORD_KINDS,
    CycleRecord,
    SpendHoldRecord,
    TokenUsageRecord,
)
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.io import (
    ABSENT,
    FileMark,
    append_line,
    complete_lines,
    read_bytes_optional,
    read_tail,
)
from promptpotter.infrastructure.store.layout import CycleLayout, campaign_cycles_dir
from promptpotter.shared.clock import epoch_seconds
from promptpotter.shared.errors import (
    FileKind,
    FileUnreadableError,
    LedgerUnreadableError,
    validation_reason,
)

# Every `CycleRecord` arm: a fold of all of them reads every line, so a line no arm names refuses it.
EVERY_ARM: tuple[type[StrictModel], ...] = get_args(get_args(CycleRecord)[0])
_EVERY_ARM = frozenset(EVERY_ARM)
_ARM_KINDS = frozenset(kind.encode() for kind in RECORD_KINDS)


# What a line reader raises on a line it cannot take, a model's or a hand-built one's.
_ROW_ERRORS = (ValueError, KeyError, TypeError)


def _reason(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return validation_reason(exc, shown=1)
    return " ".join(f"{type(exc).__name__}: {exc}".split())


class Rows[R](NamedTuple):
    rows: list[R]
    mark: FileMark
    whole: bool


def parse_rows[R](
    path: Path, lines: Iterable[bytes], read: Callable[[bytes], R], *, at: int = 0
) -> list[R]:
    """*path*'s lines from byte *at*, a file that is no ledger: a line *read* refuses refuses them, by name."""
    rows: list[R] = []
    for raw in lines:
        if raw.strip():
            try:
                rows.append(read(raw))
            except _ROW_ERRORS as exc:
                reason = f"the line at byte {at}: {_reason(exc)}"
                raise FileUnreadableError(FileKind.STORE, path=str(path), reason=reason) from exc
        at += len(raw)
    return rows


def read_rows_past[R](path: Path, read: Callable[[bytes], R], mark: FileMark) -> Rows[R]:
    """A line file that is no ledger, past *mark*."""
    tail = read_tail(path, mark)
    rows = parse_rows(path, tail.lines, read, at=0 if tail.whole else mark.end)
    return Rows(rows, tail.mark, tail.whole)


def read_rows[R](path: Path, read: Callable[[bytes], R]) -> list[R]:
    return read_rows_past(path, read, ABSENT).rows


def read_file[R](
    path: Path, read: Callable[[bytes], R], kind: FileKind = FileKind.STORE
) -> R | None:
    """THE whole-file read of one that is no ledger: ``None`` where none is, and refused as a
    *kind* by its path where *read* refuses it."""
    raw = read_bytes_optional(path)
    if raw is None:
        return None
    try:
        return read(raw)
    except _ROW_ERRORS as exc:
        raise FileUnreadableError(kind, path=str(path), reason=_reason(exc)) from exc


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

    def feed(self, record: R) -> None: ...

    def value(self) -> V: ...


class LineFold[V](Protocol):
    """`feed` takes EVERY line as written, which is what reads an envelope alone; `value` is a snapshot."""

    def feed(self, line: dict[str, Any]) -> None: ...

    def value(self) -> V: ...


@runtime_checkable
class Positioned(Protocol):
    """A fold that reads the line it stands at, its own or not: its feeder stamps every line before feeding one."""

    at_offset: int


class _Feeds[T](Protocol):
    def feed(self, record: T, /) -> None: ...


def line_dict(raw: bytes) -> dict[str, Any]:
    rec = json.loads(raw)
    if not isinstance(rec, dict):
        raise ValueError(f"a ledger line holds a record, not a {type(rec).__name__}")
    return rec


def _refusal(ledger: Path, offset: int, reason: str) -> LedgerUnreadableError:
    cycle_dir = CycleLayout.of_ledger(ledger).cycle_dir
    campaign_dir = cycle_dir.parent.parent
    held = campaign_cycles_dir(campaign_dir) == cycle_dir.parent
    return LedgerUnreadableError(
        ledger=str(ledger),
        offset=offset,
        reason=reason,
        campaign_id=campaign_dir.name if held else None,
    )


def ledger_record[R](
    validate: Callable[[bytes], R], raw: bytes, *, ledger: Path, offset: int
) -> R | None:
    """Every ledger reader's one policy: a line it cannot take refuses the read."""
    if not raw.strip():
        return None
    try:
        return validate(raw)
    except ValueError as exc:
        raise _refusal(ledger, offset, _reason(exc)) from exc


def feed_fold[T](fold: _Feeds[T], record: T, *, ledger: Path, offset: int) -> None:
    """A fold that cannot take a line refuses as a line no model takes does: a writer's checkout and a
    refold then fail at one offset, where folding past it would serve a value missing that line."""
    try:
        fold.feed(record)
    except Exception as exc:
        reason = f"{type(fold).__name__} cannot fold it: {_reason(exc)}"
        raise _refusal(ledger, offset, reason) from exc


def _by_record_type(folds: Iterable[LedgerFold[Any, Any]]) -> dict[str, type[StrictModel]]:
    return {
        model.model_fields["record_type"].default: model for fold in folds for model in fold.records
    }


class LedgerSpan(NamedTuple):
    path: Path
    until: int | None = None


LEDGER_INDEX_MAX = 256
PREFIXES_MAX = 16
DERIVED_MAX = 4096


def _registered[K, I: _LedgerTail](registry: OrderedDict[K, I], key: K, make: Callable[[], I]) -> I:
    index = registry.get(key)
    if index is not None:
        registry.move_to_end(key)
        return index
    # No fold outlives its ledger: a deleted tree's indexes leave before another joins.
    for gone in [held for held, tail in registry.items() if not tail._path.exists()]:
        del registry[gone]
    index = registry[key] = make()
    while len(registry) > LEDGER_INDEX_MAX:
        registry.popitem(last=False)
    return index


class _LedgerTail(ABC):
    """A fold's `offset` is the PHYSICAL 0-based line index: what `CycleEventLog.append` assigns and an SSE `sequence` joins on."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._reset()

    def _reset(self) -> None:
        self._sig: Signature | None = None
        # A refusal rides the fold, so the mark's identity rule is what keeps a re-created file from
        # being read as this one's tail, and served this one's refusal.
        self._mark = ABSENT
        self._lines = 0

    @abstractmethod
    def _feed(self, raw: bytes) -> None: ...

    def _refresh(self) -> None:
        sig = file_sig(self._path)
        if sig == self._sig:
            return
        tail = read_tail(self._path, self._mark)
        if tail.whole:
            self._reset()
        for raw in tail.lines:
            self._feed(raw)
            self._lines += 1
        self._mark = tail.mark
        self._sig = None if tail.mark.inode is None else sig

    def _take(self, raw: bytes, at: range) -> None:
        with self._lock:
            if self._sig is None or self._mark.end != at.start:
                return
            self._feed(raw)
            self._lines += 1
            self._mark = FileMark(at.stop, self._mark.inode, raw + b"\n")
            sig = file_sig(self._path)
            # Another process may have appended past ours already: a stale signature has the next view read it.
            if sig is not None and sig[0] == self._mark.inode and sig[1] == at.stop:
                self._sig = sig


def appended_here(path: Path, raw: bytes, at: range) -> None:
    with LedgerIndex._registry_lock:
        indexes: list[_LedgerTail] = [i for p, i in LedgerIndex._registry.items() if p == path]
    with LineIndex._registry_lock:
        indexes += [i for (p, _), i in LineIndex._registry.items() if p == path]
    for index in indexes:
        index._take(raw, at)


type _Make = Callable[[], LedgerFold[Any, Any]]

# Any JSON spacing: a line rewritten through `io.write_jsonl` is as readable as the appender's compact one.
_RECORD_TYPE = re.compile(rb'"record_type"\s*:\s*"([^"]+)"')


class _Feeder:
    def __init__(self, ledger: Path, folds: dict[_Make, LedgerFold[Any, Any]]) -> None:
        self.folds = folds
        self._ledger = ledger
        self._models = {
            kind.encode(): model for kind, model in _by_record_type(folds.values()).items()
        }
        self._placed = [fold for fold in folds.values() if isinstance(fold, Positioned)]
        # By what it refuses, so the folds that never take a refused line (run state, spend) still
        # read whole: a reshape of one kind leaves the spend bank readable, so a delete can bank before
        # it wipes. Keyed by the model a line failed, by the fold that could not take a line (fed no
        # line after), or by `None` for a line no `CycleRecord` arm names, which a fold of every arm reads.
        self.refused: dict[
            type[StrictModel] | _Make | None, LedgerUnreadableError | UnregisteredStatePartError
        ] = {}

    def line(self, offset: int, raw: bytes) -> None:
        for placed in self._placed:
            placed.at_offset = offset
        kind = _RECORD_TYPE.search(raw)
        model = None if kind is None else self._models.get(kind[1])
        if model is None:
            if raw.strip() and (kind is None or kind[1] not in _ARM_KINDS):
                reason = "no record_type" if kind is None else f"no arm is {kind[1].decode()!r}"
                self.refused.setdefault(None, _refusal(self._ledger, offset, reason))
            return
        # The bytes go to pydantic's own parser: a `json.loads` first costs as much again.
        try:
            record = ledger_record(
                model.model_validate_json, raw, ledger=self._ledger, offset=offset
            )
        except (LedgerUnreadableError, UnregisteredStatePartError) as refusal:
            self.refused.setdefault(model, refusal)
            return
        if record is None:
            return
        for make, fold in self.folds.items():
            if model in fold.records and make not in self.refused:
                try:
                    feed_fold(fold, record, ledger=self._ledger, offset=offset)
                except LedgerUnreadableError as refusal:
                    self.refused[make] = refusal

    def vouch(self, make: _Make) -> None:
        records = self.folds[make].records
        keys = (make, *records, *((None,) if set(records) >= _EVERY_ARM else ()))
        for key in keys:
            if (refusal := self.refused.get(key)) is not None:
                raise refusal.with_traceback(None)


def _fresh[F](make: Callable[[], F], seed: F | None) -> F:
    made = make() if seed is None else copy.deepcopy(seed)
    if isinstance(made, Positioned):
        made.at_offset = -1
    return made


def _fold_lines(ledger: Path, feeder: _Feeder, until: int | None) -> None:
    try:
        with open(ledger, "rb") as fh:
            for offset, raw in enumerate(islice(complete_lines(fh), until)):
                feeder.line(offset, raw)
    except FileNotFoundError:
        return


def folded[F: LedgerFold[Any, Any]](
    ledger: Path, fold: Callable[[], F], *, seed: F | None, until: int | None
) -> F:
    """No index holds it, so nothing feeds it after: its caller alone owns what it folds on to."""
    made = _fresh(fold, seed)
    feeder = _Feeder(ledger, {fold: made})
    _fold_lines(ledger, feeder, until)
    feeder.vouch(fold)
    return made


class LedgerIndex(_LedgerTail):
    """ONE per ledger, holding every typed fold any roster named: a line is screened and validated once.
    A fold no roster names holds a slot in its own ledger's index alone, seeded with a fork's parent at its cut."""

    _registry: ClassVar[OrderedDict[Path, LedgerIndex]] = OrderedDict()
    _screened: ClassVar[list[_Make]] = []
    _registry_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self, path: Path) -> None:
        self._seeds: dict[_Make, LedgerFold[Any, Any] | None] = {}
        self._feeder = _Feeder(path, {})
        super().__init__(path)

    @classmethod
    def of(cls, path: Path, roster: Sequence[_Make]) -> LedgerIndex:
        with cls._registry_lock:
            cls._screened.extend(fold for fold in roster if fold not in cls._screened)
            return _registered(cls._registry, path, lambda: cls(path))

    def _reset(self) -> None:
        super()._reset()
        held = {make: _fresh(make, self._seeds.get(make)) for make in self._feeder.folds}
        self._feeder = _Feeder(self._path, held)
        self._prefixes: OrderedDict[tuple[_Make, int], LedgerFold[Any, Any]] = OrderedDict()
        self._cuts: dict[
            tuple[_Make, int], tuple[LedgerFold[Any, Any] | None, LedgerFold[Any, Any]]
        ] = {}

    def view[V](
        self,
        fold: Callable[[], LedgerFold[Any, V]],
        until: int | None = None,
        *,
        seed: LedgerFold[Any, V] | None = None,
    ) -> V:
        with self._lock:
            self._open(fold, seed)
            if until is None or until >= self._lines:
                self._feeder.vouch(fold)
                return cast("LedgerFold[Any, V]", self._feeder.folds[fold]).value()
            return cast("LedgerFold[Any, V]", self._prefix(fold, until)).value()

    def prefix[F: LedgerFold[Any, Any]](
        self, fold: Callable[[], F], until: int, *, seed: F | None = None
    ) -> F:
        """Held until this ledger is rewritten, so a fork's slot seeded off it reads a rewrite by identity;
        never fed again, so it may seed another ledger's slot outside this lock."""
        with self._lock:
            self._refresh()
            held = self._cuts.get((fold, until))
            if held is None or held[0] is not seed:
                cut = folded(self._path, fold, seed=seed, until=until)
                held = self._cuts[(fold, until)] = (seed, cut)
            return cast("F", held[1])

    def _open(self, fold: _Make, seed: LedgerFold[Any, Any] | None) -> None:
        self._refresh()
        held = self._feeder.folds
        if fold in self._screened:
            joining = {make: make() for make in self._screened if make not in held}
        elif fold not in held or self._seeds[fold] is not seed:
            self._seeds[fold] = seed
            joining = {fold: _fresh(fold, seed)}
        else:
            return
        if not joining:
            return
        # A fold joins at the line the others stand at, so none of them refolds for it.
        joiner = _Feeder(self._path, joining)
        _fold_lines(self._path, joiner, self._lines)
        refused = self._feeder.refused | joiner.refused
        self._feeder = _Feeder(self._path, held | joining)
        self._feeder.refused = refused
        for stale in [key for key in self._prefixes if key[0] in joining]:
            del self._prefixes[stale]

    def _prefix(self, fold: _Make, until: int) -> LedgerFold[Any, Any]:
        held = self._prefixes.pop((fold, until), None)
        if held is None:
            held = folded(self._path, fold, seed=self._seeds.get(fold), until=until)
        self._prefixes[(fold, until)] = held
        while len(self._prefixes) > PREFIXES_MAX:
            self._prefixes.popitem(last=False)
        return held

    def _feed(self, raw: bytes) -> None:
        self._feeder.line(self._lines, raw)


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
        self._placed = [fold for fold in self._folds.values() if isinstance(fold, Positioned)]
        self._refused: LedgerUnreadableError | None = None

    def view[V](self, fold: Callable[[], LineFold[V]]) -> V:
        with self._lock:
            self._refresh()
            if self._refused is not None:
                raise self._refused.with_traceback(None)
            return cast("LineFold[V]", self._folds[cast("type[Any]", fold)]).value()

    def _feed(self, raw: bytes) -> None:
        for placed in self._placed:
            placed.at_offset = self._lines
        try:
            line = ledger_record(line_dict, raw, ledger=self._path, offset=self._lines)
        except LedgerUnreadableError as refusal:
            self._refused = self._refused or refusal
            return
        if line is None or self._refused is not None:
            return
        try:
            for fold in self._folds.values():
                feed_fold(fold, line, ledger=self._path, offset=self._lines)
        except LedgerUnreadableError as refusal:
            self._refused = refusal


class _Clock:
    def __init__(self) -> None:
        self.at_offset = -1
        self._at: list[float] = []

    def feed(self, line: dict[str, Any]) -> None:
        last = self._at[-1] if self._at else float("-inf")
        own = epoch_seconds(line.get("timestamp"))
        self._at.extend([last] * (self.at_offset - len(self._at)))
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


def derived[T](key: Hashable, *, sig: Hashable, compute: Callable[[], T]) -> T:
    """The answer is shared between callers, so read-only; *compute* runs outside the lock and may run twice.
    An absent file's ``None`` is a signature like any other: its compute answers what absent reads as."""
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


class HeldSends[H]:
    """A bill with neither a provider's figure nor a rate's price closes its hold in tokens alone: the money stays held."""

    def __init__(self) -> None:
        self.open: dict[str, H] = {}
        # The holds closed unpriced, whose `cost_usd` stays held.
        self.unpriced: dict[str, H] = {}
        self._holds: dict[str, H] = {}

    def hold(self, hold_id: str, hold: H) -> None:
        self.open[hold_id] = hold
        self._holds[hold_id] = hold

    def close(self, hold_id: str, *, priced: bool) -> None:
        self.open.pop(hold_id, None)
        # A bill names a hold of another trail where a nested run's copy is carried onto its root.
        if hold_id in self._holds and not priced:
            self.unpriced[hold_id] = self._holds[hold_id]

    def track(self: HeldSends[SpendHoldRecord], record: HoldTrailRecord) -> None:
        if isinstance(record, SpendHoldRecord):
            self.hold(record.hold_id, record)
        elif record.hold_id is not None:
            self.close(record.hold_id, priced=record.bill_or_rate_usd is not None)
