from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import pickle
import re
import sys
import threading
from abc import ABC, abstractmethod
from bisect import bisect_right
from collections import OrderedDict, deque
from collections.abc import Callable, Hashable, Iterable, Sequence
from dataclasses import dataclass
from functools import cache, partial
from itertools import islice
from pathlib import Path
from typing import (
    Any,
    ClassVar,
    NamedTuple,
    Protocol,
    cast,
    runtime_checkable,
)

from pydantic import ValidationError

from promptpotter.config.paths import LEDGER_FOLDS_DIR, short_form, user_data_root
from promptpotter.domain.record_kinds import RECORDS
from promptpotter.domain.records import CycleRecord
from promptpotter.domain.records.lineage import CycleMintedRecord
from promptpotter.domain.records.rounds import stale_file_warning
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.io import (
    ABSENT,
    FileMark,
    append_jsonl,
    complete_lines,
    file_stat,
    open_bytes_robust,
    read_bytes_optional,
    read_tail,
    write_bytes,
)
from promptpotter.infrastructure.store.layout import (
    CycleLayout,
    campaign_cycle_dirs,
    cycle_ledgers,
    inner_cycle_ledgers,
)
from promptpotter.shared.clock import epoch_seconds
from promptpotter.shared.errors import (
    FileKind,
    FileUnreadableError,
    NotFoundError,
    validation_reason,
)


class LedgerSpan(NamedTuple):
    path: Path
    until: int | None = None
    # An L4 inner cycle's ledger, read as the cycle whose run spawned it files its sends.
    enclosed: bool = False


# Every `CycleRecord` arm: a fold of all of them reads every line, so a line no arm names refuses it.
_ARM_MODELS = {kind.encode(): arm for kind, arm in RECORDS.registered().items()}
EVERY_ARM: tuple[type[CycleRecord], ...] = tuple(_ARM_MODELS.values())
_EVERY_ARM = frozenset(EVERY_ARM)


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
    """THE validator of a file a read stands on: its size moves on every append, where its mtime can
    stand within one tick of a coarse clock (Windows') and serve a spurious 304."""
    try:
        st = file_stat(path)
    except FileNotFoundError:
        return None
    return st.st_ino, st.st_size, st.st_mtime_ns


RecordClasses = tuple[type[StrictModel], ...]


class LedgerFold[R: StrictModel, V](Protocol):
    """`feed` takes a line VALIDATED as one of `records`; `value` is a snapshot."""

    @property
    def records(self) -> RecordClasses: ...

    def feed(self, record: R) -> None: ...

    def value(self) -> V: ...


class LineFold[V](Protocol):
    """`feed` takes EVERY line as written, which is what reads an envelope alone; `value` is a
    snapshot; `kind` is what the file it folds IS, which a line it cannot take is refused as."""

    kind: ClassVar[FileKind]

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


def _refusal(
    ledger: Path, offset: int, reason: str, kind: FileKind = FileKind.LEDGER
) -> FileUnreadableError:
    return FileUnreadableError(kind, path=str(ledger), reason=f"line {offset}: {reason}")


def ledger_record[R](
    validate: Callable[[bytes], R],
    raw: bytes,
    *,
    ledger: Path,
    offset: int,
    kind: FileKind = FileKind.LEDGER,
) -> R | None:
    """Every line-file reader's one policy: a line it cannot take refuses the read, as its *kind*."""
    if not raw.strip():
        return None
    try:
        return validate(raw)
    except ValueError as exc:
        raise _refusal(ledger, offset, _reason(exc), kind) from exc


def feed_fold[T](
    fold: _Feeds[T], record: T, *, ledger: Path, offset: int, kind: FileKind = FileKind.LEDGER
) -> None:
    """A fold that cannot take a line refuses as a line no model takes does: a writer's checkout and a
    refold then fail at one offset, where folding past it would serve a value missing that line."""
    try:
        fold.feed(record)
    except Exception as exc:
        reason = f"{type(fold).__name__} cannot fold it: {_reason(exc)}"
        raise _refusal(ledger, offset, reason, kind) from exc


def _by_record_type(folds: Iterable[LedgerFold[Any, Any]]) -> dict[str, type[StrictModel]]:
    return {
        model.model_fields["record_type"].default: model for fold in folds for model in fold.records
    }


logger = logging.getLogger(__name__)

LEDGER_INDEX_MAX = 256
# A cold process refolds every ledger it serves; the screened folds of one this long are pickled
# by its `file_sig` and their code, so a restart folds no unchanged one.
SNAPSHOT_MIN_LINES = 500
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


def ledger_line(raw: bytes) -> CycleRecord:
    """One line read whole, as a survey of the file asks: a line no arm names raises."""
    if (kind := _RECORD_TYPE.search(raw)) is None:
        raise ValueError("no record_type")
    return RECORDS.parse(kind[1].decode(), raw)


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
        self.refused: dict[type[StrictModel] | _Make | None, FileUnreadableError] = {}

    def line(self, offset: int, raw: bytes) -> None:
        for placed in self._placed:
            placed.at_offset = offset
        kind = _RECORD_TYPE.search(raw)
        model = None if kind is None else self._models.get(kind[1])
        if kind is None or model is None:
            if raw.strip() and (kind is None or kind[1] not in _ARM_MODELS):
                reason = "no record_type" if kind is None else f"no arm is {kind[1].decode()!r}"
                self.refused.setdefault(None, _refusal(self._ledger, offset, reason))
            return
        # The bytes go to pydantic's own parser: a `json.loads` first costs as much again.
        try:
            record = ledger_record(
                model.model_validate_json, raw, ledger=self._ledger, offset=offset
            )
        except FileUnreadableError as refusal:
            self.refused.setdefault(model, refusal)
            return
        if record is None:
            return
        for make, fold in self.folds.items():
            if model in fold.records and make not in self.refused:
                try:
                    feed_fold(fold, record, ledger=self._ledger, offset=offset)
                except FileUnreadableError as refusal:
                    self.refused[make] = refusal

    def vouch(self, make: _Make) -> None:
        records = self.folds[make].records
        keys = (make, *records, *((None,) if set(records) >= _EVERY_ARM else ()))
        for key in keys:
            if (refusal := self.refused.get(key)) is not None:
                raise refusal.with_traceback(None)


def _fresh[F](make: Callable[[], F], start: F | None) -> F:
    made = make() if start is None else copy.deepcopy(start)
    if isinstance(made, Positioned):
        made.at_offset = -1
    return made


def _fold_lines(ledger: Path, feeder: _Feeder, until: int | None) -> None:
    try:
        with open_bytes_robust(ledger) as fh:
            for offset, raw in enumerate(islice(complete_lines(fh), until)):
                feeder.line(offset, raw)
    except FileNotFoundError:
        return


def folded[F: LedgerFold[Any, Any]](
    ledger: Path, fold: Callable[[], F], *, start: F | None, until: int | None
) -> F:
    """No index holds it, so nothing feeds it after: its caller alone owns what it folds on to."""
    made = _fresh(fold, start)
    feeder = _Feeder(ledger, {fold: made})
    _fold_lines(ledger, feeder, until)
    feeder.vouch(fold)
    return made


# By name, so a handler's module may import its record for typing alone.
_ARMS_BY_NAME = {arm.__name__: arm for arm in EVERY_ARM}
_HANDLER = "_handle_"


class Projection:
    """A `LedgerFold` that writes, fed after its index's folds: its `records` are the arms its
    `_handle_*(record: Arm)` methods annotate; it `derives` the file its fault leaves stale."""

    #: The ``Cut`` folded to, stamped by its feeder. ``-1`` = nothing folded yet.
    at_offset: int = -1
    records: ClassVar[RecordClasses] = ()
    derives: ClassVar[str]
    _routes: ClassVar[dict[type[CycleRecord], Callable[[Any, Any], None]]] = {}

    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        routes = dict(cls._routes)
        for name, handler in vars(cls).items():
            if not name.startswith(_HANDLER):
                continue
            named = handler.__annotations__["record"]
            if named not in _ARMS_BY_NAME:
                raise RuntimeError(f"{cls.__name__}.{name} handles {named}, no CycleRecord arm")
            routes[_ARMS_BY_NAME[named]] = handler
        cls._routes = routes
        cls.records = tuple(routes)
        LedgerIndex.readers(cls)

    def feed(self, record: CycleRecord) -> None:
        handler = self._routes.get(type(record))
        if handler is not None:
            handler(self, record)

    def drain(self) -> None:
        """Settle buffered state on teardown; a no-op where every event already flushes."""


class Projecting:
    """A drainer's hold on projections of one ledger: the last holder of a projection to close drains it."""

    def __init__(self, index: LedgerIndex) -> None:
        self._index = index
        self._kinds: list[type[Any]] | None = []

    def add[P: Projection](self, projection: P) -> P:
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
    _readers: ClassVar[list[type[Any] | LastOf]] = []
    # The declared folds that write nothing: every index folds them together.
    _screened: ClassVar[list[_Make]] = []
    _registry_lock: ClassVar[threading.Lock] = threading.Lock()

    @classmethod
    def readers(cls, *folds: type[Any] | LastOf) -> None:
        """The roster every reader of a ledger declares its folds on, a `Projection` by subclassing."""
        with cls._registry_lock:
            cls._readers.extend(fold for fold in folds if fold not in cls._readers)
            cls._screened.extend(
                fold for fold in folds if not hasattr(fold, "drain") and fold not in cls._screened
            )

    @classmethod
    def check_every_arm_read(cls) -> None:
        """Every `CycleRecord` arm is a `records` arm of a fold on the roster: a kind none reads is a
        state the system enters and reports nowhere. Run where the registries complete."""
        read = {arm for fold in cls._readers for arm in fold.records}
        if unread := sorted(k.decode() for k, arm in _ARM_MODELS.items() if arm not in read):
            raise RuntimeError(f"No fold on the roster reads these record kinds: {unread}.")

    def __init__(self, path: Path) -> None:
        self._starts: dict[_Make, LedgerFold[Any, Any] | None] = {}
        self._feeder = _Feeder(path, {})
        # A projection by its type, with how many hold it; the lines it waits on, in append order.
        self._held: dict[type[Any], tuple[Projection, int]] = {}
        # The held types whose fault is reported: one warning per projection while it is held.
        self._stale: set[type[Any]] = set()
        self._queue: deque[tuple[int, bytes, list[Projection], CycleRecord | None]] = deque()
        self._draining = threading.Lock()
        self._drainer: int | None = None
        self._until = -1
        super().__init__(path)

    @property
    def pinned(self) -> bool:
        return bool(self._held)

    def projecting(self) -> Projecting:
        return Projecting(self)

    def _join[P: Projection](self, projection: P) -> P:
        """A type held already is the held instance; a new one is fed from the line the index stands at."""
        with self._lock:
            held, holders = self._held.get(type(projection), (projection, 0))
            if not holders:
                self._refresh()
                self._stale.discard(type(projection))
            self._held[type(projection)] = (held, holders + 1)
            return cast("P", held)

    def _leave(self, kind: type[Any]) -> Projection | None:
        """The projection its last holder left, to drain: no line feeds it again."""
        with self._lock:
            held, holders = self._held[kind]
            if holders > 1:
                self._held[kind] = (held, holders - 1)
                return None
            del self._held[kind]
            return held

    def shielded(
        self, projection: Projection, call: Callable[[], None], at: int | None = None
    ) -> None:
        """Every call into a projection: a derived file that cannot land is one `derived_file_stale` warning,
        never a raise into the landed append, nothing held for a later one; io refuses it a gone cycle."""
        try:
            call()
        except (OSError, NotFoundError, FileUnreadableError) as fault:
            with self._lock:
                if type(projection) in self._stale:
                    return
                self._stale.add(type(projection))
                at = self._lines - 1 if at is None else at
            warning = stale_file_warning(projection.derives, at, _reason(fault))
            try:
                appended = append_jsonl(self._path, warning.model_dump(mode="json", fallback=str))
            except NotFoundError:
                return
            ((raw, landed),) = appended.each()
            # A ledger that takes the line, then refuses the warning, raises: that fault is the ledger's.
            self.land(raw, landed, warning)

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
        held = {make: _fresh(make, self._starts.get(make)) for make in self._feeder.folds}
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
        start: LedgerFold[Any, V] | None = None,
    ) -> V:
        with self._lock:
            self._open(fold, start)
            if until is None or until >= self._lines:
                self._feeder.vouch(fold)
                return cast("LedgerFold[Any, V]", self._feeder.folds[fold]).value()
            return cast("LedgerFold[Any, V]", self._prefix(fold, until)).value()

    def live[F: LedgerFold[Any, Any]](self, fold: Callable[[], F], *, start: F | None = None) -> F:
        """The slot itself, fed on past this call: a field held off it reads each line as it lands."""
        with self._lock:
            self._open(fold, start)
            self._feeder.vouch(fold)
            return cast("F", self._feeder.folds[fold])

    def prefix[F: LedgerFold[Any, Any]](
        self, fold: Callable[[], F], until: int, *, start: F | None = None
    ) -> F:
        """Held until this ledger is rewritten, so a fork's slot started off it reads a rewrite by identity;
        never fed again, so it may start another ledger's slot outside this lock."""
        with self._lock:
            self._refresh()
            held = self._cuts.get((fold, until))
            if held is None or held[0] is not start:
                cut = folded(self._path, fold, start=start, until=until)
                held = self._cuts[(fold, until)] = (start, cut)
            return cast("F", held[1])

    def _open(self, fold: _Make, start: LedgerFold[Any, Any] | None) -> None:
        self._refresh()
        held = self._feeder.folds
        if fold in self._screened:
            joining = {make: make() for make in self._screened if make not in held}
        elif fold not in held or self._starts[fold] is not start:
            self._starts[fold] = start
            joining = {fold: _fresh(fold, start)}
        else:
            return
        if not joining:
            return
        snapshot = self._snapshot() if fold in self._screened else None
        restored = None if snapshot is None else _restored(snapshot, joining)
        if restored is None:
            # A fold joins at the line the others stand at, so none of them refolds for it.
            joiner = _Feeder(self._path, joining)
            _fold_lines(self._path, joiner, self._lines)
            restored = joining, joiner.refused
            if snapshot is not None:
                _stash(snapshot, restored)
        joining, joined_refused = restored
        refused = self._feeder.refused | joined_refused
        self._feeder = _Feeder(self._path, held | joining)
        self._feeder.refused = refused
        for stale in [key for key in self._prefixes if key[0] in joining]:
            del self._prefixes[stale]

    def _snapshot(self) -> Path | None:
        if self._sig is None or self._lines < SNAPSHOT_MIN_LINES:
            return None
        key = hashlib.sha256(f"{self._path.resolve()}|{self._sig}|{_fold_code()}".encode())
        return user_data_root() / LEDGER_FOLDS_DIR / f"{key.hexdigest()[:32]}.pickle"

    def _prefix(self, fold: _Make, until: int) -> LedgerFold[Any, Any]:
        held = self._prefixes.pop((fold, until), None)
        if held is None:
            held = folded(self._path, fold, start=self._starts.get(fold), until=until)
        self._prefixes[(fold, until)] = held
        while len(self._prefixes) > PREFIXES_MAX:
            self._prefixes.popitem(last=False)
        return held

    def _feed(self, raw: bytes) -> None:
        self._feeder.line(self._lines, raw)
        if self._held:
            self._queue.append((self._lines, raw, [held for held, _ in self._held.values()], None))


type _Joined = tuple[dict[_Make, LedgerFold[Any, Any]], dict[Any, FileUnreadableError]]


@cache
def _fold_code() -> str:
    """The screened folds' and the records' source: a snapshot of other code is another key."""
    held = {*LedgerIndex._screened, *_ARM_MODELS.values()}
    files = sorted({sys.modules[made.__module__].__file__ or "" for made in held})
    return hashlib.sha256(b"".join(Path(f).read_bytes() for f in files)).hexdigest()


def _restored(snapshot: Path, joining: dict[_Make, LedgerFold[Any, Any]]) -> _Joined | None:
    """Only this process writes the data home's cache, so its pickle is trusted; one that does not
    load, or holds another roster, is refolded."""
    try:
        folds, refused = pickle.loads(snapshot.read_bytes())
    except FileNotFoundError:
        return None
    except Exception:  # noqa: BLE001 -- a cache that does not load is refolded
        logger.warning("fold snapshot %s does not load; refolding", snapshot, exc_info=True)
        return None
    return (folds, refused) if folds.keys() == joining.keys() else None


def _stash(snapshot: Path, joined: _Joined) -> None:
    try:
        write_bytes(snapshot, pickle.dumps(joined))
    except (OSError, NotFoundError, pickle.PicklingError, TypeError, AttributeError) as exc:
        logger.warning("fold snapshot %s not written: %s", snapshot, exc)


class _Last:
    def __init__(self, records: RecordClasses) -> None:
        self.records = records
        self._found: StrictModel | None = None

    def feed(self, record: StrictModel) -> None:
        self._found = record

    def value(self) -> StrictModel | None:
        return self._found


@dataclass(frozen=True)
class LastOf:
    """Makes the fold holding a ledger's last *record*: a value, so a fold snapshot pickles it by
    its record and the key it loads equals the one asked."""

    record: type[StrictModel]

    @property
    def records(self) -> RecordClasses:
        return (self.record,)

    def __call__(self) -> _Last:
        return _Last(self.records)


@dataclass(frozen=True)
class LineScope:
    """Which ledgers answer for a subject, the earliest first: a cycle directory's, or, where
    *of_campaign*, a campaign root's."""

    spans: Callable[[Path], list[LedgerSpan]]
    of_campaign: bool = False


@dataclass(frozen=True)
class Line[V, L]:
    """A fold read over the ledgers its *over* names, its composition stated once: *fold* folds
    each span, *then* lays a span's value onto what the spans before it held, from *start*."""

    fold: Callable[[], LedgerFold[Any, V]]
    start: Callable[[], L]
    then: Callable[[L, V, LedgerSpan], L]
    over: LineScope


def read_line[V, L](at: Path, line: Line[V, L], *, moment: Moment | None = None) -> L:
    """THE read of a line: *at* is its scope's subject, cut at *moment*."""
    spans = line.over.spans(at)
    if moment is not None:
        spans = moment.spans(spans)
    held = line.start()
    for span in spans:
        held = line.then(held, LedgerIndex.of(span.path).view(line.fold, span.until), span)
    return held


def last[R: StrictModel](record: type[R], over: LineScope) -> Line[R | None, R | None]:
    """The last *record* the spans hold, a later span's over an earlier one's."""
    return Line(
        cast("Callable[[], LedgerFold[Any, R | None]]", LastOf(record)),
        lambda: None,
        lambda held, own, _: held if own is None else own,
        over,
    )


def each_last[R: StrictModel](record: type[R], over: LineScope) -> Line[R | None, list[R | None]]:
    """Each span's own last *record*, ``None`` where it holds none, the earliest span's first."""
    return Line(
        cast("Callable[[], LedgerFold[Any, R | None]]", LastOf(record)),
        lambda: list[R | None](),
        lambda held, own, _: [*held, own],
        over,
    )


def line_concat[T](
    fold: Callable[[], LedgerFold[Any, list[T]]], over: LineScope
) -> Line[list[T], list[T]]:
    """Every span's values, oldest first."""
    return Line(fold, lambda: list[T](), lambda held, own, _: [*held, *own], over)


def line_union[T](
    fold: Callable[[], LedgerFold[Any, frozenset[T]]], over: LineScope
) -> Line[frozenset[T], frozenset[T]]:
    return Line(
        fold, lambda: frozenset[T](), lambda held, own, _: held | own if held else own, over
    )


# A cycle's own ledger: what a fork never inherits.
CYCLE = LineScope(lambda cycle: [LedgerSpan(CycleLayout(cycle).ledger)])


def _fork_prefix(cycle_dir: Path) -> list[LedgerSpan]:
    prefix: list[LedgerSpan] = []
    child_dir = cycle_dir
    # By dir, never id: two trees can hold the same cycle id. A mint names its parent off the
    # child's own path, so the spelling alone is compared, with no filesystem call per hop.
    seen = {os.path.normcase(short_form(child_dir))}
    while (minted := read_line(child_dir, _MINT)) is not None and (
        parent_dir := minted.parent_cycle_dir(child_dir)
    ) is not None:
        cut = minted.forked_at_offset
        assert cut is not None
        if (spelled := os.path.normcase(short_form(parent_dir))) in seen:
            raise ValueError(
                f"{child_dir}: parent {parent_dir} is already on this fork chain — "
                "the mints describe a cycle, which no walk can resolve."
            )
        seen.add(spelled)
        child_dir = parent_dir
        prefix.append(LedgerSpan(CycleLayout(child_dir).ledger, cut))
    return prefix[::-1]


def fork_chain(cycle_dir: Path) -> list[LedgerSpan]:
    """A cycle's fork prefix, its root's first, each cut where its child forked, then its own."""
    return [*_fork_prefix(cycle_dir), LedgerSpan(CycleLayout(cycle_dir).ledger)]


def _widest_walks(campaign_root: Path) -> list[LedgerSpan]:
    widest: dict[Path, int | None] = {}
    for cycle_dir in campaign_cycle_dirs(campaign_root):
        for path, until, _ in fork_chain(cycle_dir):
            if path.is_file():
                held = widest.get(path, 0)
                widest[path] = None if until is None or held is None else max(held, until)
    return [LedgerSpan(path, until) for path, until in widest.items()]


def enclosed_spans(span: LedgerSpan) -> list[LedgerSpan]:
    """The L4 inner cycles' ledgers of *span*'s cycle, each cut at the instant *span* is."""
    inner = inner_cycle_ledgers(span.path)
    if span.until is None or not inner:
        return [LedgerSpan(ledger, enclosed=True) for ledger in inner]
    if span.until == 0:
        return [LedgerSpan(ledger, 0, enclosed=True) for ledger in inner]
    cut = Moment(span.path, span.until - 1)
    return [cut.span(ledger)._replace(enclosed=True) for ledger in inner]


def enclosing(scope: LineScope) -> LineScope:
    """*scope*'s ledgers, each followed by its L4 inner cycles': an outer's spend is a fold of
    what its inner runs filed, once each, on their own ledgers."""

    def spans(at: Path) -> list[LedgerSpan]:
        return [one for span in scope.spans(at) for one in (span, *enclosed_spans(span))]

    return LineScope(spans, of_campaign=scope.of_campaign)


# A cycle's chain: a fork reads its parent's ledger to its cut.
CHAIN = LineScope(fork_chain)
# Every cycle ledger of a campaign, each read whole, so each cycle's own appends count once.
CAMPAIGN = LineScope(lambda root: [LedgerSpan(p) for p in cycle_ledgers(root)], of_campaign=True)
# Each cycle's whole chain, widest per ledger: a cycle lifted from another tree remembers its prefix.
CAMPAIGN_WALKS = LineScope(_widest_walks, of_campaign=True)
_MINT = last(CycleMintedRecord, CYCLE)


class LineIndex(_LedgerTail):
    """One per (ledger, roster): a line fold is fed every line, so it shares no screened index."""

    _registry: ClassVar[OrderedDict[tuple[Path, tuple[type[LineFold[Any]], ...]], LineIndex]] = (
        OrderedDict()
    )
    _registry_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self, path: Path, roster: tuple[type[LineFold[Any]], ...]) -> None:
        # One file is one kind: its folds say which, and a roster naming two is a wiring fault.
        (self._kind,) = {fold.kind for fold in roster}
        self._roster = roster
        super().__init__(path)

    @classmethod
    def of(cls, path: Path, roster: tuple[type[LineFold[Any]], ...]) -> LineIndex:
        with cls._registry_lock:
            return _registered(cls._registry, (path, roster), lambda: cls(path, roster))

    def _reset(self) -> None:
        super()._reset()
        self._folds: dict[type[Any], LineFold[Any]] = {
            type(fold): fold for fold in (make() for make in self._roster)
        }
        self._placed = [fold for fold in self._folds.values() if isinstance(fold, Positioned)]
        self._refused: FileUnreadableError | None = None

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
            line = ledger_record(
                line_dict, raw, ledger=self._path, offset=self._lines, kind=self._kind
            )
        except FileUnreadableError as refusal:
            self._refused = self._refused or refusal
            return
        if line is None or self._refused is not None:
            return
        try:
            for fold in self._folds.values():
                feed_fold(fold, line, ledger=self._path, offset=self._lines, kind=self._kind)
        except FileUnreadableError as refusal:
            self._refused = refusal


class _Clock:
    kind: ClassVar[FileKind] = FileKind.LEDGER

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
            out.append(held._replace(until=then if held.until is None else min(held.until, then)))
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
