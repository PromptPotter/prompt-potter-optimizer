from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import Field

from promptpotter.config.settings import settings
from promptpotter.domain.phases import QUEUE_STARTS_ITSELF
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.llm.send_pacing import throttle_stall_seconds

if TYPE_CHECKING:
    from promptpotter.application.jobs.registry import JobRegistry

logger = logging.getLogger(__name__)

# "One task waited a whole window". Not a setting: the operator's knob is MACHINE_RUN_CAPACITY.
_STALL_BUDGET_S = 60.0


def resolve_run_capacity(running: int) -> int:
    ceiling = settings.MACHINE_RUN_CAPACITY
    stalled = throttle_stall_seconds()
    if stalled <= _STALL_BUDGET_S:
        return ceiling
    # Never below one, or a stalled quiet box refuses its own last slot and admits nothing again.
    held = max(1, min(ceiling, running))
    if held < ceiling:
        logger.info(
            "throttle back-pressure: %.0fs stall in the last window holds capacity at %d (ceiling %d)",
            stalled,
            held,
            ceiling,
        )
    return held


def throttle_hold(capacity: int, ceiling: int) -> str | None:
    if capacity >= ceiling:
        return None
    return f"admitting {capacity} of {ceiling} while the provider throttle is saturated"


class MachineHolder(StrictModel):
    user: str = Field(description="user_id of the operator whose run owns the slot")
    campaign_id: str
    cycle_id: str
    started_at: str | None = Field(
        default=None, description="ISO start time of the holding run; null if still pending"
    )


class MachineQueueEntry(StrictModel):
    job_id: str
    dataset_name: str
    created_at: str
    position: int = Field(
        description="1-based place in the machine-wide drain order at the moment of this read. "
        "Least-served-first, so it moves as other accounts start and finish — it is where this "
        "launch stands now, not a countdown."
    )


class MachineRefusal(StrictModel):
    job_id: str
    dataset_name: str
    campaign_id: str = Field(
        description="Empty where the launch was a mint refused before a campaign existed"
    )
    cycle_id: str
    released_at: str = Field(description="When the launch was refused")
    reason: str = Field(description="The refusal, in the words the launch's own error carried")


class MachineNotice(StrictModel):
    """What the machine's occupancy means for the CALLER, in words: their own launch waiting, or
    a full machine a launch would wait on. Built by :meth:`of` alone."""

    title: str
    detail: str

    @classmethod
    def of(
        cls, *, busy: bool, holder: MachineHolder | None, queue: list[MachineQueueEntry]
    ) -> MachineNotice | None:
        if queue:
            # Ordered and scoped to the caller: the first entry IS their place.
            position = queue[0].position
            return cls(
                title="Queued — next in line" if position == 1 else f"Queued — position {position}",
                detail=QUEUE_STARTS_ITSELF,
            )
        if not busy:
            return None
        since = holder.started_at if holder is not None else None
        return cls(
            title=f"Machine full — {holder.user if holder else 'another run'} is running",
            detail=f"a launch will queue · oldest run since {since}"
            if since
            else "a launch will queue",
        )


class MachineStatusResponse(StrictModel):
    capacity: int = Field(
        description="Campaigns the machine admits right now. Resolved per read against the same "
        "rule a launch is admitted on, and lowered from the operator's ceiling while the shared "
        "provider throttle is saturated."
    )
    ceiling: int = Field(
        description="The operator's `MACHINE_RUN_CAPACITY` — set in the server environment and "
        "writable nowhere else. `capacity` never exceeds it, and neither may an account's limit."
    )
    held_back: str | None = Field(
        description="`capacity` below `ceiling`, worded once for the terminal and the usage tab; "
        "null where the machine admits its whole ceiling."
    )
    running: int = Field(description="Campaigns currently live on the machine.")
    queued: int = Field(
        description="Launches waiting for a slot, machine-wide — an occupancy figure like "
        "`running`, not a list. Who they belong to is deliberately not served; `queue` carries "
        "the caller's own."
    )
    busy: bool = Field(
        description="True iff `running >= capacity` — no slot free for anyone, the caller "
        "included. A launch is then QUEUED rather than refused, so this reads as 'you will wait', "
        "not 'you cannot start'."
    )
    holder: MachineHolder | None = Field(
        default=None,
        description="The oldest live run, whoever owns it; null when nothing is running.",
    )
    queue: list[MachineQueueEntry] = Field(
        default_factory=list,
        description="The CALLER's own waiting launches, oldest first — everything a client needs "
        "to say 'queued, position 3' and to offer a cancel. Own is the PRINCIPAL that launched "
        "them, the test `cancel-queued-run` applies. Other entries are counted in `queued` and "
        "never listed.",
    )
    refused: list[MachineRefusal] = Field(
        default_factory=list,
        description="The CALLER's own launches refused today, newest first. A launch that queued "
        "was answered before it was refused, so this is where its refusal is read.",
    )
    notice: MachineNotice | None = Field(
        description="`busy` and the caller's place in `queue`, worded once for the terminal and "
        "the alert bar; null where a slot is free and nothing of theirs waits."
    )


def machine_status(jobs: JobRegistry, *, principal_id: str) -> MachineStatusResponse:
    """The caller's own run counts: occupancy is not relative to who asks. Blocking: threadpool it."""
    live = len(jobs.list_running())
    capacity = resolve_run_capacity(live)
    oldest = jobs.holder()
    # ONE ordering, the drain's own: numbering the caller's slice would read 1, 2, 3 to everyone.
    order = jobs.queue_order()
    busy = live >= capacity
    holder = (
        None
        if oldest is None
        else MachineHolder(
            user=oldest.user_id,
            campaign_id=oldest.campaign_id,
            cycle_id=oldest.cycle_id,
            started_at=oldest.started_at,
        )
    )
    queue = [
        MachineQueueEntry(
            job_id=job.job_id,
            dataset_name=job.dataset_name,
            created_at=job.created_at,
            position=index,
        )
        for index, job in enumerate(order, 1)
        if job.principal_id == principal_id
    ]
    ceiling = settings.MACHINE_RUN_CAPACITY
    return MachineStatusResponse(
        capacity=capacity,
        ceiling=ceiling,
        held_back=throttle_hold(capacity, ceiling),
        running=live,
        queued=len(order),
        busy=busy,
        holder=holder,
        queue=queue,
        notice=MachineNotice.of(busy=busy, holder=holder, queue=queue),
        refused=[
            MachineRefusal(
                job_id=job.job_id,
                dataset_name=job.dataset_name,
                campaign_id=job.campaign_id,
                cycle_id=job.cycle_id,
                released_at=job.released_at or job.created_at,
                reason=job.refusal,
            )
            # `list_created_today` answers newest first.
            for job in jobs.list_created_today()
            if job.principal_id == principal_id and job.refusal
        ],
    )


__all__ = [
    "MachineHolder",
    "MachineNotice",
    "MachineQueueEntry",
    "MachineRefusal",
    "MachineStatusResponse",
    "machine_status",
    "resolve_run_capacity",
    "throttle_hold",
]
