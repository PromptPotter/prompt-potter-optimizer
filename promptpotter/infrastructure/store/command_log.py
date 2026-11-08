"""The account's command log, ``.workspace/commands.jsonl`` under the account beside its grants and
charges: every door files on it, and no ledger move or delete reaches it (`domain/command_events.py`).

A read is a fold cached per file and process, as `LedgerIndex.of` is, refreshed by its tail; each
refresh publishes a fresh snapshot no later append mutates."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator, Mapping
from pathlib import Path
from types import MappingProxyType
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
    LoopPayload,
    OriginGateDecisionPayload,
    PauseCyclePayload,
    SetSampleLookaheadPayload,
    SkipSearchpointPayload,
    SubmitArmsPayload,
)
from promptpotter.domain.cycle_paths import Subject, WorkspaceDir
from promptpotter.infrastructure.store.io import ABSENT, FileMark
from promptpotter.infrastructure.store.layout import WORKSPACE_STATE_DIR
from promptpotter.infrastructure.store.read_model import (
    Signature,
    append_row,
    file_sig,
    read_rows_past,
)
from promptpotter.shared.clock import epoch_seconds
from promptpotter.shared.errors import IdempotencyKeyReusedError
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


class CommandBook(NamedTuple):
    """The log folded, as one snapshot: each command's filing and status, each subject's events in
    log order (a landed draft's ahead of its cycle's), and each subject's inbox and last event."""

    filed: Mapping[str, CommandRecord]
    status: Mapping[str, CommandStatus]
    events: Mapping[str, tuple[_Event, ...]]
    landed: Mapping[str, str]
    inboxes: Mapping[str, Controls]
    stamps: Mapping[str, int]

    def subject_events(self, subject: Subject | str) -> tuple[_Event, ...]:
        return self.events.get(self._spelled(subject), ())

    def controls(self, subject: Subject) -> Controls:
        return self.inboxes.get(self._spelled(subject), Controls())

    def stamp(self, subject: Subject) -> int | None:
        """The subject's last event, in ns: a filed control re-validates its cycle's reads."""
        return self.stamps.get(self._spelled(subject))

    def untaken(self, subject: Subject) -> list[str]:
        """The loop commands on *subject* accepted and never taken, less an ``auto`` look-ahead,
        which is a mode: what a run boundary voids."""
        kept = self.controls(subject).lookahead
        keep = kept.command_id if kept is not None and kept.auto else None
        return [
            record.command_id
            for record in self.subject_events(subject)
            if isinstance(record, CommandRecord)
            and record.kind in LOOP_KINDS
            and record.command_id != keep
            and self.status[record.command_id] == "accepted"
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
        for subject, events in self.events.items():
            for event in events:
                if isinstance(event, CommandRecord):
                    yield subject, event

    def _spelled(self, subject: Subject | str) -> str:
        spelled = subject if isinstance(subject, str) else subject_key(subject)
        return self.landed.get(spelled, spelled)


_EMPTY = CommandBook(*(MappingProxyType({}) for _ in range(6)))


def _folded(base: CommandBook, rows: list[CommandLogRow]) -> CommandBook:
    """A fresh book over *base* and *rows*: the published one is never written again."""
    filed, status = dict(base.filed), dict(base.status)
    events = {subject: list(held) for subject, held in base.events.items()}
    landed = dict(base.landed)
    touched: set[str] = set()
    for row in rows:
        if isinstance(row, CommandLanded):
            landed[row.subject] = row.onto
            events[row.onto] = [*events.pop(row.subject, []), *events.get(row.onto, [])]
            touched.add(row.onto)
            continue
        if isinstance(row, CommandRecord):
            if row.command_id in filed:
                continue
            filed[row.command_id], status[row.command_id] = row, "filed"
            subject = row.subject
        else:
            held = status.get(row.command_id)
            to = None if held is None else COMMAND_TRANSITIONS[held, row.status]
            if to is None:
                continue
            status[row.command_id] = to
            subject = filed[row.command_id].subject
        subject = landed.get(subject, subject)
        events.setdefault(subject, []).append(row)
        touched.add(subject)
    inboxes, stamps = dict(base.inboxes), dict(base.stamps)
    for subject in touched:
        inbox = _Inbox()
        for event in events[subject]:
            inbox.feed(event)
        inboxes[subject] = inbox.value()
        stamps[subject] = int((epoch_seconds(events[subject][-1].timestamp) or 0.0) * 1e9)
    return CommandBook(
        MappingProxyType(filed),
        MappingProxyType(status),
        MappingProxyType({subject: tuple(held) for subject, held in events.items()}),
        MappingProxyType(landed),
        MappingProxyType(inboxes),
        MappingProxyType(stamps),
    )


class _Fold:
    def __init__(self) -> None:
        self.mark: FileMark = ABSENT
        self.sig: Signature | None = None
        self.book = _EMPTY


_FOLDS: dict[Path, _Fold] = {}
_FOLDS_LOCK = threading.Lock()


class CommandLog:
    """One account's log; `build_stores` opens it once, as `Stores.commands`."""

    def __init__(self, account: WorkspaceDir) -> None:
        self.path = command_log_path(account)

    def book(self) -> CommandBook:
        with _FOLDS_LOCK:
            fold = _FOLDS.setdefault(self.path, _Fold())
            # Stat-only while nothing landed: the 2 s poll's 304 path reads this.
            if (sig := file_sig(self.path)) != fold.sig:
                read = read_rows_past(self.path, COMMAND_LOG_ADAPTER.validate_json, fold.mark)
                fold.book = _folded(_EMPTY if read.whole else fold.book, read.rows)
                fold.mark, fold.sig = read.mark, sig
            return fold.book

    def append(self, *rows: CommandLogRow) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        append_row(self.path, *(json.loads(COMMAND_LOG_ADAPTER.dump_json(row)) for row in rows))

    def ack(self, command_id: str, status: CommandAckStatus, detail: str = "") -> None:
        self.append(CommandAckRecord(command_id=command_id, status=status, detail=detail))

    def void(self, subject: Subject, detail: str) -> None:
        """A run boundary: every loop command *subject* accepted and the run never took."""
        untaken = self.book().untaken(subject)
        if untaken:
            self.append(
                *(CommandAckRecord(command_id=c, status="voided", detail=detail) for c in untaken)
            )
