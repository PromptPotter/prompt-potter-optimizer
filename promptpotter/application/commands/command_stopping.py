"""The one stop of a command at every door, by its id (`cancel-command`), and a pass's Ctrl+C."""

from __future__ import annotations

from typing import TYPE_CHECKING, assert_never, get_args

from promptpotter.application.acting_command import PassStoppedError
from promptpotter.application.commands.command_reading import (
    TERMINAL,
    command_status,
    launched_jobs,
    owned_command,
)
from promptpotter.application.commands.dispatcher import (
    Applier,
    CommandCall,
    CommandDispatcher,
    addressed,
)
from promptpotter.domain.command_kinds import (
    CancelQueuedRunPayload,
    CommandKind,
    CyclePayload,
    LinePayload,
    PauseCyclePayload,
)
from promptpotter.domain.cycle_paths import WorkspaceDir
from promptpotter.domain.job import UNRESOLVED_HOP, InnerRun, Job, ScoringVerb
from promptpotter.domain.phases import INNER_PAUSE_REFUSAL
from promptpotter.domain.run_records import CommandCancelled
from promptpotter.infrastructure.runtime_flags import derive_run_state
from promptpotter.infrastructure.store.layout import cycle_dir_for
from promptpotter.infrastructure.store.stores import owned_campaign
from promptpotter.shared.errors import ConflictError, TaskUncancellableError
from promptpotter.shared.identity import acting_principal

if TYPE_CHECKING:
    from promptpotter.application.commands.dispatcher import Dispatched
    from promptpotter.application.jobs.launcher.admission import PassHold
    from promptpotter.domain.command_kinds import CancelCommandPayload, CommandPayload
    from promptpotter.infrastructure.store.stores import Stores


# What a cancel's pause and withdrawal say they were asked for.
_CANCELLED = "command cancelled"
# The kinds whose act is a paid pass, which reads its own cancel off the log (`paid_pass`): a
# verify, a bench, and each pass no command runs, filed under its verb.
_PASS_KINDS = frozenset(
    {CommandKind.VERIFY_CANDIDATE, CommandKind.GRADE_BENCH, *get_args(ScoringVerb)}
)


def pass_pause(
    stores: Stores, payload: CyclePayload | LinePayload, *, reason: str
) -> PauseCyclePayload:
    """A pass's Ctrl+C: a pause of the cycle it scores — the cycle a verify names, the line holder
    a bench grades — at the address the dispatcher acts on (`addressed`), sent under the pass's own
    hold (`stop_pass`)."""
    match acted := addressed(stores, payload):
        case CyclePayload():
            return PauseCyclePayload(
                campaign_id=acted.campaign_id,
                cycle_id=acted.cycle_id,
                descend=acted.descend,
                reason=reason,
            )
        case LinePayload():
            campaign = owned_campaign(stores, acted.campaign_id)
            holder = stores.campaigns.line_holder(campaign.root_hop)
            return PauseCyclePayload(
                campaign_id=holder.campaign_id, cycle_id=holder.cycle_id, reason=reason
            )
        case _:
            assert_never(acted)


async def stop_pass(stores: Stores, hold: PassHold, pause: CommandCall[PauseCyclePayload]) -> None:
    """*pause*, sent under the pass's own hold (`PassHold.stopping`), so it reaches the pass or is
    refused, never a run holding the cycle after."""
    async with hold.stopping(stores, pause.payload):
        await CommandDispatcher(stores).dispatch(pause)


def _job_cancels(jobs: list[Job], workspace: WorkspaceDir) -> list[CommandCall[CommandPayload]]:
    """Each live job's own cancel, resolved for every job before any is dispatched: a queued one
    withdrawn, a running run paused on its own cycle in *workspace*, each keyed by the job so a
    retried cancel acts once. A job no command of *workspace* cancels now — its cycle refusing the
    pause (`RunState.pause_refusal`) among them — refuses them all."""
    acts: list[CommandCall[CommandPayload]] = []
    for job in jobs:
        key = f"cancel:{job.job_id}"
        if job.released:
            continue
        if job.queued:
            acts.append(CommandCall(CancelQueuedRunPayload(job_id=job.job_id), key))
            continue
        if isinstance(job.holder, InnerRun):
            raise TaskUncancellableError(f"{job.what}: {INNER_PAUSE_REFUSAL}")
        if job.hop == UNRESOLVED_HOP:
            raise TaskUncancellableError(f"{job.what} names no cycle to pause yet; cancel again")
        if job.workspace != str(workspace):
            raise TaskUncancellableError(
                f"{job.what} runs in a workspace this caller does not hold"
            )
        if refusal := derive_run_state(cycle_dir_for(workspace, job.hop)).pause_refusal:
            raise TaskUncancellableError(f"{job.what}: {refusal}")
        pause = PauseCyclePayload(
            campaign_id=job.campaign_id, cycle_id=job.cycle_id, reason=_CANCELLED
        )
        acts.append(CommandCall(pause, key))
    return acts


async def cancel_command(dispatched: Dispatched[CancelCommandPayload]) -> Applier[object]:
    """The one stop of a command at every door, by its id: a pass in flight cancelled on the log,
    which the pass reads wherever it runs; a launch's jobs each by its own act; a command already
    ended, as its status reads it, left as it ended. Anything else acting is refused, nothing
    touched."""
    dispatcher, target = dispatched.dispatcher, dispatched.payload.command_id
    stores = dispatcher.stores

    async def _apply() -> None:
        state = owned_command(stores, target)
        kind = state.filed.kind
        if (jobs := launched_jobs(dispatcher.job_registry, state)) is not None:
            for act in _job_cancels(jobs, stores.campaigns.workspace):
                await dispatcher.dispatch(act)
            return
        if command_status(stores, dispatcher.job_registry, target).status in TERMINAL:
            return
        if state.status == "accepted":
            raise TaskUncancellableError(f"{kind} stands for its loop, which takes it")
        if kind not in _PASS_KINDS:
            raise TaskUncancellableError(f"{kind} acts to its end; ask again once it ends")
        cancelled = CommandCancelled(
            command_id=target,
            error=PassStoppedError.__name__,
            code=PassStoppedError.code,
            detail=f"{_CANCELLED} by {acting_principal(stores.identity).display}",
        )
        try:
            stores.commands.append(cancelled)
        except ConflictError:
            # It ended between the read and the cancel: left as it ended.
            if (ended := stores.commands.state(target)) is None or ended.status == "filed":
                raise

    return Applier.silent(_apply)
