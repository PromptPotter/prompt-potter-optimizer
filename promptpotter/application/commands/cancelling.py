"""The one stop of a command at every door, by its id: a `cancel` on the account's log."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from promptpotter.application.command_reading import launched_jobs, owned_command
from promptpotter.application.commands.dispatcher import Applier
from promptpotter.application.commands.launching import launch_cancels
from promptpotter.domain.command_kinds import CommandKind
from promptpotter.shared.errors import TaskUncancellableError

if TYPE_CHECKING:
    from promptpotter.application.commands.dispatcher import Dispatched
    from promptpotter.domain.command_kinds import CancelPayload

# A paid pass reads its cancel as its pause, by its own command id (`RunControl.acting`): it stops
# at its next cell and answers over what it banked. Every kind `measuring.py` answers is one.
_PASS_KINDS = frozenset(k for k in CommandKind if k.handler.startswith("measuring:"))


async def cancel(dispatched: Dispatched[CancelPayload]) -> Applier[object]:
    """A pass still acting: applied, it stands on the log until the pass ends, read by that pass
    alone (`CommandBook.stop`). A launch: each job it launched, by its own act
    (`launching.py::launch_cancels`). A command that ended otherwise is left as it ended, and
    anything else acting is refused, nothing touched."""
    dispatcher, target = dispatched.dispatcher, dispatched.payload.command_id
    stores = dispatcher.stores

    async def _ask() -> None:
        owned = await asyncio.to_thread(owned_command, stores, target)
        kind = owned.filed.kind
        jobs = await asyncio.to_thread(launched_jobs, dispatcher.job_registry, owned)
        if jobs is not None:
            for act in await asyncio.to_thread(launch_cancels, jobs, stores):
                await dispatcher.dispatch(act)
        elif owned.end is None and kind not in _PASS_KINDS:
            raise TaskUncancellableError(f"{kind} acts to its end; ask again once it ends")
        elif owned.end is not None and owned.end.status == "accepted":
            raise TaskUncancellableError(f"{kind} stands for its loop, which takes it")

    return Applier.silent(_ask)
