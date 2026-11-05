"""A command's stop, the same at every door: the act its record names stopped by its own act."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from promptpotter.application.command_status import (
    TERMINAL,
    CommandStatus,
    asked,
    command_status,
    launched_jobs,
    settled,
)
from promptpotter.application.commands.dispatcher import Applier, CommandCall
from promptpotter.application.jobs.launcher.admission import held_pass
from promptpotter.domain.command_kinds import (
    PAYLOAD_MODEL_FOR_KIND,
    CancelQueuedRunPayload,
    CommandPayload,
    CyclePayload,
    LinePayload,
    PauseCyclePayload,
)
from promptpotter.domain.cycle_paths import CyclePath, Subject
from promptpotter.domain.job import UNRESOLVED_HOP, InnerRun, Job
from promptpotter.domain.phases import INNER_PAUSE_REFUSAL
from promptpotter.infrastructure.producer_lock import pass_held
from promptpotter.infrastructure.runtime_flags import derive_run_state
from promptpotter.infrastructure.store.command_index import Unrecorded
from promptpotter.infrastructure.store.layout import cycle_dir_for
from promptpotter.infrastructure.store.stores import owned_campaign, resolve_cycle_path
from promptpotter.shared.errors import CommandUncancellableError

if TYPE_CHECKING:
    from promptpotter.application.commands.dispatcher import CommandDispatcher, Dispatched
    from promptpotter.domain.command_kinds import CancelCommandPayload
    from promptpotter.domain.cycle_paths import WorkspaceDir
    from promptpotter.infrastructure.store.command_index import AskedCommand
    from promptpotter.infrastructure.store.stores import Stores

_CANCELLED = "command cancelled"


def cancel_acts(jobs: list[Job], workspace: WorkspaceDir) -> list[CommandCall[CommandPayload]]:
    """Each live job's own stop, resolved for every job before any is dispatched: a queued one
    withdrawn, a running run paused on its own cycle in *workspace*, each keyed by the job so a
    retried cancel acts once. A job no command of *workspace* stops now — its cycle refusing the
    pause (`RunState.pause_refusal`) among them — refuses them all."""
    acts: list[CommandCall[CommandPayload]] = []
    for job in jobs:
        key = f"cancel-command:{job.job_id}"
        if job.released:
            continue
        if job.queued:
            acts.append(CommandCall(CancelQueuedRunPayload(job_id=job.job_id), key))
            continue
        if isinstance(job.holder, InnerRun):
            raise CommandUncancellableError(f"{job.what}: {INNER_PAUSE_REFUSAL}")
        if job.hop == UNRESOLVED_HOP:
            raise CommandUncancellableError(f"{job.what} names no cycle to pause yet; cancel again")
        if job.workspace != str(workspace):
            raise CommandUncancellableError(
                f"{job.what} runs in a workspace this caller does not hold"
            )
        if refusal := derive_run_state(cycle_dir_for(workspace, job.hop)).pause_refusal:
            raise CommandUncancellableError(f"{job.what}: {refusal}")
        pause = PauseCyclePayload(
            campaign_id=job.campaign_id, cycle_id=job.cycle_id, reason=_CANCELLED
        )
        acts.append(CommandCall(pause, key))
    return acts


def _addressed_path(stores: Stores, command: AskedCommand) -> CyclePath:
    """The cycle an in-flight act scores on: a cycle command's own, a line command's holder; an
    act addressing no cycle runs nothing a pause stops."""
    record = command.record
    payload = PAYLOAD_MODEL_FOR_KIND[record.kind].model_validate(record.payload)
    match payload:
        case CyclePayload():
            return Subject.below(payload.campaign_id, payload.cycle_id, payload.descend).path
        case LinePayload():
            root = owned_campaign(stores, payload.campaign_id).root_hop
            return (stores.campaigns.line_holder(root),)
    raise CommandUncancellableError(f"{record.kind} runs inside its own call; nothing stops it")


async def stop_pass(dispatcher: CommandDispatcher, path: CyclePath, command_id: str | None) -> None:
    """The one stop of the paid pass *command_id*'s act runs on *path* (``None``: a pass no
    command records): its cycle's pause, sent under that pass's hold on it
    (`admission.py::PassHold.stopping`), so it reaches that pass or is refused, never another
    command's or the producer after."""
    leaf_stores, leaf = await asyncio.to_thread(resolve_cycle_path, dispatcher.stores, path)
    pause = PauseCyclePayload(**Subject.of(path).command_fields, reason=_CANCELLED)
    cycle_dir = leaf_stores.campaigns.cycle_dir(leaf)
    hold = held_pass(cycle_dir)
    if hold is None:
        elsewhere = await asyncio.to_thread(pass_held, cycle_dir)
        raise CommandUncancellableError(
            f"the pass on {leaf.cycle_id} runs in another process: stop it there"
            if elsewhere
            else f"no pass holds {leaf.cycle_id} yet, so no pause stops one; ask again"
        )
    async with hold.stopping(command_id):
        await dispatcher.dispatch(CommandCall(pause))


async def cancel_command(
    dispatched: Dispatched[CancelCommandPayload],
) -> Applier[CommandStatus]:
    """Answers the command's status as its stop left it, or as it ended where nothing of it
    runs; another principal's command is not found, as an unknown one is."""
    dispatcher, command_id = dispatched.dispatcher, dispatched.payload.command_id
    stores, jobs = dispatcher.stores, dispatcher.job_registry

    async def _apply() -> CommandStatus:
        status = await asyncio.to_thread(command_status, stores, jobs, command_id)
        if status.status in TERMINAL:
            return status
        found = await asyncio.to_thread(asked, stores, command_id)
        if isinstance(found, Unrecorded):
            # Its record is landing, and nothing of its act runs before it.
            raise CommandUncancellableError(f"{status.said}; ask again")
        command = found.asked
        if settled(command) is None:
            # In flight, the act runs inside its own call: a paid pass, which its pause stops.
            path = await asyncio.to_thread(_addressed_path, stores, command)
            await stop_pass(dispatcher, path, command_id)
        else:
            launched = await asyncio.to_thread(launched_jobs, stores, jobs, command)
            acts = await asyncio.to_thread(cancel_acts, launched or [], stores.campaigns.workspace)
            if not acts:
                # Acked and launching nothing: a loop command standing for its loop to take.
                raise CommandUncancellableError(
                    f"{status.said}: it stands until its loop takes it, or ends without it"
                )
            for act in acts:
                await dispatcher.dispatch(act)
        return await asyncio.to_thread(command_status, stores, jobs, command_id)

    return Applier(_apply, replay=None)
