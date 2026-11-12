from __future__ import annotations

import copy
import json
import re
import threading
from abc import ABC, abstractmethod
from bisect import bisect_right
from collections import OrderedDict, deque
from collections.abc import Callable, Hashable, Iterable, Sequence
from functools import partial
from itertools import islice
from pathlib import Path
from typing import (
    Any,
    ClassVar,
    NamedTuple,
    Protocol,
    assert_never,
    cast,
    get_args,
    runtime_checkable,
)

from pydantic import ValidationError

from promptpotter.domain.run_records import (
    RECORD_ADAPTER,
    Bill,
    CycleRecord,
    Hold,
    Lapse,
    Release,
    TokenUsageRecord,
    stale_file_warning,
)
from promptpotter.domain.spend import (
    NOTHING_PAID,
    Budget,
    CeilingMeter,
    PaidSpend,
    SpendRollup,
    UserSpend,
    draws_coupon,
    metered,
    spend_slot,
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
_ARM_MODELS = {arm.model_fields["record_type"].default.encode(): arm for arm in EVERY_ARM}


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
    # No fold outlives its ledger: a deleted tree's indexes leave before another joins; a pinned one stays.
    for gone in [
        held for held, tail in registry.items() if not (tail.pinned or tail._path.exists())
    ]:
        del registry[gone]
    index = registry[key] = make()
    unpinned = [held for held, tail in registry.items() if not tail.pinned]
    for gone in unpinned[: max(0, len(registry) - LEDGER_INDEX_MAX)]:
        del registry[gone]
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

    @property
    def pinned(self) -> bool:
        return False

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

    def _take(self, raw: bytes, at: range) -> bool:
        """Under the lock: whether *raw*, appended at *at*, was this tail's next line, now fed."""
        if self._sig is None or self._mark.end != at.start:
            return False
        self._feed(raw)
        self._lines += 1
        self._mark = FileMark(at.stop, self._mark.inode, raw + b"\n")
        sig = file_sig(self._path)
        # Another process may have appended past ours already: a stale signature has the next view read it.
        if sig is not None and sig[0] == self._mark.inode and sig[1] == at.stop:
            self._sig = sig
        return True


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
        self.refused: dict[type[StrictModel] | _Make | None, LedgerUnreadableError] = {}

    def line(self, offset: int, raw: bytes) -> None:
        for placed in self._placed:
            placed.at_offset = offset
        kind = _RECORD_TYPE.search(raw)
        model = None if kind is None else self._models.get(kind[1])
        if model is None:
            if raw.strip() and (kind is None or kind[1] not in _ARM_MODELS):
                reason = "no record_type" if kind is None else f"no arm is {kind[1].decode()!r}"
                self.refused.setdefault(None, _refusal(self._ledger, offset, reason))
            return
        # The bytes go to pydantic's own parser: a `json.loads` first costs as much again.
        try:
            record = ledger_record(
                model.model_validate_json, raw, ledger=self._ledger, offset=offset
            )
        except LedgerUnreadableError as refusal:
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


@runtime_checkable
class Projected(Protocol):
    """A fold that writes (`projections/base.py::Projection`), fed after its index's folds."""

    def feed(self, record: CycleRecord) -> None: ...

    def drain(self) -> None: ...


class Projecting:
    """A drainer's hold on projections of one ledger: the last holder of a projection to close drains it."""

    def __init__(self, index: LedgerIndex) -> None:
        self._index = index
        self._kinds: list[type[Any]] | None = []

    def add[P: Projected](self, projection: P) -> P:
        assert self._kinds is not None, "a closed Projecting holds nothing more"
        held = self._index._join(projection)
        self._kinds.append(type(projection))
        return held

    def close(self, *, drain: bool = True) -> None:
        kinds, self._kinds = self._kinds or [], None
        for projection in [self._index._leave(kind) for kind in kinds]:
            if projection is not None and drain:
                self._index.shielded(projection, projection.drain)

    def __enter__(self) -> Projecting:
        return self

    def __exit__(self, raised: object, *_: object) -> None:
        # An error in flight leaves them undrained: a drain's own fault never replaces it.
        self.close(drain=raised is None)


class LedgerIndex(_LedgerTail):
    """ONE per ledger, holding every screened fold the roster declares: a line is screened and
    validated once. A projection, or a fold the roster does not declare, holds a slot in its own
    ledger's index alone, seeded with a fork's parent at its cut. Its projections are fed outside
    its lock, so one may view this index while it is fed."""

    _registry: ClassVar[OrderedDict[Path, LedgerIndex]] = OrderedDict()
    # Every fold a reader declares (`readers`), whichever index it is read on.
    _readers: ClassVar[list[type[Any]]] = []
    # The declared folds that write nothing: every index folds them together.
    _screened: ClassVar[list[_Make]] = []
    _registry_lock: ClassVar[threading.Lock] = threading.Lock()

    @classmethod
    def readers(cls, *folds: type[Any]) -> None:
        """The roster every reader of a ledger declares its folds on, a `Projection` by subclassing."""
        with cls._registry_lock:
            cls._readers.extend(folds)
            cls._screened.extend(
                fold
                for fold in folds
                if not issubclass(fold, Projected) and fold not in cls._screened
            )

    @classmethod
    def check_every_arm_read(cls) -> None:
        """Every `CycleRecord` arm is a `records` arm of a fold on the roster: a kind none reads is a
        state the system enters and reports nowhere. Run once the readers are imported."""
        read = {arm for fold in cls._readers for arm in fold.records}
        if unread := sorted(k.decode() for k, arm in _ARM_MODELS.items() if arm not in read):
            raise RuntimeError(f"No fold on the roster reads these record kinds: {unread}.")

    def __init__(self, path: Path) -> None:
        self._seeds: dict[_Make, LedgerFold[Any, Any] | None] = {}
        self._feeder = _Feeder(path, {})
        # A projection by its type, with how many hold it; the lines it waits on, in append order.
        self._held: dict[type[Any], tuple[Projected, int]] = {}
        # The held types whose fault is reported: one warning per projection while it is held.
        self._stale: set[type[Any]] = set()
        self._queue: deque[tuple[int, bytes, list[Projected], CycleRecord | None]] = deque()
        self._draining = threading.Lock()
        self._drainer: int | None = None
        self._until = -1
        super().__init__(path)

    @property
    def pinned(self) -> bool:
        return bool(self._held)

    def projecting(self) -> Projecting:
        return Projecting(self)

    def _join[P: Projected](self, projection: P) -> P:
        """A type held already is the held instance; a new one is fed from the line the index stands at."""
        with self._lock:
            held, holders = self._held.get(type(projection), (projection, 0))
            if not holders:
                self._refresh()
                self._stale.discard(type(projection))
            self._held[type(projection)] = (held, holders + 1)
            return cast("P", held)

    def _leave(self, kind: type[Any]) -> Projected | None:
        """The projection its last holder left, to drain: no line feeds it again."""
        with self._lock:
            held, holders = self._held[kind]
            if holders > 1:
                self._held[kind] = (held, holders - 1)
                return None
            del self._held[kind]
            return held

    def shielded(
        self, projection: Projected, call: Callable[[], None], at: int | None = None
    ) -> None:
        """Every call into a projection: a fault of any type lands as one `derived_file_stale` warning on
        this ledger, never a raise into the append whose line landed, and nothing is held for a later one."""
        try:
            call()
        except Exception as fault:  # noqa: BLE001 -- a derived file's fault of any kind is its warning
            with self._lock:
                if type(projection) in self._stale:
                    return
                self._stale.add(type(projection))
                at = self._lines - 1 if at is None else at
            warning = stale_file_warning(type(projection).__name__, at, _reason(fault))
            raw = RECORD_ADAPTER.dump_json(warning, fallback=str)
            # A ledger that takes the line, then refuses the warning, raises: that fault is the ledger's.
            self.land(raw, append_line(self._path, raw), warning)

    def line_count(self) -> int:
        with self._lock:
            self._refresh()
            return self._lines

    def land(self, raw: bytes, at: range, record: CycleRecord) -> int:
        """*raw*, just appended at *at* as *record*: its offset, once its projections were handed every line
        up to it in append order, *record* itself, so a fold keyed by identity knows its own append."""
        # The run's own controls poll this ledger per cell; its own appends must not cost them a read.
        with LineIndex._registry_lock:
            lines = [i for (p, _), i in LineIndex._registry.items() if p == self._path]
        for line_index in lines:
            with line_index._lock:
                line_index._take(raw, at)
        with self._lock:
            if not self._take(raw, at):
                self._refresh()
            offset = self._lines - 1
            if self._mark.end > at.stop:
                with open(self._path, "rb") as fh:
                    fh.seek(at.stop)
                    offset -= fh.read(self._mark.end - at.stop).count(b"\n")
            for i, (queued, line, held, _) in enumerate(self._queue):
                if queued == offset:
                    self._queue[i] = (queued, line, held, record)
        # The drainer's own re-entrant append is fed after the line it is feeding; any other waits.
        if self._drainer == threading.get_ident():
            self._until = max(self._until, offset)
            return offset
        with self._draining:
            self._drainer, self._until = threading.get_ident(), offset
            try:
                while True:
                    with self._lock:
                        # A later line is its own appender's to feed, or the next append's.
                        if not self._queue or self._queue[0][0] > self._until:
                            return offset
                        at_line, line, fed_then, carried = self._queue.popleft()
                        # Who is held now, never when the line was queued: a dropped one is fed no more.
                        held = [p for p in fed_then if self._held.get(type(p), (None,))[0] is p]
                    kind = _RECORD_TYPE.search(line)
                    model = None if kind is None else _ARM_MODELS.get(kind[1])
                    if model is None:
                        continue
                    read = carried or ledger_record(
                        model.model_validate_json, line, ledger=self._path, offset=at_line
                    )
                    fed = cast("CycleRecord", read)
                    for projection in held:
                        if isinstance(projection, Positioned):
                            projection.at_offset = at_line
                        self.shielded(projection, partial(projection.feed, fed), at_line)
            finally:
                self._drainer = None

    @classmethod
    def of(cls, path: Path) -> LedgerIndex:
        with cls._registry_lock:
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

    def live[F: LedgerFold[Any, Any]](self, fold: Callable[[], F], *, seed: F | None = None) -> F:
        """The slot itself, fed on past this call: a field held off it reads each line as it lands."""
        with self._lock:
            self._open(fold, seed)
            self._feeder.vouch(fold)
            return cast("F", self._feeder.folds[fold])

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
        if self._held:
            self._queue.append((self._lines, raw, [held for held, _ in self._held.values()], None))


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


def _billed(bill: Bill) -> UserSpend:
    price, tokens = bill.price, bill.tokens
    if price.cost_usd is None and price.rate_priced_usd is None:
        return UserSpend(0.0, tokens, tokens)
    return UserSpend(price.cost_usd or 0.0, tokens, 0, rate_priced_usd=price.rate_priced_usd or 0.0)


def _unreported_of(hold: Hold) -> PaidSpend:
    """Unpriced lands in the token residue: `$0.00` would report an unpriceable send as a free one."""
    bound = hold.bound
    tokens = bound.tokens or 0
    if bound.usd is None:
        return PaidSpend.of(hold.key_source, UserSpend(0.0, 0, tokens, 0.0, tokens))
    return PaidSpend.of(hold.key_source, UserSpend(0.0, 0, 0, bound.usd, tokens))


def _unpriced_of(hold: Hold) -> PaidSpend:
    """Its bill already filed the tokens, as residue (:func:`_billed`); only the money is kept."""
    usd = hold.bound.usd
    return NOTHING_PAID if usd is None else PaidSpend.of(hold.key_source, UserSpend(0.0, 0, 0, usd))


def still_held(open_holds: Iterable[Hold], unpriced: Iterable[Hold]) -> PaidSpend:
    total = NOTHING_PAID
    for hold in open_holds:
        total = total.plus(_unreported_of(hold))
    for hold in unpriced:
        total = total.plus(_unpriced_of(hold))
    return total


class Spent(NamedTuple):
    # What the ledger's subject COST: every answered send, a nested run's mirrored onto it included.
    cost: PaidSpend
    # What its own sends billed: the cost less the mirrored sends a nested run's own ledger bills.
    billed: PaidSpend

    def plus(self, spent: Spent) -> Spent:
        return Spent(self.cost.plus(spent.cost), self.billed.plus(spent.billed))


MoneyRecord = Hold | Bill | Release | Lapse | TokenUsageRecord


class Money(NamedTuple):
    """One subject's money: the account's charge book, or one ledger's OWN lines."""

    spent: Spent
    # Each hold no bill or release closed yet.
    open: dict[str, Hold]
    # Each hold, at its latest bound.
    holds: dict[str, Hold]
    # What was drawn under each hold, by every call beneath it: its bills, a lapse at its bound,
    # an unpriced bill's bound money.
    drawn: dict[str, PaidSpend]
    # The holds a bill closed in tokens alone, naming neither a provider's figure nor a rate's
    # price: the bound's money stays held.
    unpriced: tuple[Hold, ...]
    # Each send a lapse closed, at what it may have cost.
    lapsed: tuple[Hold, ...]
    bills: tuple[Bill, ...]
    # Every call, a replay included, as each meter counts it.
    usage: tuple[TokenUsageRecord, ...]
    metered: dict[CeilingMeter, Budget]
    by_round: dict[int, SpendRollup]
    calls: int
    worked_s: float

    def holding(self, hold_id: str) -> bool:
        """Whether *hold_id* and every hold above it is still open."""
        above: str | None = hold_id
        while above is not None:
            if above not in self.open:
                return False
            above = self.open[above].parent
        return True

    def unreported_holds(self) -> tuple[list[Hold], list[Hold]]:
        """The sends no open hold covers, at their bound and at its money alone: each send left
        open under a closed hold, each lapsed one and each unpriced one."""
        left = [
            hold
            for hold in self.open.values()
            if hold.ledger is not None and not self.holding(hold.hold_id)
        ]
        return [*left, *self.lapsed], list(self.unpriced)

    @property
    def unreported(self) -> PaidSpend:
        return still_held(*self.unreported_holds())

    def _spent(self, hold_id: str, host: bool) -> UserSpend:
        drawn = self.drawn.get(hold_id, NOTHING_PAID)
        return drawn.host if host else drawn.total

    def _below(self, hold_id: str, host: bool) -> tuple[float, int]:
        below = [
            self.kept(child, host=host)
            for child in self.open.values()
            if child.parent == hold_id and (not host or draws_coupon(child.key_source))
        ]
        return sum(u for u, _ in below), sum(t for _, t in below)

    def kept(self, hold: Hold, *, host: bool) -> tuple[float, int]:
        """What *hold* still holds of the money above it (*host*: of the coupon): on a bounded
        axis its bound less what was drawn under it, never below what its open children hold; on
        an unbounded one what they hold."""
        usd, tokens = self._below(hold.hold_id, host)
        bound, spent = hold.bound, self._spent(hold.hold_id, host)
        return (
            usd if bound.usd is None else max(usd, bound.usd - spent.at_most_usd),
            tokens if bound.tokens is None else max(tokens, bound.tokens - spent.at_most_tokens),
        )

    def left(self, hold: Hold) -> tuple[float | None, int | None]:
        """What *hold* still admits: its bound less what was drawn under it and what its open
        children hold; ``None`` on an axis it leaves unbounded."""
        usd, tokens = self._below(hold.hold_id, False)
        bound, spent = hold.bound, self._spent(hold.hold_id, False)
        return (
            None if bound.usd is None else bound.usd - spent.at_most_usd - usd,
            None if bound.tokens is None else bound.tokens - spent.at_most_tokens - tokens,
        )

    def held_back(self, excluding: str | None) -> Budget:
        """What the open holds on the account still hold of its coupon, from each root but
        *excluding* (`kept`): a bill past a bound is drawn all the same."""
        roots = [
            self.kept(hold, host=True)
            for hold in self.open.values()
            if hold.parent is None and draws_coupon(hold.key_source) and hold.hold_id != excluding
        ]
        return Budget.amount(sum(u for u, _ in roots), sum(t for _, t in roots))


class _Money:
    """THE fold of money: a hold opens under its parent, each bill closes a call's and draws on
    every hold above it, a release closes one, a lapse closes a call's and draws what it may have
    cost, a replay is priced and charges nothing."""

    records: ClassVar[RecordClasses] = get_args(MoneyRecord)

    def __init__(self) -> None:
        self._spent = Spent(NOTHING_PAID, NOTHING_PAID)
        self._open: dict[str, Hold] = {}
        self._drawn: dict[str, PaidSpend] = {}
        self._holds: dict[str, Hold] = {}
        self._unpriced: dict[str, Hold] = {}
        self._lapsed: list[Hold] = []
        self._bills: list[Bill] = []
        self._usage: list[TokenUsageRecord] = []
        self._usd: dict[CeilingMeter, float] = dict.fromkeys(get_args(CeilingMeter), 0.0)
        self._tokens: dict[CeilingMeter, int] = dict.fromkeys(get_args(CeilingMeter), 0)
        self._by_round: dict[int, SpendRollup] = {}
        self._calls = 0
        self._worked_s = 0.0
        # A copy, since the next feed banks into the live rollups; one per feed, not per read.
        self._view: Money | None = None

    def feed(self, record: MoneyRecord) -> None:
        self._view = None
        match record:
            case Hold():
                self._hold(record)
            case Bill():
                self._bill(record, mirrored=False)
            case Release():
                self._open.pop(record.hold_id, None)
            case Lapse():
                self._open.pop(record.hold_id, None)
                lapsed = self._holds[record.hold_id].model_copy(update={"bound": record.bound})
                self._lapsed.append(lapsed)
                self._draw(lapsed.parent, _unreported_of(lapsed))
            case TokenUsageRecord():
                self._call(record)
            case _:
                assert_never(record)

    def _hold(self, hold: Hold) -> None:
        self._holds[hold.hold_id] = hold
        self._open[hold.hold_id] = hold

    def _bill(self, bill: Bill, *, mirrored: bool) -> None:
        paid = PaidSpend.of(bill.key_source, _billed(bill))
        self._spent = Spent(
            self._spent.cost.plus(paid),
            self._spent.billed if mirrored else self._spent.billed.plus(paid),
        )
        self._bills.append(bill)
        # A bill names a hold of another trail where a nested run's copy is carried onto its root.
        if (held := self._holds.get(bill.hold_id)) is None:
            return
        self._draw(held.parent, paid)
        self._open.pop(held.hold_id, None)
        if bill.price.bill_or_rate_usd is None and held.hold_id not in self._unpriced:
            self._unpriced[held.hold_id] = held
            self._draw(held.parent, _unpriced_of(held))

    def _draw(self, above: str | None, paid: PaidSpend) -> None:
        while above is not None:
            self._drawn[above] = self._drawn.get(above, NOTHING_PAID).plus(paid)
            above = self._holds[above].parent if above in self._holds else None

    def _call(self, usage: TokenUsageRecord) -> None:
        self._usage.append(usage)
        for meter in self._usd:
            usd, tokens = metered(usage, meter)
            if usd is not None:
                self._usd[meter] += usd
            self._tokens[meter] += tokens
        spend_slot(self._by_round, usage.spend_round, SpendRollup).bank(usage)
        # A replay carries no charge: it cost nothing.
        if (bill := usage.bill) is not None:
            self._calls += 1
            self._worked_s += usage.duration_s
            self._bill(bill, mirrored=usage.mirrored)

    def value(self) -> Money:
        if self._view is None:
            self._view = Money(
                self._spent,
                dict(self._open),
                dict(self._holds),
                dict(self._drawn),
                tuple(self._unpriced.values()),
                tuple(self._lapsed),
                tuple(self._bills),
                tuple(self._usage),
                {m: Budget.amount(self._usd[m], self._tokens[m]) for m in self._usd},
                {n: held.model_copy(deep=True) for n, held in self._by_round.items()},
                self._calls,
                self._worked_s,
            )
        return self._view


LedgerIndex.readers(_Money)


def money_on(subject: Path, until: int | None = None) -> Money:
    """*subject* is a ledger, or an account's charge book (`ChargeBook.path`)."""
    return LedgerIndex.of(subject).view(_Money, until)
