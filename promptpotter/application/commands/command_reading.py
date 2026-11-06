"""One command as every door reads it: its status off the account's command log and, for a
launch, each job it launched (`Job.ending`, worded by `STOP_REASON_INFO`, or how its job lapsed).
The host's task, the REST status read and `cancel-command` read it here."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from promptpotter.application.jobs.interlock import producer_alive
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.domain.command_kinds import PAYLOAD_MODEL_FOR_KIND, LoopPayload
from promptpotter.domain.cycle_paths import CycleHop, WorkspaceDir
from promptpotter.domain.job import UNRESOLVED_HOP, Job
from promptpotter.domain.phases import STOP_REASON_INFO, StopCategory, StopOutcome, StopReason
from promptpotter.domain.run_records import CommandState, LaunchedEffect
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.infrastructure.runtime_flags import derive_run_state, run_mark
from promptpotter.infrastructure.store.command_log import CommandLog
from promptpotter.infrastructure.store.layout import cycle_dir_for
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import NotFoundError
from promptpotter.shared.identity import acting_principal

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


class CommandStatus(FrozenModel):
    """One command as every door reads it: where it stands, in the words of its task status,
    read afresh off the account's command log and, for a launch, each job it launched."""

    command_id: str
    kind: str
    # The address the dispatcher resolved and filed it on.
    subject: str
    status: TaskStatus
    said: str
    created_at: str
    last_updated_at: str


def owned_command(stores: Stores, command_id: str) -> CommandState:
    """*command_id* as the account's log holds it, filed by this principal; another's is absent."""
    state = CommandLog(stores.base_dir).state(command_id)
    principal = acting_principal(stores.identity).id
    if state is None or state.filed.issued_by.id != principal:
        raise NotFoundError(f"no command {command_id!r}")
    return state


def launched_jobs(jobs: JobRegistry, state: CommandState) -> list[Job] | None:
    """Each job a launch command launched, ``None`` for a command that launched none."""
    if not isinstance(effect := state.effect, LaunchedEffect):
        return None
    job_ids = [launch.job_id for launch in effect.launches]
    launched = jobs.launched_by(job_ids, principal_id=state.filed.issued_by.id)
    if launched is None:
        raise NotFoundError(f"no jobs {','.join(job_ids)!r}")
    return launched


_LOOP_KINDS = frozenset(k for k, m in PAYLOAD_MODEL_FOR_KIND.items() if issubclass(m, LoopPayload))
# What a loop command reads once the run it was accepted for ended without taking it.
_LOOP_ENDED = "loop ended without taking it"


def _loop_left(stores: Stores, state: CommandState) -> bool:
    """An accepted loop command its cycle's runs have moved past (`run_mark`), or whose cycle is gone."""
    if state.filed.kind not in _LOOP_KINDS:
        return False
    campaign_id, _, cycle_id = state.filed.subject.partition("/")
    hop = CycleHop(campaign_id=campaign_id, cycle_id=cycle_id)
    cycle_dir = cycle_dir_for(WorkspaceDir(stores.base_dir), hop)
    return not cycle_dir.is_dir() or run_mark(cycle_dir) != state.run_mark


def command_status(stores: Stores, jobs: JobRegistry, command_id: str) -> CommandStatus:
    """*command_id* as this principal filed it: refused as absent for anyone else's."""
    state = owned_command(stores, command_id)
    updated = state.at
    status: TaskStatus
    if (launched := launched_jobs(jobs, state)) is not None:
        read = [
            (*job_status(job, held=_held(job)), job.released_at or job.started_at or job.created_at)
            for job in launched
        ]
        status = next(s for s in _PRECEDENCE if s in {r[0] for r in read})
        said = "; ".join(r[1] for r in read)
        updated = max([updated, *(r[2] for r in read)])
    else:
        match state.status:
            case "filed" if producer_alive(stores.jobs_dir, state.filed.filer):
                status, said = "working", "running"
            case "filed":
                status, said = "failed", "ended: its filer is gone"
            case "accepted" if _loop_left(stores, state):
                status, said = "cancelled", _LOOP_ENDED
            case "accepted":
                status, said = "working", "accepted, standing for its loop"
            case "applied":
                status, said = "completed", "answered"
            case "cancelled":
                status, said = "cancelled", state.detail or "cancelled"
            case _:
                status, said = "failed", state.detail or state.status
    return CommandStatus(
        command_id=command_id,
        kind=state.filed.kind,
        subject=state.filed.subject,
        status=status,
        said=said,
        created_at=state.filed.timestamp,
        last_updated_at=updated,
    )
