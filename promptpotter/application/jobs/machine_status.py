from __future__ import annotations

from enum import StrEnum
from typing import NamedTuple

from pydantic import Field

from promptpotter.application.jobs.capacity import SlotBound, resolve_run_capacity
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.config.settings import settings
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.job import UNRESOLVED_HOP, EmbeddedRun, InnerRun, Job, SlotHolder
from promptpotter.domain.phases import QUEUE_STARTS_ITSELF
from promptpotter.domain.said import Said, Tone
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.identity.default_claim import is_host
from promptpotter.shared.identity import IdentityContext, acting_principal

_WHAT_DOC = (
    "The job in words (`Job.what`), worded once for every surface: a run's `campaign/cycle`, a "
    "mint's dataset, or a scoring pass's verb and the cycle or workspace it scores."
)


class MachineSlot(StrictModel):
    """One held slot, whoever holds it: a campaign run or a paid verb's scoring pass."""

    job_id: str
    what: str = Field(description=_WHAT_DOC)
    holder: SlotHolder = Field(
        description="WHAT holds the slot, by `kind`: a `campaign_launch` or a `scoring_pass` "
        "(a paid verb outside any run)."
    )
    cycle: CycleHop | None = Field(
        description="The cycle the job holds, which `/hierarchy` serves as a course entry with its run phase — "
        "`scoring` for a pass on it; null for a fresh mint and a pass scoring no cycle."
    )
    user: str = Field(description="user_id of the account whose job holds the slot")
    mine: bool = Field(
        description="The CALLER's account holds it — a delegate's job is its owner's. The test "
        "a surface listing the account's own jobs filters on."
    )
    started_at: str | None = Field(
        description="ISO time the holder started running; null while it is still starting"
    )

    @classmethod
    def of(cls, job: Job, *, account: str) -> MachineSlot:
        holder = job.holder
        assert not isinstance(holder, EmbeddedRun | InnerRun), "holds no slot"
        return cls(
            job_id=job.job_id,
            what=job.what,
            holder=holder,
            cycle=None if job.hop == UNRESOLVED_HOP else job.hop,
            user=job.user_id,
            mine=job.user_id == account,
            started_at=job.started_at,
        )


class MachineQueueEntry(StrictModel):
    """Always a launch: a scoring pass finding no slot is refused, never queued."""

    job_id: str
    what: str = Field(description=_WHAT_DOC)
    created_at: str
    position: int = Field(
        description="1-based place in the machine-wide drain order at the moment of this read. "
        "Least-served-first, so it moves as other accounts start and finish — it is where this "
        "launch stands now, not a countdown."
    )


class MachineRefusal(StrictModel):
    job_id: str
    what: str = Field(description=_WHAT_DOC)
    released_at: str = Field(description="When the launch was refused")
    reason: str = Field(description="The refusal, in the words the launch's own error carried")


class MachineNoticeKind(StrEnum):
    """What the machine's occupancy means for the caller's next launch: the `key` of its notice."""

    LANDING = "landing"
    QUEUED_NEXT = "queued_next"
    QUEUED = "queued"
    FULL_YOURS = "full_yours"
    FULL = "full"
    FULL_UNSEEN = "full_unseen"


class MachineNoticeInfo(NamedTuple):
    # `{name}` stands for the `machine_notice` fact of that name.
    title: str
    detail: str
    tone: Tone


_QUEUES = "a launch will queue"

MACHINE_NOTICE_INFO: dict[MachineNoticeKind, MachineNoticeInfo] = {
    MachineNoticeKind.LANDING: MachineNoticeInfo(
        "Landing new code", "a launch is refused until the box restarts on {commit}", "warn"
    ),
    MachineNoticeKind.QUEUED_NEXT: MachineNoticeInfo(
        "Queued — {what}, next in line", QUEUE_STARTS_ITSELF, "attention"
    ),
    MachineNoticeKind.QUEUED: MachineNoticeInfo(
        "Queued — {what}, position {position}", QUEUE_STARTS_ITSELF, "attention"
    ),
    MachineNoticeKind.FULL_YOURS: MachineNoticeInfo(
        "Machine full — your run {what} is running", _QUEUES, "warn"
    ),
    MachineNoticeKind.FULL: MachineNoticeInfo("Machine full — {what} is running", _QUEUES, "warn"),
    MachineNoticeKind.FULL_UNSEEN: MachineNoticeInfo(
        "Machine full — another run is running", _QUEUES, "warn"
    ),
}
assert MACHINE_NOTICE_INFO.keys() == set(MachineNoticeKind)


def _said(kind: MachineNoticeKind, **fields: object) -> Said:
    words = MACHINE_NOTICE_INFO[kind]
    return Said(
        key=kind.value,
        tone=words.tone,
        label=words.title.format(**fields),
        detail=words.detail.format(**fields),
    )


def machine_notice(
    *,
    landing: str | None,
    busy: bool,
    oldest: MachineSlot | None,
    queue: list[MachineQueueEntry],
) -> Said | None:
    """What the machine's occupancy means for the CALLER: a landing refusing every launch, their own
    launch waiting, or a full machine a launch would wait on."""
    if landing is not None:
        return _said(MachineNoticeKind.LANDING, commit=landing[:12])
    if queue:
        # Ordered and scoped to the caller: the first entry IS their place.
        first = queue[0]
        kind = MachineNoticeKind.QUEUED_NEXT if first.position == 1 else MachineNoticeKind.QUEUED
        return _said(kind, what=first.what, position=first.position)
    if not busy:
        return None
    # True of the caller's own slot too: their next launch queues behind it.
    if oldest is None:
        return _said(MachineNoticeKind.FULL_UNSEEN)
    kind = MachineNoticeKind.FULL_YOURS if oldest.mine else MachineNoticeKind.FULL
    return _said(kind, what=oldest.what)


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
    bound: SlotBound | None = Field(
        description="`throttle` while `capacity` is below `ceiling`; null where the machine "
        "admits its whole ceiling."
    )
    occupied: int = Field(
        description="Slots held machine-wide, whoever holds them — the occupancy figure, which "
        "`running` may list fewer of."
    )
    running: list[MachineSlot] = Field(
        description="The held slots this caller may see, oldest first — live campaigns and paid "
        "scoring passes alike. Every slot under `MACHINE_STATUS_SCOPE=all` or to the host; "
        "otherwise the caller's own, another account's being counted in `occupied` alone."
    )
    queued: int = Field(
        description="Launches waiting for a slot, machine-wide — a count, not a list. Who they "
        "belong to is deliberately not served; `queue` carries the caller's own."
    )
    busy: bool = Field(
        description="True iff `occupied` reaches `capacity` — no slot free for anyone, "
        "the caller included. A launch is then QUEUED rather than refused, so this reads as 'you "
        "will wait', not 'you cannot start'."
    )
    queue: list[MachineQueueEntry] = Field(
        default_factory=list,
        description="The CALLER's own waiting launches, oldest first — everything a client needs "
        "to say 'queued, position 3' and to offer a cancel. Own is the PRINCIPAL that launched "
        "them, the test `cancel-queued` applies. Other entries are counted in `queued` and "
        "never listed.",
    )
    refused: list[MachineRefusal] = Field(
        default_factory=list,
        description="The CALLER's own launches refused today, newest first. A launch that queued "
        "was answered before it was refused, so this is where its refusal is read.",
    )
    notice: Said | None = Field(
        description="A landing in progress, `busy` and the caller's place in `queue` — a fact about "
        "the NEXT launch, never about a run in view; null where a slot is free and nothing of "
        "theirs waits."
    )


def machine_status(jobs: JobRegistry, *, identity: IdentityContext) -> MachineStatusResponse:
    """Occupancy is not relative to who asks; which slots are NAMED is. Blocking: threadpool it."""
    principal_id = acting_principal(identity).id
    account = str(identity.user_id)
    held = [MachineSlot.of(job, account=account) for job in jobs.list_running()]
    sees_all = settings.MACHINE_STATUS_SCOPE == "all" or is_host(identity)
    running = [slot for slot in held if sees_all or slot.mine]
    capacity = resolve_run_capacity(len(held))
    # ONE ordering, the drain's own: numbering the caller's slice would read 1, 2, 3 to everyone.
    order = jobs.queue_order()
    busy = len(held) >= capacity
    queue = [
        MachineQueueEntry(
            job_id=job.job_id,
            what=job.what,
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
        bound=SlotBound.THROTTLE if capacity < ceiling else None,
        occupied=len(held),
        running=running,
        queued=len(order),
        busy=busy,
        queue=queue,
        # Named only where the caller may see the machine's oldest slot.
        notice=machine_notice(
            landing=jobs.landing(),
            busy=busy,
            oldest=held[0] if held and held[0] in running else None,
            queue=queue,
        ),
        refused=[
            MachineRefusal(
                job_id=job.job_id,
                what=job.what,
                released_at=job.released_at or job.created_at,
                reason=job.refusal,
            )
            # `list_launched_today` answers newest first.
            for job in jobs.list_launched_today()
            if job.principal_id == principal_id and job.lapse == "refused"
        ],
    )
