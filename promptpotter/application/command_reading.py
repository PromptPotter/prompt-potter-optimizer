"""One command as every door reads it, by its id, off the account's log: where it stands in the
words of an MCP task's status, and for a launch each job it launched (its cycle's stop since the
launch, worded by `STOP_REASON_INFO`, or how its job left before any run). The host's task, the
`command` read and the `cancel` of a launch read it here."""

from __future__ import annotations

from typing import Literal, NamedTuple

from promptpotter.application.jobs.launcher.admission import PassStoppedError
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.domain.command_effects import LaunchedEffect, LaunchedJob
from promptpotter.domain.command_events import CommandAckRecord, CommandRecord, CommandStatus
from promptpotter.domain.command_kinds import CommandKind, acts_filed, filable
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.job import UNRESOLVED_HOP, CampaignLaunch, Job
from promptpotter.domain.phases import (
    RUN_PHASE_INFO,
    STOP_REASON_INFO,
    PauseCause,
    RunPhase,
    StopCategory,
    StopOutcome,
    StopReading,
    StopReason,
)
from promptpotter.domain.refusal import Refusal
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import NextAct, NotFoundError

type TaskStatus = Literal["working", "input_required", "completed", "failed", "cancelled"]

# Past its run's end a task's status is how the run ended, and a pause the operator asked for is a
# cancel; a run held at a gate waits on its requester.
_ENDED: dict[StopOutcome, TaskStatus] = {
    StopOutcome.SUCCESS: "completed",
    StopOutcome.HALTED: "failed",
    StopOutcome.FAILED: "failed",
    StopOutcome.PAUSED: "failed",
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
# What a held task asks, the hold's own acts: one source with the run control's.
_HOLDING = RUN_PHASE_INFO[RunPhase.GATE].refusal
assert _HOLDING is not None, "a gate's hold names the act that answers it"
_HOLDING_ACTS = _HOLDING.next_acts


class JobStatus(NamedTuple):
    status: TaskStatus
    said: str
    # The acts past what it says: its stop's, or the refusal it lapsed on.
    acts: tuple[NextAct, ...] = ()


def _ended(what: str, ending: StopReason, *, stepped: bool) -> JobStatus:
    info = STOP_REASON_INFO[ending]
    if stepped:
        return JobStatus(
            "completed", f"{what}: stepped its rounds. {info.next_step}", info.next_acts
        )
    status = _ENDED[info.outcome]
    if info.outcome is StopOutcome.PAUSED and info.category is StopCategory.OPERATOR:
        status = "cancelled"
    return JobStatus(status, f"{what}: {info.label}. {info.next_step}", info.next_acts)


def job_status(
    job: Job, *, held: bool, stop: StopReading | None, refused: Refusal | None
) -> JobStatus:
    """One job's task status and its words; *held* is its cycle holding at a gate for an answer,
    *stop* its cycle's stop since the job was launched, *refused* what its release refused it
    with. A released job neither names never ran: withdrawn from the queue, refused before its
    admission, a pass that returned, or a run whose producer died before it held the cycle."""
    if not job.released:
        if held:
            return JobStatus(
                "input_required", f"{job.what}: holding at a gate for its answer", _HOLDING_ACTS
            )
        return JobStatus("working", f"{job.what}: {job.stage}")
    if refused is not None:
        return JobStatus("failed", f"{job.what}: {refused.message}", refused.next_acts)
    if stop is not None and stop.stop_reason is not None:
        return _ended(job.what, stop.stop_reason, stepped=stop.cause is PauseCause.STEP)
    if job.stage is RunPhase.QUEUED:
        return JobStatus("cancelled", f"{job.what}: withdrawn from the queue")
    if not job.admitted:
        return JobStatus("failed", f"{job.what}: refused before its admission")
    if isinstance(job.holder, CampaignLaunch):
        return JobStatus("failed", f"{job.what}: its producer died before its run held the cycle")
    return JobStatus("completed", f"{job.what}: returned")


class ArmsHold(FrozenModel):
    """A launched job's cycle holding at its arms gate, the hold `submit-arms` answers: named by
    its declaration's stamp, since each round holds afresh, with the drafts it offers to keep."""

    hop: CycleHop
    held_at: str
    drafts: int


class _OnCycle(NamedTuple):
    hold: ArmsHold | None = None
    stop: StopReading | None = None


def _on_cycle(stores: Stores, job: Job) -> _OnCycle:
    """Its cycle's hold, while the run waits on an answer; its stop, once it ended there."""
    if job.hop == UNRESOLVED_HOP:
        return _OnCycle()
    state = stores.run_state(job.hop)
    if job.released:
        stop = state.stop
        return _OnCycle(stop=stop if stop is not None and stop.at >= job.created_at else None)
    if state.run_phase is not RunPhase.GATE:
        return _OnCycle()
    return _OnCycle(hold=ArmsHold(hop=job.hop, held_at=state.held_at, drafts=state.drafts))


class CommandReading(FrozenModel):
    """A command read afresh off the account's log: where it stands, in the words of its task
    status, at the address the dispatcher resolved and filed it on; a launch's jobs, each at the
    cycle it holds now (``None`` for a command launching none)."""

    command_id: str
    kind: str
    subject: str
    # The API token that filed it; ``None`` for a session or the terminal.
    token_id: str | None
    status: TaskStatus
    said: str
    # The acts past what it says, as the stop or refusal it ended on names them.
    next_acts: tuple[NextAct, ...]
    created_at: str
    last_updated_at: str
    launches: list[LaunchedJob] | None
    # Each launched cycle holding for its answer, in launch order.
    holds: list[ArmsHold]


class OwnedCommand(FrozenModel):
    """A command on the account's log: its filing and the status event that last moved it."""

    filed: CommandRecord
    end: CommandAckRecord | None
    # `CommandBook.status`: a filing with no end reads ``cancelled`` once its filer is gone.
    status: CommandStatus


def owned_command(stores: Stores, command_id: str) -> OwnedCommand:
    """*command_id* as the account's log holds it, whoever in the account filed it: a read is gated
    by tenancy, as a reopen of the draft it was filed on is, so whoever may reopen a draft follows
    the Start on it (`draft_build.py::HeldStart`); another account's log holds none of it."""
    book = stores.commands.book()
    filed, status = book.filing(command_id), book.status(command_id)
    if filed is None or status is None:
        raise NotFoundError(f"no command {command_id!r}")
    return OwnedCommand(filed=filed, end=book.end(command_id), status=status)


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
    acts: tuple[NextAct, ...] = ()
    holds: list[ArmsHold] = []
    # The acts and holds its reader may answer, by the dispatcher's own rule.
    kinds = filable(stores.identity)
    answers = CommandKind.SUBMIT_ARMS in kinds
    launched = launched_jobs(jobs, owned)
    if launched is not None:
        on = {job.job_id: _on_cycle(stores, job) for job in launched}
        holds = [c.hold for c in on.values() if c.hold is not None and answers]
        read = [
            job_status(
                job,
                held=on[job.job_id].hold is not None,
                stop=on[job.job_id].stop,
                refused=jobs.refusal(job.job_id) if job.released else None,
            )
            for job in launched
        ]
        status = next(s for s in _PRECEDENCE if s in {r.status for r in read})
        said = "; ".join(r.said for r in read)
        acts = tuple(dict.fromkeys(act for r in read for act in r.acts))
        updated = max([updated, *(j.released_at or j.started_at or j.created_at for j in launched)])
    elif end is None and owned.status == "cancelled":
        status, said = "cancelled", f"{filed.kind}: its filer died before it ended"
    elif end is None:
        status, said = "working", f"{filed.kind}: running"
    else:
        match end.status:
            case "accepted" if owned.status == "voided":
                status, said = "cancelled", f"{filed.kind}: its run released the cycle untaken"
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
                acts = refusal.next_acts
            case "rejected":
                status, said = "failed", f"{filed.kind}: {end.detail}"
    return CommandReading(
        command_id=command_id,
        kind=filed.kind,
        subject=filed.subject,
        token_id=filed.token_id,
        status=status,
        said=said,
        next_acts=acts_filed(acts, kinds),
        created_at=filed.timestamp,
        last_updated_at=updated,
        launches=None if launched is None else [LaunchedJob.of(job) for job in launched],
        holds=holds,
    )
