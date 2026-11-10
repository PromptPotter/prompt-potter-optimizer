"""The account's command log, ``.workspace/commands.jsonl`` under the account beside its grants and
charges: every door files on it, and no ledger move or delete reaches it (`domain/command_events.py`).

A read is a fold registered per file as `LedgerIndex.of`'s are, fed from the last byte it read: its
state only grows, every value versioned by the line that set it, so a book is that state read at
one line and no later line moves what it reads."""

from __future__ import annotations

import threading
from bisect import bisect_right
from collections import OrderedDict
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

from promptpotter.domain.command_events import (
    COMMAND_LOG_ADAPTER,
    COMMAND_TRANSITIONS,
    CommandAckRecord,
    CommandAckStatus,
    CommandLanded,
    CommandLogRow,
    CommandRecord,
    CommandStatus,
    subject_key,
)
from promptpotter.domain.command_kinds import (
    PAYLOAD_MODEL_FOR_KIND,
    CommandKind,
    LoopPayload,
    OriginGateDecisionPayload,
    PauseCyclePayload,
    SetSampleLookaheadPayload,
    SkipSearchpointPayload,
    SubmitArmsPayload,
)
from promptpotter.domain.cycle_paths import Subject, WorkspaceDir
from promptpotter.infrastructure.store.io import append_line, appending
from promptpotter.infrastructure.store.layout import WORKSPACE_STATE_DIR
from promptpotter.infrastructure.store.read_model import _LedgerTail, _registered, parse_rows
from promptpotter.shared.clock import epoch_seconds
from promptpotter.shared.errors import ConflictError, IdempotencyKeyReusedError
from promptpotter.shared.identity import Principal

COMMAND_LOG_FILE = "commands.jsonl"


def command_log_path(account: WorkspaceDir) -> Path:
    return account / WORKSPACE_STATE_DIR / COMMAND_LOG_FILE


class PauseAsk(NamedTuple):
    command_id: str
    issued_by: Principal
    at: str


class LookaheadAsk(NamedTuple):
    command_id: str
    cells: int
    auto: bool
    taken: bool = False


class Controls(NamedTuple):
    """A running loop's inbox: the loop commands on its cycle accepted and not yet taken."""

    pause: PauseAsk | None = None
    skips: tuple[str, ...] = ()
    lookahead: LookaheadAsk | None = None
    gate_decisions: tuple[tuple[str, str], ...] = ()
    submissions: tuple[tuple[str, SubmitArmsPayload], ...] = ()


LOOP_KINDS = frozenset(
    kind for kind, model in PAYLOAD_MODEL_FOR_KIND.items() if issubclass(model, LoopPayload)
)

_Event = CommandRecord | CommandAckRecord


class _Inbox:
    """One subject's loop commands in log order; a ``voided`` or ``cancelled`` one leaves it."""

    def __init__(self) -> None:
        self._held = Controls()
        self._asked: dict[str, CommandRecord] = {}
        self._kinds: dict[str, type[LoopPayload]] = {}

    def feed(self, event: _Event) -> None:
        match event:
            case CommandRecord():
                if event.kind in LOOP_KINDS:
                    self._asked[event.command_id] = event
            case CommandAckRecord(command_id=command_id, status="accepted"):
                if asked := self._asked.pop(command_id, None):
                    self._arm(asked)
            case CommandAckRecord(command_id=command_id, status="rejected"):
                self._asked.pop(command_id, None)
            case CommandAckRecord(command_id=command_id, status="applied"):
                if asked := self._asked.pop(command_id, None):
                    # Applied with no accept before it: a look-ahead DISARM, nothing to take.
                    self._arm(asked)
                elif kind := self._kinds.pop(command_id, None):
                    self._take(command_id, kind, dropped=False)
            case CommandAckRecord(command_id=command_id):
                self._asked.pop(command_id, None)
                if kind := self._kinds.pop(command_id, None):
                    self._take(command_id, kind, dropped=True)

    def _arm(self, asked: CommandRecord) -> None:
        command_id, held = asked.command_id, self._held
        kind = PAYLOAD_MODEL_FOR_KIND[asked.kind]
        assert issubclass(kind, LoopPayload), "only a kind in `LOOP_KINDS` is asked"
        match kind.model_validate(asked.payload):
            case PauseCyclePayload():
                if held.pause is not None:
                    return
                held = held._replace(pause=PauseAsk(command_id, asked.issued_by, asked.timestamp))
            case SkipSearchpointPayload():
                held = held._replace(skips=(*held.skips, command_id))
            case OriginGateDecisionPayload(decision=decision):
                held = held._replace(gate_decisions=(*held.gate_decisions, (command_id, decision)))
            case SubmitArmsPayload() as submitted:
                held = held._replace(submissions=(*held.submissions, (command_id, submitted)))
            case SetSampleLookaheadPayload(cells=cells, auto=auto):
                if held.lookahead is not None:
                    self._kinds.pop(held.lookahead.command_id, None)
                armed = auto or cells > 1
                held = held._replace(
                    lookahead=LookaheadAsk(command_id, cells, auto) if armed else None
                )
                if not armed:
                    self._held = held
                    return
        self._kinds[command_id] = kind
        self._held = held

    def _take(self, command_id: str, kind: type[LoopPayload], *, dropped: bool) -> None:
        held = self._held
        if kind is PauseCyclePayload:
            held = held._replace(pause=None)
        elif kind is SkipSearchpointPayload:
            held = held._replace(skips=tuple(s for s in held.skips if s != command_id))
        elif kind is OriginGateDecisionPayload:
            held = held._replace(
                gate_decisions=tuple(d for d in held.gate_decisions if d[0] != command_id)
            )
        elif kind is SubmitArmsPayload:
            held = held._replace(
                submissions=tuple(s for s in held.submissions if s[0] != command_id)
            )
        else:
            ask = held.lookahead
            if ask is not None and ask.auto and not dropped:
                held = held._replace(lookahead=ask._replace(taken=True))
            else:
                held = held._replace(lookahead=None)
        self._held = held

    def value(self) -> Controls:
        return self._held


class _Versions[V]:
    """A value's every version, by the line that set it. Appended only, the line before the value:
    a read at a line before a version's never reaches it."""

    __slots__ = ("_at", "_held")

    def __init__(self) -> None:
        self._at: list[int] = []
        self._held: list[V] = []

    def set(self, line: int, value: V) -> None:
        self._at.append(line)
        self._held.append(value)

    def at(self, line: int) -> V | None:
        i = bisect_right(self._at, line)
        return self._held[i - 1] if i else None

    def upto(self, line: int) -> list[V]:
        return self._held[: bisect_right(self._at, line)]

    def __bool__(self) -> bool:
        return bool(self._at)


class _Moved(NamedTuple):
    status: CommandStatus
    # The status event that moved it there: its effect, or the refusal it kept.
    end: CommandAckRecord | None


class _State:
    """The log folded so far. Only grows: a book reads it at its line."""

    def __init__(self) -> None:
        self.filed: dict[str, tuple[int, CommandRecord]] = {}
        self.moved: dict[str, _Versions[_Moved]] = {}
        # The `cancel` standing on each command it names, while that command is filed.
        self.stopping: dict[str, _Versions[PauseAsk | None]] = {}
        self.landed: dict[str, tuple[int, str]] = {}
        self.drafts_onto: dict[str, list[str]] = {}
        self.events: dict[str, _Versions[_Event]] = {}
        self.inboxes: dict[str, tuple[_Inbox, _Versions[Controls]]] = {}

    def feed(self, line: int, row: CommandLogRow) -> None:
        if isinstance(row, CommandLanded):
            self._land(line, row.subject, row.onto)
        elif isinstance(row, CommandRecord):
            if row.command_id in self.filed:
                return
            self.filed[row.command_id] = (line, row)
            moved = self.moved[row.command_id] = _Versions()
            moved.set(line, _Moved("filed", None))
            self._add(line, row.subject, row)
        else:
            versions = self.moved.get(row.command_id)
            held = None if versions is None else versions.at(line)
            to = None if held is None else COMMAND_TRANSITIONS[held.status, row.status]
            if versions is None or to is None:
                return
            versions.set(line, _Moved(to, row))
            # Any move ends what a cancel stopped: no command is filed again.
            if (stop := self.stopping.get(row.command_id)) is not None:
                stop.set(line, None)
            asked = self.filed[row.command_id][1]
            if to == "applied" and asked.kind == CommandKind.CANCEL:
                target = str(asked.payload["command_id"])
                standing = self.moved.get(target)
                if standing is not None and (now := standing.at(line)) and now.status == "filed":
                    ask = PauseAsk(row.command_id, asked.issued_by, asked.timestamp)
                    self.stopping.setdefault(target, _Versions()).set(line, ask)
            self._add(line, asked.subject, row)

    def _add(self, line: int, subject: str, event: _Event) -> None:
        onto = self.landed.get(subject)
        spelled = subject if onto is None else onto[1]
        self.events.setdefault(spelled, _Versions()).set(line, event)
        inbox, held = self.inboxes.setdefault(spelled, (_Inbox(), _Versions()))
        was = inbox.value()
        inbox.feed(event)
        if (now := inbox.value()) is not was or not held:
            held.set(line, now)

    def _land(self, line: int, draft: str, onto: str) -> None:
        """What was filed on *draft* reads as *onto*'s from here, ahead of its own."""
        self.landed[draft] = (line, onto)
        self.drafts_onto.setdefault(onto, []).append(draft)
        inbox = _Inbox()
        for spelled in (draft, onto):
            for event in self.events.get(spelled, _Versions()).upto(line):
                inbox.feed(event)
        held = self.inboxes[onto][1] if onto in self.inboxes else _Versions[Controls]()
        self.inboxes[onto] = (inbox, held)
        held.set(line, inbox.value())


class CommandBook:
    """The log as it stood at one line: each command's filing and where it stands, each subject's
    events in log order (a landed draft's ahead of its cycle's) and its inbox, and the ``cancel``
    asked of each command still acting, which its end drops."""

    __slots__ = ("_line", "_state")

    def __init__(self, state: _State, line: int) -> None:
        self._state, self._line = state, line

    def filing(self, command_id: str) -> CommandRecord | None:
        held = self._state.filed.get(command_id)
        return held[1] if held is not None and held[0] <= self._line else None

    def _moved(self, command_id: str) -> _Moved | None:
        moved = self._state.moved.get(command_id)
        return None if moved is None else moved.at(self._line)

    def status(self, command_id: str) -> CommandStatus | None:
        return None if (moved := self._moved(command_id)) is None else moved.status

    def end(self, command_id: str) -> CommandAckRecord | None:
        """The status event that last moved it: its effect, or the refusal it kept."""
        return None if (moved := self._moved(command_id)) is None else moved.end

    def stop(self, command_id: str) -> PauseAsk | None:
        """The ``cancel`` standing on *command_id* while it acts: what its pass reads as its pause."""
        stopping = self._state.stopping.get(command_id)
        return None if stopping is None else stopping.at(self._line)

    def subject_events(self, subject: Subject | str) -> tuple[_Event, ...]:
        spelled, state = self._spelled(subject), self._state
        events: list[_Event] = []
        for draft in list(state.drafts_onto.get(spelled, ())):
            if state.landed[draft][0] <= self._line:
                events += state.events.get(draft, _Versions()).upto(self._line)
        held = state.events.get(spelled)
        return tuple(events if held is None else [*events, *held.upto(self._line)])

    def controls(self, subject: Subject) -> Controls:
        held = self._state.inboxes.get(self._spelled(subject))
        return (None if held is None else held[1].at(self._line)) or Controls()

    def stamp(self, subject: Subject) -> int | None:
        """The subject's last event, in ns: a filed control re-validates its cycle's reads."""
        spelled, state = self._spelled(subject), self._state
        last = None if (held := state.events.get(spelled)) is None else held.at(self._line)
        for draft in reversed(list(state.drafts_onto.get(spelled, ()))):
            if last is not None:
                break
            if state.landed[draft][0] <= self._line:
                last = state.events.get(draft, _Versions()).at(self._line)
        return None if last is None else int((epoch_seconds(last.timestamp) or 0.0) * 1e9)

    def untaken(self, subject: Subject, *, before: float | None = None) -> list[str]:
        """The loop commands on *subject* accepted and never taken, less an ``auto`` look-ahead,
        which is a mode, filed before epoch *before* where given: what a run boundary voids."""
        kept = self.controls(subject).lookahead
        keep = kept.command_id if kept is not None and kept.auto else None
        return [
            record.command_id
            for record in self.subject_events(subject)
            if isinstance(record, CommandRecord)
            and record.kind in LOOP_KINDS
            and record.command_id != keep
            and self.status(record.command_id) == "accepted"
            and (before is None or (epoch_seconds(record.timestamp) or 0.0) < before)
        ]

    def keyed(
        self, subject: str, kind: str, act_digest: str, idempotency_key: str
    ) -> tuple[CommandRecord, CommandAckRecord] | None:
        """The act a retry under *idempotency_key* names on *subject*, matched on (kind, act, key);
        a key *subject* holds for another act is refused, never replayed. A command never accepted
        satisfies no key; one accepted answers its retry after a void too."""
        found: tuple[CommandRecord, CommandAckRecord] | None = None
        asked: dict[str, CommandRecord] = {}
        for event in self.subject_events(subject):
            if isinstance(event, CommandRecord):
                if event.idempotency_key != idempotency_key or event.subject != subject:
                    continue
                if event.kind != kind or event.act_digest != act_digest:
                    raise IdempotencyKeyReusedError(
                        f"Key {idempotency_key!r} already names a {event.kind} command with "
                        f"another payload here; send this {kind} under a key of its own.",
                        details={"command_id": event.command_id},
                    )
                asked[event.command_id] = event
            elif event.status in ("accepted", "applied") and event.command_id in asked:
                found = asked[event.command_id], event
        return found

    def lines(self) -> Iterator[tuple[str, CommandRecord]]:
        """Every filing, under the subject it reads as."""
        for line, record in list(self._state.filed.values()):
            if line <= self._line:
                yield self._spelled(record.subject), record

    def _spelled(self, subject: Subject | str) -> str:
        spelled = subject if isinstance(subject, str) else subject_key(subject)
        onto = self._state.landed.get(spelled)
        return spelled if onto is None or onto[0] > self._line else onto[1]


class _Fold(_LedgerTail):
    def _reset(self) -> None:
        super()._reset()
        # A rewritten log is read whole into a fresh state: a book read off the old one keeps it.
        self._state = _State()

    def _feed(self, raw: bytes) -> None:
        for row in parse_rows(self._path, [raw], COMMAND_LOG_ADAPTER.validate_json):
            self._state.feed(self._lines + 1, row)

    def book(self) -> CommandBook:
        with self._lock:
            try:
                self._refresh()
            except BaseException:
                # A refused line leaves the lines before it fed: the next read starts whole.
                self._reset()
                raise
            return CommandBook(self._state, self._lines)


_FOLDS: OrderedDict[Path, _Fold] = OrderedDict()
# The registry's alone: each fold refreshes under its own lock.
_FOLDS_LOCK = threading.Lock()


def _encoded(*rows: CommandLogRow) -> bytes:
    return b"\n".join(COMMAND_LOG_ADAPTER.dump_json(row) for row in rows)


class CommandLog:
    """One account's log; `build_stores` opens it once, as `Stores.commands`."""

    def __init__(self, account: WorkspaceDir) -> None:
        self.path = command_log_path(account)

    def book(self) -> CommandBook:
        with _FOLDS_LOCK:
            fold = _registered(_FOLDS, self.path, lambda: _Fold(self.path))
        return fold.book()

    def file(self, record: CommandRecord) -> None:
        """A filing's id is the log's own: refused inside the write that would land a second one,
        so two calls under one id never both act."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with appending(self.path) as put:
            if self.book().filing(record.command_id) is not None:
                raise ConflictError(
                    f"command {record.command_id} is already filed; send a new call under an id "
                    "of its own",
                    code="command_id_reused",
                )
            put(_encoded(record))

    def append(self, *rows: CommandLogRow) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        append_line(self.path, _encoded(*rows))

    def ack(self, command_id: str, status: CommandAckStatus, detail: str = "") -> None:
        self.append(CommandAckRecord(command_id=command_id, status=status, detail=detail))

    def void(self, subject: Subject, detail: str, *, before: float | None = None) -> None:
        """A run boundary: every loop command *subject* accepted, filed before epoch *before* where
        given, and the run never took."""
        untaken = self.book().untaken(subject, before=before)
        if untaken:
            self.append(
                *(CommandAckRecord(command_id=c, status="voided", detail=detail) for c in untaken)
            )
