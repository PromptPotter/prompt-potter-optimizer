"""The account's command log: what was ASKED, one append-only stream per account
(`.workspace/commands.jsonl` under the workspace a command addresses). The cycle ledger keeps what
HAPPENED; no command event rides it, so moving or deleting a ledger moves no command's status.

Every event is written under the log's own check lock, after the fold it lands on accepts it
(`run_records.py::COMMAND_TRANSITIONS`), so no reader meets a move the table refuses."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import NamedTuple

from promptpotter.domain.command_kinds import (
    PAYLOAD_MODEL_FOR_KIND,
    LoopPayload,
    OriginGateDecisionPayload,
    PauseCyclePayload,
    SetSampleLookaheadPayload,
    SkipSearchpointPayload,
    SubmitArmsPayload,
)
from promptpotter.domain.cycle_paths import CycleHop, WorkspaceDir
from promptpotter.domain.run_records import (
    COMMAND_EVENT_ADAPTER,
    CommandEvent,
    CommandFiled,
    CommandState,
    CommandSubject,
)
from promptpotter.infrastructure.store.io import ABSENT, FileMark, held_lock
from promptpotter.infrastructure.store.layout import WORKSPACE_STATE_DIR
from promptpotter.infrastructure.store.read_model import append_row, read_rows_past
from promptpotter.shared.errors import ConflictError, IdempotencyKeyReusedError
from promptpotter.shared.identity import Principal

COMMAND_LOG_FILE = "commands.jsonl"
COMMAND_CHECK_LOCK = ".commands.lock"
# The check lock waits out another writer's check-and-append, never a long act.
_CHECK_WAIT_S = 30.0


def command_log_path(workspace_dir: WorkspaceDir | Path) -> Path:
    """Never creates it: a read must not mint a `.workspace/` in a tenant that filed nothing."""
    return Path(workspace_dir) / WORKSPACE_STATE_DIR / COMMAND_LOG_FILE


def cycle_workspace(cycle_dir: Path) -> WorkspaceDir:
    """The workspace a cycle dir sits in: ``<workspace>/campaigns/<campaign>/cycles/<cycle>``."""
    return WorkspaceDir(cycle_dir.absolute().parents[3])


class CommandIdReusedError(ConflictError):
    """A command id is filed once: a second filing under it is refused, never merged."""

    code = "command_id_reused"


class CommandBook(NamedTuple):
    """The log folded: each command's state, and each subject's events in log order."""

    commands: dict[str, CommandState]
    # (command_id, the status that event moved it to), in the order the log holds them.
    by_subject: dict[str, list[tuple[str, CommandState]]]


def _row(raw: bytes) -> CommandEvent:
    return COMMAND_EVENT_ADAPTER.validate_json(raw)


class _Fold:
    def __init__(self) -> None:
        self.mark: FileMark = ABSENT
        self.sig: tuple[int, int, int] | None = None
        self.book = CommandBook({}, {})

    def feed(self, event: CommandEvent) -> None:
        commands, by_subject = self.book
        held = commands.get(event.command_id)
        if isinstance(event, CommandFiled):
            if held is not None:
                raise ValueError(f"command {event.command_id} is filed twice")
            CommandState.moved(None, "filed")
            state = CommandState(filed=event, status="filed", at=event.timestamp)
        elif held is None:
            raise ValueError(f"an {event.event} event names unfiled command {event.command_id}")
        else:
            state = held.after(event)
        commands[event.command_id] = state
        by_subject.setdefault(state.filed.subject, []).append((event.command_id, state))

    def refresh(self, path: Path) -> CommandBook:
        try:
            st = path.stat()
        except FileNotFoundError:
            self.mark, self.sig, self.book = ABSENT, None, CommandBook({}, {})
            return self.book
        sig = (st.st_ino, st.st_size, st.st_mtime_ns)
        if sig != self.sig:
            read = read_rows_past(path, _row, self.mark)
            if read.whole:
                self.book = CommandBook({}, {})
            for event in read.rows:
                self.feed(event)
            self.mark, self.sig = read.mark, sig
        return self.book


_FOLDS: dict[Path, _Fold] = {}
_FOLDS_LOCK = threading.Lock()


class CommandLog:
    """One account's log. Reads are incremental folds, cached per file."""

    def __init__(self, workspace_dir: WorkspaceDir | Path) -> None:
        self.path = command_log_path(workspace_dir)

    @classmethod
    def of_cycle(cls, cycle_dir: Path) -> CommandLog:
        return cls(cycle_workspace(cycle_dir))

    def book(self) -> CommandBook:
        with _FOLDS_LOCK:
            fold = _FOLDS.setdefault(self.path, _Fold())
            return fold.refresh(self.path)

    def state(self, command_id: str) -> CommandState | None:
        return self.book().commands.get(command_id)

    def of_subject(self, subject: str) -> list[tuple[str, CommandState]]:
        """*subject*'s events in log order: each the command it moved and where it moved it."""
        return list(self.book().by_subject.get(subject, ()))

    def keyed(self, kind: str, act_digest: str, idempotency_key: str) -> CommandState | None:
        """The command a retry under *idempotency_key* names: matched on (kind, act, key), the act
        its whole validated payload, by its digest; a key the log holds for another act is refused,
        never replayed. A command that ended without happening satisfies no key."""
        found: CommandState | None = None
        for state in self.book().commands.values():
            filed = state.filed
            if filed.idempotency_key != idempotency_key:
                continue
            if filed.kind != kind or filed.act_digest != act_digest:
                raise IdempotencyKeyReusedError(
                    f"Key {idempotency_key!r} already names a {filed.kind} command with another "
                    f"payload on this account; send this {kind} under a key of its own.",
                    details={"command_id": filed.command_id},
                )
            if state.status in ("filed", "accepted", "applied"):
                found = state
        return found

    def append(self, event: CommandEvent) -> CommandState:
        """Lands *event* only where the fold takes it, checked and appended under one lock."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with held_lock(self.path.parent / COMMAND_CHECK_LOCK, wait_s=_CHECK_WAIT_S):
            with _FOLDS_LOCK:
                fold = _FOLDS.setdefault(self.path, _Fold())
                commands = fold.refresh(self.path).commands
            held = commands.get(event.command_id)
            if isinstance(event, CommandFiled):
                if held is not None:
                    raise CommandIdReusedError(
                        f"command id {event.command_id} is already filed",
                        details={"command_id": event.command_id},
                    )
                landed = CommandState(filed=event, status="filed", at=event.timestamp)
            elif held is None:
                raise ConflictError(
                    f"no command {event.command_id} is filed", code="command_unfiled"
                )
            else:
                try:
                    landed = held.after(event)
                except ValueError as exc:
                    raise ConflictError(
                        f"command {event.command_id} is {held.status}: {exc}",
                        code="command_transition_refused",
                        details={"command_id": event.command_id, "status": held.status},
                    ) from exc
            append_row(self.path, json.loads(COMMAND_EVENT_ADAPTER.dump_json(event)))
        return landed


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


_LOOP_TAKEN = frozenset(
    kind for kind, model in PAYLOAD_MODEL_FOR_KIND.items() if issubclass(model, LoopPayload)
)


class _Inbox:
    """The cycle's loop commands in log order. One accepted under an earlier ``run_mark`` than
    the cycle's now was left by a stop or a fresh launch, and stands no more; an ``auto``
    look-ahead is a mode, and stands across them."""

    def __init__(self, run_mark: int) -> None:
        self._mark = run_mark
        self._held = Controls()
        self._armed: dict[str, type[LoopPayload]] = {}
        self._lookahead_mark: int | None = None

    def feed(self, command_id: str, state: CommandState) -> None:
        if state.filed.kind not in _LOOP_TAKEN:
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
        assert issubclass(kind, LoopPayload), "only a kind in `_LOOP_TAKEN` is asked"
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
                if held.lookahead is not None:
                    self._armed.pop(held.lookahead.command_id, None)
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
            if ask is not None and ask.auto and not dropped:
                held = held._replace(lookahead=ask._replace(taken=True))
            else:
                held = held._replace(lookahead=None)
        self._held = held

    def value(self) -> Controls:
        ask = self._held.lookahead
        if ask is not None and not ask.auto and self._lookahead_mark != self._mark:
            return self._held._replace(lookahead=None)
        return self._held


def standing_on(log: CommandLog, hop: CycleHop, run_mark: int) -> Controls:
    """*hop*'s inbox as of its cycle's *run_mark* (`ledger_scan.py::scan_run_mark`)."""
    inbox = _Inbox(run_mark)
    for command_id, state in log.of_subject(CommandSubject.cycle(hop)):
        inbox.feed(command_id, state)
    return inbox.value()
