"""One command as every door reads it, by its id, off the account's log: where it stands in the
words of an MCP task's status, and for a launch each job it launched (`Job.ending`, worded by
`STOP_REASON_INFO`, or how its job lapsed, as its release stamped it). The host's task, the
`command` read and the `cancel` of a launch read it here."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from promptpotter.application.jobs.launcher.admission import PassStoppedError
from promptpotter.domain.command_events import CommandAckRecord, CommandRecord
from promptpotter.domain.job import UNRESOLVED_HOP, Job
from promptpotter.domain.phases import STOP_REASON_INFO, StopCategory, StopOutcome, StopReason
from promptpotter.domain.run_records import LaunchedEffect
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.shared.errors import NotFoundError
from promptpotter.shared.identity import acting_principal

if TYPE_CHECKING:
    from promptpotter.application.jobs.registry import JobRegistry
    from promptpotter.infrastructure.store.stores import Stores

type TaskStatus = Literal["working", "input_required", "completed", "failed", "cancelled"]

# Past its run's end a task's status is how the run ended, and a pause the operator asked for is a
# cancel; a run held at a gate waits on its requester.
_ENDED: dict[StopOutcome, TaskStatus] = {
    StopOutcome.SUCCESS: "completed",
    StopOutcome.HALTED: "failed",
    StopOutcome.FAILED: "failed",
    StopOutcome.PAUSED: "failed",
}
_LAPSED: dict[str, TaskStatus] = {
    "refused": "failed",
    "withdrawn": "cancelled",
    "orphaned": "failed",
}
# One job held or working holds the task open; past that, the worst ending speaks for it.
_PRECEDENCE: tuple[TaskStatus, ...] = (
    "input_required",
    "working",
    "failed",
    "cancelled",
    "completed",
)
TERMINAL = frozenset[TaskStatus]({"completed", "failed", "cancelled"})


def _ended(what: str, ending: StopReason, *, stepped: bool) -> tuple[TaskStatus, str]:
    info = STOP_REASON_INFO[ending]
    if stepped:
        return "completed", f"{what}: stepped its rounds. {info.next_step}"
    status = _ENDED[info.outcome]
    if info.outcome is StopOutcome.PAUSED and info.category is StopCategory.OPERATOR:
        status = "cancelled"
    return status, f"{what}: {info.label}. {info.next_step}"


def job_status(job: Job, *, held: bool) -> tuple[TaskStatus, str]:
    """One job's task status and its words; *held* is its cycle holding at a gate for an answer."""
    if job.ending is not None:
        return _ended(job.what, job.ending, stepped=job.stepped)
    if job.lapse is not None:
        refused = "" if job.refusal is None else job.refusal.message
        return _LAPSED[job.lapse], f"{job.what}: {refused or job.lapse}"
    if job.released:
        return "completed", f"{job.what}: returned"
    if held:
        return "input_required", f"{job.what}: holding at a gate for its answer"
    return "working", f"{job.what}: {job.stage}"


def _held(stores: Stores, job: Job) -> bool:
    """Its cycle holds at a gate (`RunState.gate`) while the run waits on an answer."""
    if job.released or job.hop == UNRESOLVED_HOP:
        return False
    return stores.run_state(job.hop).gate is not None


class CommandReading(FrozenModel):
    """A command read afresh off the account's log: where it stands, in the words of its task
    status, at the address the dispatcher resolved and filed it on."""

    command_id: str
    kind: str
    subject: str
    status: TaskStatus
    said: str
    created_at: str
    last_updated_at: str


class OwnedCommand(FrozenModel):
    """A command this principal filed: its filing and the status event that last moved it."""

    filed: CommandRecord
    end: CommandAckRecord | None


def owned_command(stores: Stores, command_id: str) -> OwnedCommand:
    """*command_id* as this principal filed it: refused as absent for anyone else's."""
    book = stores.commands.book()
    filed = book.filing(command_id)
    if filed is None or filed.issued_by.id != acting_principal(stores.identity).id:
        raise NotFoundError(f"no command {command_id!r}")
    return OwnedCommand(filed=filed, end=book.end(command_id))


def launched_jobs(jobs: JobRegistry, owned: OwnedCommand) -> list[Job] | None:
    """Each job a launch command launched; ``None`` for a command that launched none."""
    if owned.end is None or not isinstance(effect := owned.end.effect, LaunchedEffect):
        return None
    job_ids = [launch.job_id for launch in effect.launches]
    launched = jobs.launched_by(job_ids, principal_id=owned.filed.issued_by.id)
    if launched is None:
        raise NotFoundError(f"no jobs {','.join(job_ids)!r}")
    return launched


def read_command(stores: Stores, jobs: JobRegistry, command_id: str) -> CommandReading:
    owned = owned_command(stores, command_id)
    filed, end = owned.filed, owned.end
    updated = filed.timestamp if end is None else end.timestamp
    status: TaskStatus
    if (launched := launched_jobs(jobs, owned)) is not None:
        read = [
            (
                *job_status(job, held=_held(stores, job)),
                job.released_at or job.started_at or job.created_at,
            )
            for job in launched
        ]
        status = next(s for s in _PRECEDENCE if s in {r[0] for r in read})
        said = "; ".join(r[1] for r in read)
        updated = max([updated, *(r[2] for r in read)])
    elif end is None:
        status, said = "working", f"{filed.kind}: running"
    else:
        match end.status:
            case "accepted":
                status, said = "working", f"{filed.kind}: accepted, standing for its loop"
            case "applied":
                status, said = "completed", f"{filed.kind}: answered"
            case "voided":
                status, said = "cancelled", f"{filed.kind}: {end.detail or 'voided'}"
            case "cancelled":
                status, said = "cancelled", f"{filed.kind}: cancelled"
            case "rejected" if end.refusal is not None:
                refusal = end.refusal.refusal
                stopped = refusal.code == PassStoppedError.code
                status = "cancelled" if stopped else "failed"
                said = f"{filed.kind}: {refusal.message}"
                if not stopped:
                    said = f"{filed.kind}: {refusal.code}: {refusal.message}"
            case "rejected":
                status, said = "failed", f"{filed.kind}: {end.detail}"
    return CommandReading(
        command_id=command_id,
        kind=filed.kind,
        subject=filed.subject,
        status=status,
        said=said,
        created_at=filed.timestamp,
        last_updated_at=updated,
    )
