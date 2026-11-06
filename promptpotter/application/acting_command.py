"""The command whose act this task runs: filed on the account's command log before its act, ended
there once, and stopped by what that log says (`cancel-command`), never by anything this process
alone holds — so a stop sent from any process reaches it."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from promptpotter.application.jobs.interlock import this_producer
from promptpotter.domain.run_records import (
    CommandCancelled,
    CommandFiled,
    CommandRefused,
    CommandState,
)
from promptpotter.shared.errors import ConflictError, PotterError
from promptpotter.shared.identity import acting_principal

if TYPE_CHECKING:
    from promptpotter.domain.run_records import CommandAccepted, CommandApplied
    from promptpotter.infrastructure.store.command_log import CommandLog
    from promptpotter.infrastructure.store.stores import Stores


class PassStoppedError(ConflictError):
    """A pause or a cancel reached the pass: what it measured is banked, and a rerun replays it."""

    code = "pass_stopped"


def filing(
    stores: Stores,
    *,
    command_id: str,
    kind: str,
    payload: dict[str, Any],
    idempotency_key: str,
    act_digest: str,
    subject: str,
) -> CommandFiled:
    """THE one build of a filing: who issued it, and the process that files it and runs its act."""
    return CommandFiled(
        command_id=command_id,
        kind=kind,
        payload=payload,
        idempotency_key=idempotency_key,
        act_digest=act_digest,
        issued_by=acting_principal(stores.identity),
        subject=subject,
        filer=this_producer(stores.jobs_dir),
    )


def ended_by(exc: BaseException, command_id: str) -> CommandRefused | CommandCancelled:
    """How an act that raised *exc* ended: refused by an error, cancelled by an interrupt or a
    pass's stop, the error's class and code kept for a door that answers it later."""
    error = type(exc).__name__
    code = exc.code if isinstance(exc, PotterError) else ""
    said = str(exc) or error
    if isinstance(exc, Exception) and not isinstance(exc, PassStoppedError):
        return CommandRefused(command_id=command_id, error=error, code=code, detail=said)
    return CommandCancelled(command_id=command_id, error=error, code=code, detail=said)


class ActingCommand:
    """One filed command whose act runs in this task."""

    def __init__(self, log: CommandLog, command_id: str) -> None:
        self.log = log
        self.command_id = command_id

    def cancel(self) -> CommandState | None:
        """The cancel that reached it on the log, ``None`` while none did."""
        state = self.log.state(self.command_id)
        return state if state is not None and state.status == "cancelled" else None

    def end(
        self, event: CommandApplied | CommandAccepted | CommandRefused | CommandCancelled
    ) -> None:
        """Lands *event*, unless another end landed first (checked under the log's write lock): a
        cancel, or the void of a filer read as gone. That one stands."""
        try:
            self.log.append(event)
        except ConflictError:
            state = self.log.state(self.command_id)
            if state is None or state.status in ("filed", "accepted"):
                raise


_ACTING: ContextVar[ActingCommand | None] = ContextVar("acting_command", default=None)


def acting_command() -> ActingCommand | None:
    return _ACTING.get()


async def _landed(write: asyncio.Future[Any]) -> bool:
    """Waits out a filing's write a cancel interrupted the wait on, every further cancel too:
    whether it landed decides whether there is a command to end."""
    while not write.done():
        # `wait` never raises the write's own error, nor cancels it when this wait is cancelled.
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait({write})
    return not write.cancelled() and write.exception() is None


@asynccontextmanager
async def filed(log: CommandLog, filed_as: CommandFiled) -> AsyncIterator[ActingCommand]:
    """Files *filed_as* on *log* and runs the body as its act; a raise ends it (`ended_by`) once
    the filing landed — never before, so no end lands on a command not filed. The body ends a
    command that answered."""
    acting = ActingCommand(log, filed_as.command_id)
    write = asyncio.ensure_future(asyncio.to_thread(log.append, filed_as))
    try:
        await asyncio.shield(write)
    except BaseException as exc:
        if await _landed(write):
            acting.end(ended_by(exc, acting.command_id))
        raise
    token = _ACTING.set(acting)
    try:
        yield acting
    except BaseException as exc:
        acting.end(ended_by(exc, acting.command_id))
        raise
    finally:
        _ACTING.reset(token)
