"""A command read by its id, off its own ledger record: in flight until its ack while its filer's
process lives (an act whose process died never acks, so it reads failed), then as its latest ack
left it (a loop command: ``accepted``, then its loop's ``applied`` or ``stopped``), and where the
act's ack launched jobs (`run_records.py::LaunchedEffect`), as those jobs read now. What a host
reads a started command by (MCP's task handle), and what every door's stop addresses
(`commands/cancelling.py::cancel_command`)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from promptpotter.application.jobs.interlock import producer_alive
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.domain.cycle_paths import WorkspaceDir
from promptpotter.domain.job import UNRESOLVED_HOP, Job
from promptpotter.domain.phases import STOP_REASON_INFO, StopCategory, StopOutcome, StopReason
from promptpotter.domain.run_records import CommandAckRecord, LaunchedEffect
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.infrastructure.runtime_flags import derive_run_state
from promptpotter.infrastructure.store.command_index import AskedCommand, FoundCommand, Unrecorded
from promptpotter.infrastructure.store.layout import cycle_dir_for
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.clock import epoch_seconds
from promptpotter.shared.errors import NotFoundError
from promptpotter.shared.identity import acting_principal

type CommandState = Literal["working", "input_required", "completed", "failed", "cancelled"]

# Past its run's end a job's state is how the run ended, and a pause the operator asked for is a
# cancel; a run held at a gate waits on its requester.
_ENDED: dict[StopOutcome, CommandState] = {
    StopOutcome.SUCCESS: "completed",
    StopOutcome.HALTED: "failed",
    StopOutcome.FAILED: "failed",
    StopOutcome.PAUSED: "failed",
}
_LAPSED: dict[str, CommandState] = {
    "refused": "failed",
    "withdrawn": "cancelled",
    "orphaned": "failed",
}
# One job held or working holds the command open; past that, the worst ending speaks for it.
_PRECEDENCE: tuple[CommandState, ...] = (
    "input_required",
    "working",
    "failed",
    "cancelled",
    "completed",
)
TERMINAL = frozenset[CommandState]({"completed", "failed", "cancelled"})


class CommandStatus(FrozenModel):
    """A command's state as it reads now, worded job by job where it launched any."""

    command_id: str
    kind: str
    status: CommandState
    said: str
    created_at: str
    last_updated_at: str


def _ended(what: str, ending: StopReason, *, stepped: bool) -> tuple[CommandState, str]:
    info = STOP_REASON_INFO[ending]
    if stepped:
        return "completed", f"{what}: stepped its rounds. {info.next_step}"
    status = _ENDED[info.outcome]
    if info.outcome is StopOutcome.PAUSED and info.category is StopCategory.OPERATOR:
        status = "cancelled"
    return status, f"{what}: {info.label}. {info.next_step}"


def job_status(job: Job, *, held: bool) -> tuple[CommandState, str]:
    """One job's state and its words; *held* is its cycle holding at a gate for an answer."""
    if job.ending is not None:
        return _ended(job.what, job.ending, stepped=job.stepped)
    if job.lapse is not None:
        return _LAPSED[job.lapse], f"{job.what}: {job.refusal or job.lapse}"
    if job.released:
        return "completed", f"{job.what}: returned"
    if held:
        return "input_required", f"{job.what}: holding at a gate for its answer"
    return "working", f"{job.what}: {job.stage}"


def _held(job: Job) -> bool:
    """Its cycle holds at a gate (`RunState.gate`) while the run waits on an answer."""
    if job.released or job.hop == UNRESOLVED_HOP:
        return False
    cycle_dir = cycle_dir_for(WorkspaceDir(Path(job.workspace)), job.hop)
    return derive_run_state(cycle_dir).gate is not None


def asked(stores: Stores, command_id: str) -> FoundCommand | Unrecorded:
    """*command_id* as the caller's principal issued it, or its filing and why no record of it
    is on its ledger; another principal's is not found, as an unknown one is."""
    found = stores.command_index.asked(command_id)
    if found is None or found.filed.issued_by != acting_principal(stores.identity).id:
        raise NotFoundError(f"no command {command_id!r} runs here", code="command_not_found")
    return found


def unrecorded_state(stores: Stores, gone: Unrecorded) -> tuple[CommandState, str]:
    """An id filed whose record is not on its ledger, read off facts: its owner deleted, its
    record raised, or its owner standing without it, landing while its filer's process lives and
    never landed once it ended. No act is ever recorded under it."""
    filed = gone.filed
    if gone.why == "deleted":
        return "failed", f"{filed.kind}: its {filed.owner.kind} was deleted, and its record with it"
    if gone.why == "absent" and producer_alive(stores.jobs_dir, filed.filer):
        return "working", f"{filed.kind}: filed, its record landing"
    cut = "the act raised" if gone.why == "voided" else "its filer ended"
    return "failed", f"{filed.kind}: filed {filed.at}, its record never landed: {cut} before it"


def settled(command: AskedCommand) -> CommandAckRecord | None:
    """The ack its state reads off: a refusal or a stop wherever it stands, else the latest (a
    loop command's ``applied`` past its ``accepted``); ``None`` while the act is in flight."""
    acks = command.acks
    ended = (ack for ack in acks if ack.status in ("rejected", "stopped"))
    return next(ended, acks[-1] if acks else None)


def launched_jobs(stores: Stores, jobs: JobRegistry, command: AskedCommand) -> list[Job] | None:
    """The jobs its first ack's effect launched (the act's own ack, which the launch rides), as
    their registry reads them now: none where it launched none, ``None`` where the registry no
    longer holds one."""
    ack = command.acks[0] if command.acks else None
    if ack is None or not isinstance(ack.effect, LaunchedEffect):
        return []
    principal = acting_principal(stores.identity).id
    ids = [launch.job_id for launch in ack.effect.launches]
    return jobs.launched_by(ids, principal_id=principal)


def command_status(stores: Stores, jobs: JobRegistry, command_id: str) -> CommandStatus:
    found = asked(stores, command_id)
    if isinstance(found, Unrecorded):
        state, said = unrecorded_state(stores, found)
        return CommandStatus(
            command_id=command_id,
            kind=found.filed.kind,
            status=state,
            said=said,
            created_at=found.filed.at,
            last_updated_at=found.filed.at,
        )
    command = found.asked
    record, ack, kind = command.record, settled(command), command.record.kind
    launched = launched_jobs(stores, jobs, command)
    stamps = [record.timestamp, *(a.timestamp for a in command.acks)]
    read: list[tuple[CommandState, str]]
    if ack is None and producer_alive(stores.jobs_dir, found.filed.filer):
        read = [("working", f"{kind}: in flight")]
    elif ack is None:
        read = [("failed", f"{kind}: its filer ended mid-act, before its ack")]
    elif ack.status == "rejected":
        read = [("failed", f"{kind}: {ack.detail}")]
    elif ack.status == "stopped":
        read = [("cancelled", f"{kind}: {ack.detail}")]
    elif launched is None:
        read = [("completed", f"{kind}: its jobs ended past what the registry keeps")]
    elif launched:
        read = [job_status(job, held=_held(job)) for job in launched]
        stamps += [job.released_at or job.started_at or job.created_at for job in launched]
    else:
        read = [("completed" if ack.status == "applied" else "working", f"{kind}: {ack.status}")]
    states = {state for state, _ in read}
    return CommandStatus(
        command_id=command_id,
        kind=kind,
        status=next(s for s in _PRECEDENCE if s in states),
        said="; ".join(said for _, said in read),
        created_at=record.timestamp,
        last_updated_at=max(stamps, key=lambda stamp: epoch_seconds(stamp) or 0.0),
    )
