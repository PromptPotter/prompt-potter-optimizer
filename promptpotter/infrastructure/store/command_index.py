"""Where each command of an account lives: the one id index, and the one record↔ack join.

Every command the dispatcher records is first filed here against the OWNER of the ledger its record
goes on (a workspace, a cycle or a draft, at any sandbox depth); an id the index already files is
refused, so an id names one act in the whole account, and a read opens the one ledger its owner
holds now. Filing is append-then-read: the first line for an id is its filing, so two processes
racing one id leave one winner and no second lock.

Filing and the record are two writes on two files, so a read derives what stands between them from
facts, never a clock (`CommandIndex.asked`):

- a draft's ledger moved whole into the cycle it minted is filed as moving by the move itself, past
  its precondition and BEFORE the rename, and the rename decides: the ledger is read where its file
  lies, on either side of a crash, and a move whose rename raised is never followed;
- a destroyer (a cycle, a campaign at either keep, a sandbox, a draft discarded or closed) files
  the directory it removes before it removes it, and every id filed before that, whose ledger lies
  under it, reads deleted: the delete's own fact, never what a directory left standing says;
- an id whose owner stands but whose record is not on its ledger is its filer's to land: landing
  while the filer's process lives (the producer lock its filing names), else never landed;
- a filer whose record raised says so here (`void`), and no retry writes a record under that id.

What a found command's filing carries beyond that, its filer, is what a reader asks of an act
recorded without its ack (`application/command_status.py`).
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any, ClassVar, Literal, NamedTuple

from pydantic import TypeAdapter

from promptpotter.domain.command_kinds import CommandKind
from promptpotter.domain.cycle_paths import WorkspaceDir
from promptpotter.domain.run_records import CommandAckRecord, CommandRecord
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.infrastructure.store.layout import (
    WORKSPACE_LEDGER_FILE,
    WORKSPACE_STATE_DIR,
    CycleLayout,
)
from promptpotter.infrastructure.store.read_model import (
    LedgerIndex,
    LineIndex,
    RecordClasses,
    append_row,
)
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import CommandIdReusedError

COMMAND_INDEX_FILE = f"{WORKSPACE_STATE_DIR}/command_ids.jsonl"

type OwnerKind = Literal["workspace", "cycle", "draft"]


class LedgerOwner(FrozenModel):
    """What a ledger belongs to, by its directory relative to the account's workspace
    (``projects/<tenant>``). An inner sandbox's lies outside it, under the data root's ``.inner/``
    beside ``projects/``, so its relative path climbs out first: ``../../.inner/<key>/<tenant>/…``."""

    kind: OwnerKind
    dir: str

    def ledger(self, account: Path) -> Path:
        root = account / self.dir
        return (
            root / WORKSPACE_LEDGER_FILE if self.kind == "workspace" else CycleLayout(root).ledger
        )

    def under(self, removed: str) -> bool:
        return self.dir == removed or self.dir.startswith(f"{removed}/")


class CommandFiled(FrozenModel):
    """*kind*, *issued_by* and *at* name the act before its record lands; *filer* is the producer
    id of the process filing it, whose liveness says a record still lands; *nonce* tells the filer
    its own line from a racer's."""

    command_id: str
    owner: LedgerOwner
    kind: CommandKind
    issued_by: str
    at: str
    filer: str
    nonce: str


class LedgerMoving(FrozenModel):
    """Filed before the move it names: the ledger lies where its file is."""

    moved: LedgerOwner
    to: LedgerOwner


class FilingVoided(FrozenModel):
    voided: str


class OwnerRemoved(FrozenModel):
    """A destroyer's directory, relative to the account, filed before it goes."""

    removed: str


INDEX_LINE: TypeAdapter[CommandFiled | LedgerMoving | FilingVoided | OwnerRemoved] = TypeAdapter(
    CommandFiled | LedgerMoving | FilingVoided | OwnerRemoved
)

# Why a filed id has no record to read: its owner was deleted; its filer raised before it; its
# owner stands without it (its filer's liveness says landing or never landed).
type UnrecordedWhy = Literal["deleted", "voided", "absent"]


class Unrecorded(NamedTuple):
    filed: CommandFiled
    why: UnrecordedWhy


class AskedCommand(NamedTuple):
    """A command as its ledger holds it: its record and every ack after it, in ledger order."""

    record: CommandRecord
    acks: tuple[CommandAckRecord, ...] = ()


class FoundCommand(NamedTuple):
    """A command by its id: its filing, and its record and acks off the ledger its owner holds."""

    filed: CommandFiled
    asked: AskedCommand


class CommandsFold:
    """The one record↔ack join of a ledger, by command id: what a status read, a keyed retry and a
    running loop's inbox each read. An id holds its first record; the index refuses a second."""

    records: ClassVar[RecordClasses] = (CommandRecord, CommandAckRecord)

    def __init__(self) -> None:
        self._asked: dict[str, AskedCommand] = {}

    def feed(self, record: CommandRecord | CommandAckRecord) -> None:
        match record:
            case CommandRecord(command_id=command_id):
                self._asked.setdefault(command_id, AskedCommand(record))
            case CommandAckRecord(command_id=command_id):
                if (asked := self._asked.get(command_id)) is not None:
                    self._asked[command_id] = asked._replace(acks=(*asked.acks, record))

    def get(self, command_id: str) -> AskedCommand | None:
        return self._asked.get(command_id)

    def value(self) -> dict[str, AskedCommand]:
        return dict(self._asked)


def ledger_commands(ledger: Path) -> dict[str, AskedCommand]:
    return LedgerIndex.of(ledger, (CommandsFold,)).view(CommandsFold)


class _Filed(NamedTuple):
    # Each filing with the line it stands at, and each removal with its own: a removal deletes
    # only what was filed before it, so an id reused on a directory minted again reads its own.
    filings: dict[str, tuple[int, CommandFiled]]
    moves: dict[LedgerOwner, LedgerOwner]
    voided: frozenset[str]
    removals: tuple[tuple[int, str], ...]


class _Filings:
    def __init__(self) -> None:
        self._at = 0
        self._filed: dict[str, tuple[int, CommandFiled]] = {}
        self._moves: dict[LedgerOwner, LedgerOwner] = {}
        self._voided: set[str] = set()
        self._removals: list[tuple[int, str]] = []

    def feed(self, line: dict[str, Any]) -> None:
        self._at += 1
        match INDEX_LINE.validate_python(line):
            case CommandFiled() as filed:
                self._filed.setdefault(filed.command_id, (self._at, filed))
            case LedgerMoving(moved=moved, to=to):
                self._moves[moved] = to
            case FilingVoided(voided=command_id):
                self._voided.add(command_id)
            case OwnerRemoved(removed=removed):
                self._removals.append((self._at, removed))

    def value(self) -> _Filed:
        return _Filed(
            dict(self._filed), dict(self._moves), frozenset(self._voided), tuple(self._removals)
        )


class CommandIndex:
    def __init__(self, workspace: WorkspaceDir | Path) -> None:
        self._workspace = Path(workspace)
        self.path = self._workspace / COMMAND_INDEX_FILE

    def _filed(self) -> _Filed:
        return LineIndex.of(self.path, (_Filings,)).view(_Filings)

    def _relative(self, directory: Path) -> str:
        return Path(os.path.relpath(directory.resolve(), self._workspace.resolve())).as_posix()

    def owner(self, kind: OwnerKind, directory: Path) -> LedgerOwner:
        return LedgerOwner(kind=kind, dir=self._relative(directory))

    def file(
        self,
        command_id: str,
        owner: LedgerOwner,
        *,
        kind: CommandKind,
        issued_by: str,
        filer: str,
    ) -> None:
        """Files *command_id* on *owner*'s ledger, or refuses it where the index files it already."""
        if command_id in self._filed().filings:
            raise CommandIdReusedError(f"Command id {command_id!r} already names an act here.")
        nonce = uuid.uuid4().hex
        line = CommandFiled(
            command_id=command_id,
            owner=owner,
            kind=kind,
            issued_by=issued_by,
            at=utcnow_iso(),
            filer=filer,
            nonce=nonce,
        )
        append_row(self.path, line.model_dump(mode="json"))
        if self._filed().filings[command_id][1].nonce != nonce:
            raise CommandIdReusedError(f"Command id {command_id!r} was filed by another act.")

    def void(self, command_id: str) -> None:
        """Its filer's record raised: the id reads as never landed from now on."""
        append_row(self.path, FilingVoided(voided=command_id).model_dump(mode="json"))

    def moving(self, draft_ledger: Path, cycle_ledger: Path) -> None:
        """A draft's ledger about to move whole into the cycle it minted; written by the move past
        its precondition, before the rename, which decides where every id filed on the draft is
        read."""
        moved = self.owner("draft", CycleLayout.of_ledger(draft_ledger).cycle_dir)
        to = self.owner("cycle", CycleLayout.of_ledger(cycle_ledger).cycle_dir)
        append_row(self.path, LedgerMoving(moved=moved, to=to).model_dump(mode="json"))

    def removing(self, directory: Path) -> None:
        """A destroyer about to remove *directory*, and every ledger under it, past its refusals."""
        append_row(
            self.path, OwnerRemoved(removed=self._relative(directory)).model_dump(mode="json")
        )

    def _holder(self, owner: LedgerOwner, moves: dict[LedgerOwner, LedgerOwner]) -> LedgerOwner:
        """The owner whose ledger holds what *owner*'s did: its own while its file lies there, else
        where it moved once the move happened, which the target's ledger file marks (the rename
        alone writes it: `CampaignStore.adopt_ledger` refuses a target that holds one). A move
        filed whose rename never came leaves the ids with their own owner, and its removal."""
        while (
            owner in moves
            and not owner.ledger(self._workspace).is_file()
            and moves[owner].ledger(self._workspace).is_file()
        ):
            owner = moves[owner]
        return owner

    def asked(self, command_id: str) -> FoundCommand | Unrecorded | None:
        """The command *command_id* names, off the ledger its owner holds now; why it has no
        record there where it has none; ``None`` where nothing files it."""
        filed = self._filed()
        if (found := filed.filings.get(command_id)) is None:
            return None
        at, filing = found
        holder = self._holder(filing.owner, filed.moves)
        if any(holder.under(removed) for after, removed in filed.removals if after > at):
            return Unrecorded(filing, "deleted")
        asked = ledger_commands(holder.ledger(self._workspace)).get(command_id)
        if asked is not None:
            return FoundCommand(filing, asked)
        return Unrecorded(filing, "voided" if command_id in filed.voided else "absent")
