from __future__ import annotations

import copy
import json
import logging
import re
import threading
from abc import ABC, abstractmethod
from bisect import bisect_right
from collections import OrderedDict
from collections.abc import Callable, Hashable, Iterable, Iterator, Sequence
from itertools import islice
from pathlib import Path
from typing import Any, ClassVar, NamedTuple, Protocol, cast, get_args, runtime_checkable

from pydantic import ValidationError

from promptpotter.domain.optimizer_state import UnregisteredStatePartError
from promptpotter.domain.run_records import SpendHoldRecord, TokenUsageRecord
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.io import append_line, open_text_robust
from promptpotter.infrastructure.store.layout import CycleLayout, campaign_cycles_dir
from promptpotter.shared.clock import epoch_seconds
from promptpotter.shared.errors import LedgerUnreadableError

logger = logging.getLogger(__name__)


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


def _refusal(ledger: Path, offset: int, exc: ValueError) -> LedgerUnreadableError:
    if isinstance(exc, ValidationError):
        err = exc.errors()[0]
        reason = f"{'.'.join(map(str, err['loc'])) or 'the record'}: {err['msg']}"
    else:
        reason = str(exc)
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
        raise _refusal(ledger, offset, exc) from exc


def feed_fold[T](fold: _Feeds[T], record: T, *, ledger: Path, offset: int) -> None:
    """A fold that raises on a line folds on past it, so a writer's checkout and a refold stay one value."""
    try:
        fold.feed(record)
    except Exception:
        logger.exception("fold %s skipped offset %d of %s", type(fold).__name__, offset, ledger)


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
        self._bytes = 0
        self._lines = 0
        # The last line fed, which an append leaves where it was: a refusal rides the fold, so
        # whatever reads a re-created file as this one's tail serves this one's refusal.
        self._last = b""

    @abstractmethod
    def _feed(self, raw: bytes) -> None: ...

    def _holds_last(self) -> bool:
        """An inode number outlives its file, so a delete and re-mint passes every stat test."""
        try:
            with open(self._path, "rb") as fh:
                fh.seek(self._bytes - len(self._last))
                return fh.read(len(self._last)) == self._last
        except FileNotFoundError:
            return False

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
            and self._holds_last()
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
                self._last = raw
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
            self._last = raw + b"\n"
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
        # By kind, so the folds that never take a refused line (run state, spend) still read whole:
        # a reshape of one kind leaves the spend bank readable, so a reset can bank before it wipes.
        self.refused: dict[
            type[StrictModel], LedgerUnreadableError | UnregisteredStatePartError
        ] = {}

    def line(self, offset: int, raw: bytes) -> None:
        for placed in self._placed:
            placed.at_offset = offset
        kind = _RECORD_TYPE.search(raw)
        model = None if kind is None else self._models.get(kind[1])
        if model is None:
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
        for fold in self.folds.values():
            if model in fold.records:
                feed_fold(fold, record, ledger=self._ledger, offset=offset)

    def vouch(self, fold: LedgerFold[Any, Any]) -> None:
        for model in fold.records:
            if (refusal := self.refused.get(model)) is not None:
                raise refusal.with_traceback(None)


def _fresh[F](make: Callable[[], F], seed: F | None) -> F:
    made = make() if seed is None else copy.deepcopy(seed)
    if isinstance(made, Positioned):
        made.at_offset = -1
    return made


def _fold_lines(ledger: Path, feeder: _Feeder, until: int | None) -> None:
    try:
        for offset, raw in enumerate(islice(_complete_lines(ledger, 0), until)):
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
    feeder.vouch(made)
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
                held = self._feeder.folds[fold]
                self._feeder.vouch(held)
                return cast("LedgerFold[Any, V]", held).value()
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
        if line is None:
            return
        for fold in self._folds.values():
            feed_fold(fold, line, ledger=self._path, offset=self._lines)


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
        # The holds closed unpriced, whose `cost_usd` stays held.
        self.unpriced: dict[str, SpendHoldRecord] = {}
        self._holds: dict[str, SpendHoldRecord] = {}

    def track(self, record: HoldTrailRecord) -> None:
        if isinstance(record, SpendHoldRecord):
            self.open[record.hold_id] = record
            self._holds[record.hold_id] = record
        elif (hold_id := record.hold_id) is not None:
            self.open.pop(hold_id, None)
            # A bill names a hold of another trail where a nested run's copy is carried onto its root.
            if hold_id in self._holds and record.bill_or_rate_usd is None:
                self.unpriced[hold_id] = self._holds[hold_id]


def held_tokens(hold: SpendHoldRecord) -> int:
    """An uncapped reply holds no tokens: such a send is admitted only where no token ceiling stands."""
    return 0 if hold.output_tokens is None else hold.input_tokens + hold.output_tokens
