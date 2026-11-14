"""What was ASKED: the events of the account's command log (`store/command_log.py`), never a cycle
ledger's, which keeps what happened. A command is its filing and the status events after it; its
status is their fold by `COMMAND_TRANSITIONS`."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Self, get_args

from pydantic import Field, TypeAdapter, model_validator

from promptpotter.domain.cycle_paths import Subject
from promptpotter.domain.records.run_records import (
    DraftOpenedEffect,
    LaunchedEffect,
    NoEffect,
    StandingEffect,
)
from promptpotter.domain.refusal import Refusal
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import PotterError
from promptpotter.shared.identity import Principal

CommandAckStatus = Literal["accepted", "applied", "rejected", "voided", "cancelled"]
COMMAND_ACK_STATUSES: tuple[CommandAckStatus, ...] = get_args(CommandAckStatus)
# Where a command stands: filed and nothing after, or the status its last event moved it to.
CommandStatus = Literal["filed", CommandAckStatus]

# The subject a command is filed on, as the log spells it: the workspace, a draft, or a campaign or
# course in `Subject`'s own encoding (`Subject.key`, its sandbox chain kept).
WORKSPACE_SUBJECT = ""
_DRAFT = "draft:"


def subject_key(subject: Subject) -> str:
    return subject.key


def draft_subject(draft_id: str) -> str:
    return f"{_DRAFT}{draft_id}"


def draft_of(spelled: str) -> str | None:
    return spelled.removeprefix(_DRAFT) if spelled.startswith(_DRAFT) else None


def subject_of(spelled: str) -> Subject | None:
    """The campaign or course *spelled* names; ``None`` for the workspace or a draft."""
    if spelled == WORKSPACE_SUBJECT or draft_of(spelled) is not None:
        return None
    return Subject.parse(spelled)


class CommandRecord(FrozenModel):
    """``CommandDispatcher`` alone files it, on the subject it resolved."""

    record_type: Literal["command"] = "command"
    command_id: str
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str
    # `CommandPayload.recorded`: the act its key names, the fields `payload` leaves out included.
    act_digest: str
    issued_by: Principal
    subject: str
    # The producer that files and acts it: gone with no end, the act died with it (`producer_alive`).
    filer: str
    timestamp: str = Field(default_factory=utcnow_iso)


CommandEffect = Annotated[
    NoEffect | StandingEffect | DraftOpenedEffect | LaunchedEffect, Field(discriminator="kind")
]


class KeptRefusal(FrozenModel):
    """An act's refusal as its door served it, kept on its end so any door reading the command
    later serves it again, its status beside it: never rebuilt from a class name."""

    http_status: int
    refusal: Refusal

    @classmethod
    def of(cls, refused: PotterError) -> Self:
        return cls(http_status=refused.http_status, refusal=Refusal.of(refused))

    def raised(self) -> PotterError:
        error = PotterError(
            self.refusal.message, code=self.refusal.code, details=self.refusal.details
        )
        error.http_status = self.http_status
        return error


class CommandAckRecord(FrozenModel):
    """``applied`` = it HAPPENED; a loop command is ``accepted`` first, for the hold its
    `StandingEffect` names, and stays so if untaken until the run boundary ``voided`` it. ``cancelled``: its act was interrupted. A ``rejected``
    act keeps the refusal its door served, and a task kind's applied act its modelled answer
    (`dispatcher.py::keeps_answer`): what its task's result and its retry answer."""

    record_type: Literal["command_ack"] = "command_ack"
    command_id: str
    status: CommandAckStatus
    detail: str = ""
    effect: CommandEffect = Field(default_factory=NoEffect)
    answer: dict[str, Any] | None = None
    refusal: KeptRefusal | None = None
    timestamp: str = Field(default_factory=utcnow_iso)

    @model_validator(mode="after")
    def _accepted_names_what_stands(self) -> Self:
        if self.status == "accepted" and isinstance(self.effect, NoEffect):
            raise ValueError("an accept names the hold it stands for or the launch it admitted")
        return self


class CommandLanded(FrozenModel):
    """A draft's mint adopted it as a cycle (`jobs/mint.py`): every command filed on ``subject``
    reads as ``onto``'s, ahead of the cycle's own."""

    record_type: Literal["command_landed"] = "command_landed"
    subject: str
    onto: str
    timestamp: str = Field(default_factory=utcnow_iso)


CommandLogRow = Annotated[
    CommandRecord | CommandAckRecord | CommandLanded, Field(discriminator="record_type")
]
COMMAND_LOG_ADAPTER: TypeAdapter[CommandLogRow] = TypeAdapter(CommandLogRow)


def _moves(*to: CommandAckStatus) -> dict[CommandAckStatus, CommandStatus | None]:
    return {event: (event if event in to else None) for event in COMMAND_ACK_STATUSES}


_ENDED: tuple[CommandAckStatus, ...] = ("applied", "rejected", "voided", "cancelled")
_ROWS: dict[CommandStatus, dict[CommandAckStatus, CommandStatus | None]] = {
    "filed": _moves("accepted", "applied", "rejected", "cancelled"),
    "accepted": _moves("applied", "voided", "cancelled"),
    **{ended: _moves() for ended in _ENDED},
}
# EXHAUSTIVE over (status, event): a ``None`` value is a move the fold refuses, so it moves nothing.
COMMAND_TRANSITIONS: dict[tuple[CommandStatus, CommandAckStatus], CommandStatus | None] = {
    (status, event): to for status, row in _ROWS.items() for event, to in row.items()
}
