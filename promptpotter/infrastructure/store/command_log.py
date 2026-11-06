"""The account's command log: what was ASKED, one append-only stream per account
(`.workspace/commands.jsonl` under the account's own workspace, beside its grants and charges).
The cycle ledger keeps what HAPPENED; no command event rides it, so moving or deleting a ledger, a
campaign or a sandbox moves no command's status.

Every event is checked against the fold it lands on (`run_records.py::COMMAND_TRANSITIONS`) under
the file's own write lock (`io.py::append_line`), so no reader meets a move the table refuses. Each
read is a snapshot no later write mutates."""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import NamedTuple

from promptpotter.config.paths import PROJECTS_DIR
from promptpotter.domain.command_kinds import (
    PAYLOAD_MODEL_FOR_KIND,
    LoopPayload,
    OriginGateDecisionPayload,
    PauseCyclePayload,
    SetSampleLookaheadPayload,
    SkipSearchpointPayload,
    SubmitArmsPayload,
)
from promptpotter.domain.cycle_paths import WorkspaceDir
from promptpotter.domain.run_records import (
    COMMAND_EVENT_ADAPTER,
    CommandEvent,
    CommandFiled,
    CommandLogRow,
    CommandState,
    CommandSubject,
    SubjectLanded,
)
from promptpotter.infrastructure.store.io import ABSENT, FileMark
from promptpotter.infrastructure.store.layout import (
    INNER_SANDBOXES_DIR,
    WORKSPACE_STATE_DIR,
    inner_sandbox_key,
)
from promptpotter.infrastructure.store.read_model import append_row, read_rows_past
from promptpotter.shared.errors import ConflictError, IdempotencyKeyReusedError
from promptpotter.shared.identity import Principal

COMMAND_LOG_FILE = "commands.jsonl"


def command_log_path(account: WorkspaceDir | Path) -> Path:
    """Never creates it: a read must not mint a `.workspace/` in a tenant that filed nothing."""
    return Path(account) / WORKSPACE_STATE_DIR / COMMAND_LOG_FILE


class CommandIdReusedError(ConflictError):
    """A command id is filed once: a second filing under it is refused, never merged."""

    code = "command_id_reused"


# Where on disk a cycle's commands are read from: its campaign and cycle, in the sandbox that holds
# it (`layout.py::inner_sandbox_key`), so a cycle read by its directory alone finds its own.
Place = str


def _place(sandbox: str, campaign_id: str, cycle_id: str = "") -> Place:
    tail = f"{campaign_id}/{cycle_id}" if cycle_id else campaign_id
    return f"{sandbox}:{tail}" if sandbox else tail


class CommandBook(NamedTuple):
    """The log folded, as one snapshot: each command's state, each place's events in log order,
    and each draft subject a mint adopted, by the cycle it landed on."""

    commands: Mapping[str, CommandState]
    # (command_id, the status that event moved it to), in the order the log holds them.
    by_subject: Mapping[Place, tuple[tuple[str, CommandState], ...]]
    landed: Mapping[str, str]


_EMPTY = CommandBook(MappingProxyType({}), MappingProxyType({}), MappingProxyType({}))


def _row(raw: bytes) -> CommandLogRow:
    return COMMAND_EVENT_ADAPTER.validate_json(raw)


def _moved(held: CommandState | None, event: CommandEvent) -> CommandState:
    """What *event* moves its command to; a move the table refuses raises."""
    if isinstance(event, CommandFiled):
        if held is not None:
            raise CommandIdReusedError(
                f"command id {event.command_id} is already filed",
                details={"command_id": event.command_id},
            )
        CommandState.moved(None, "filed")
        return CommandState(filed=event, status="filed", at=event.timestamp)
    if held is None:
        raise ConflictError(f"no command {event.command_id} is filed", code="command_unfiled")
    try:
        return held.after(event)
    except ValueError as exc:
        raise ConflictError(
            f"command {event.command_id} is {held.status}: {exc}",
            code="command_transition_refused",
            details={"command_id": event.command_id, "status": held.status},
        ) from exc


class _Fold:
    def __init__(self, tenant: str) -> None:
        self.tenant = tenant
        self.mark: FileMark = ABSENT
        self.sig: tuple[int, int, int] | None = None
        self.book = _EMPTY

    def place_of(self, spelled: str) -> Place:
        """The place a filed subject's commands are read at."""
        subject = CommandSubject.subject_of(spelled)
        if subject is None:
            return spelled
        sandbox = inner_sandbox_key(self.tenant, subject.inside[-1]) if subject.inside else ""
        return _place(sandbox, subject.campaign_id, subject.cycle_id)

    def _folded(self, rows: list[CommandLogRow], whole: bool) -> CommandBook:
        """A fresh book over *rows*: the published one is never written again."""
        base = _EMPTY if whole else self.book
        commands = dict(base.commands)
        by_subject = {place: list(events) for place, events in base.by_subject.items()}
        landed = dict(base.landed)
        for row in rows:
            if isinstance(row, SubjectLanded):
                # Its events lead the cycle's, as its draft's ledger leads the cycle's ledger.
                landed[row.subject] = row.onto
                onto = self.place_of(row.onto)
                by_subject[onto] = [*by_subject.pop(row.subject, []), *by_subject.get(onto, [])]
                continue
            state = _moved(commands.get(row.command_id), row)
            commands[row.command_id] = state
            subject = state.filed.subject
            place = self.place_of(landed.get(subject, subject))
            by_subject.setdefault(place, []).append((row.command_id, state))
        return CommandBook(
            MappingProxyType(commands),
            MappingProxyType({place: tuple(events) for place, events in by_subject.items()}),
            MappingProxyType(landed),
        )

    def refresh(self, path: Path) -> CommandBook:
        try:
            st = path.stat()
        except FileNotFoundError:
            self.mark, self.sig, self.book = ABSENT, None, _EMPTY
            return self.book
        sig = (st.st_ino, st.st_size, st.st_mtime_ns)
        if sig != self.sig:
            read = read_rows_past(path, _row, self.mark)
            self.book = self._folded(list(read.rows), read.whole)
            self.mark, self.sig = read.mark, sig
        return self.book


_FOLDS: dict[Path, _Fold] = {}
_FOLDS_LOCK = threading.Lock()


def _account_of(workspace: Path) -> tuple[Path, str]:
    """A workspace's account and the sandbox it is (``""`` for the account's own): a sandbox's
    workspace is ``.inner/<key>/<tenant>`` beside the account's ``projects/<tenant>``."""
    sandboxes = workspace.parent.parent
    if sandboxes.name != INNER_SANDBOXES_DIR:
        return workspace, ""
    return sandboxes.parent / PROJECTS_DIR / workspace.name, workspace.parent.name


class CommandLog:
    """One account's log. Reads are incremental folds, cached per file, each a snapshot."""

    def __init__(self, account: WorkspaceDir | Path) -> None:
        self.path = command_log_path(account)
        self._tenant = Path(account).name

    @classmethod
    def of_cycle(cls, cycle_dir: Path) -> tuple[CommandLog, Place]:
        """The account's log a cycle's commands are filed on, and the place they are read at:
        ``<workspace>/campaigns/<campaign>/cycles/<cycle>``, the workspace an account's or a
        sandbox's."""
        cycle_dir = cycle_dir.absolute()
        account, sandbox = _account_of(cycle_dir.parents[3])
        return cls(account), _place(sandbox, cycle_dir.parent.parent.name, cycle_dir.name)

    def _fold(self) -> _Fold:
        return _FOLDS.setdefault(self.path, _Fold(self._tenant))

    def book(self) -> CommandBook:
        with _FOLDS_LOCK:
            return self._fold().refresh(self.path)

    def state(self, command_id: str) -> CommandState | None:
        return self.book().commands.get(command_id)

    def place(self, subject: str) -> Place:
        """Where *subject*'s commands are read: a draft a mint adopted, at its cycle."""
        return self._fold().place_of(self.landed_on(subject))

    def of_subject(self, place: Place) -> tuple[tuple[str, CommandState], ...]:
        """*place*'s events in log order: each the command it moved and where it moved it."""
        return self.book().by_subject.get(place, ())

    def landed_on(self, subject: str) -> str:
        """Where *subject*'s commands are read: a draft a mint adopted reads as its cycle."""
        return self.book().landed.get(subject, subject)

    def keyed(
        self, kind: str, act_digest: str, idempotency_key: str, subject: str
    ) -> CommandState | None:
        """The command a retry under *idempotency_key* names on *subject*: matched on (kind, act,
        key), the act its whole validated payload, by its digest; a key the subject holds for
        another act is refused, never replayed. A command that ended without happening satisfies
        no key."""
        found: CommandState | None = None
        for _, state in self.of_subject(self.place(subject)):
            filed = state.filed
            if filed.idempotency_key != idempotency_key or filed.subject != subject:
                continue
            if filed.kind != kind or filed.act_digest != act_digest:
                raise IdempotencyKeyReusedError(
                    f"Key {idempotency_key!r} already names a {filed.kind} command with another "
                    f"payload here; send this {kind} under a key of its own.",
                    details={"command_id": filed.command_id},
                )
            current = self.state(filed.command_id)
            if current is not None and current.status in ("filed", "accepted", "applied"):
                found = current
        return found

    def append(self, event: CommandLogRow) -> CommandState | None:
        """Lands *event* only where the fold takes it, checked under the file's own write lock;
        the state it moved its command to, ``None`` for a subject's landing."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        landed: list[CommandState] = []

        def _check() -> None:
            if isinstance(event, SubjectLanded):
                return
            held = self.book().commands.get(event.command_id)
            landed.append(_moved(held, event))

        append_row(self.path, json.loads(COMMAND_EVENT_ADAPTER.dump_json(event)), check=_check)
        return landed[0] if landed else None


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


class _Inbox:
    """The cycle's loop commands in log order. One accepted under an earlier ``run_mark`` than
    the cycle's now was left by a stop or a fresh launch, and stands no more; an ``auto``
    look-ahead is a mode, and stands across them. A superseded look-ahead is voided on the log
    (`superseded_lookahead`), so it leaves here by its own event."""

    def __init__(self, run_mark: int) -> None:
        self._mark = run_mark
        self._held = Controls()
        self._armed: dict[str, type[LoopPayload]] = {}
        self._lookahead_mark: int | None = None

    def feed(self, command_id: str, state: CommandState) -> None:
        if state.filed.kind not in LOOP_KINDS:
            return
        match state.status:
            case "accepted":
                self._arm(command_id, state)
            case "applied" if state.run_mark is None:
                # Applied with no accept before it: a look-ahead DISARM, nothing to take.
                self._arm(command_id, state)
            case "applied":
                if kind := self._armed.pop(command_id, None):
                    self._take(command_id, kind)
            case "refused" | "voided" | "cancelled":
                if kind := self._armed.pop(command_id, None):
                    self._take(command_id, kind, dropped=True)
            case _:
                pass

    def _arm(self, command_id: str, state: CommandState) -> None:
        held, filed = self._held, state.filed
        kind = PAYLOAD_MODEL_FOR_KIND[filed.kind]
        assert issubclass(kind, LoopPayload), "only a kind in `LOOP_KINDS` is asked"
        payload = kind.model_validate(filed.payload)
        if not isinstance(payload, SetSampleLookaheadPayload) and state.run_mark != self._mark:
            return
        match payload:
            case PauseCyclePayload():
                if held.pause is not None:
                    return
                held = held._replace(pause=PauseAsk(command_id, filed.issued_by, filed.timestamp))
            case SkipSearchpointPayload():
                held = held._replace(skips=(*held.skips, command_id))
            case OriginGateDecisionPayload(decision=decision):
                held = held._replace(gate_decisions=(*held.gate_decisions, (command_id, decision)))
            case SubmitArmsPayload() as submitted:
                held = held._replace(submissions=(*held.submissions, (command_id, submitted)))
            case SetSampleLookaheadPayload(cells=cells, auto=auto):
                armed = auto or cells > 1
                self._lookahead_mark = state.run_mark
                held = held._replace(
                    lookahead=LookaheadAsk(command_id, cells, auto) if armed else None
                )
                if not armed:
                    self._held = held
                    return
            case _:
                return
        self._armed[command_id] = kind
        self._held = held

    def _take(self, command_id: str, kind: type[LoopPayload], *, dropped: bool = False) -> None:
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
            if ask is None or ask.command_id != command_id:
                pass
            elif ask.auto and not dropped:
                held = held._replace(lookahead=ask._replace(taken=True))
            else:
                held = held._replace(lookahead=None)
        self._held = held

    def value(self) -> Controls:
        ask = self._held.lookahead
        if ask is not None and not ask.auto and self._lookahead_mark != self._mark:
            return self._held._replace(lookahead=None)
        return self._held


def standing_on(log: CommandLog, place: Place, run_mark: int) -> Controls:
    """*place*'s inbox as of its cycle's *run_mark* (`ledger_scan.py::scan_run_mark`)."""
    inbox = _Inbox(run_mark)
    for command_id, state in log.of_subject(place):
        inbox.feed(command_id, state)
    return inbox.value()


def superseded_lookahead(log: CommandLog, place: Place, by: str) -> str | None:
    """The look-ahead standing on *place* that one accepted as *by* leaves, ``None`` where none
    stands: its supersede is voided on the log, so its own events say it stands no more."""
    standing: str | None = None
    for command_id, state in log.of_subject(place):
        if command_id == by or state.filed.kind not in LOOP_KINDS:
            continue
        payload = PAYLOAD_MODEL_FOR_KIND[state.filed.kind]
        if payload is not SetSampleLookaheadPayload:
            continue
        current = log.state(command_id)
        if current is not None and current.status == "accepted":
            standing = command_id
    return standing
