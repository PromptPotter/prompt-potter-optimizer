from __future__ import annotations

import asyncio
import logging
import secrets
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from json import JSONDecodeError
from pathlib import Path
from typing import Annotated, ClassVar, Literal, assert_never, get_args

from pydantic import ConfigDict, Field, TypeAdapter

from promptpotter.application.jobs.capacity import resolve_run_capacity
from promptpotter.application.jobs.interlock import (
    AdmissionGate,
    producer_alive,
    this_producer,
    this_producer_lock,
)
from promptpotter.config.settings import settings
from promptpotter.domain.cycle_paths import CycleHop, WorkspaceDir
from promptpotter.domain.phases import LaunchStage, RunPhase
from promptpotter.domain.spend import Budget
from promptpotter.infrastructure.store.io import read_json, unlink_robust, write_json
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import ConflictError

logger = logging.getLogger(__name__)

# Each ask takes the machine-wide admission lock.
_QUEUE_POLL_S = 2.0

# The slot is held before the mint resolves the cycle it will name; `update_target` fills it in.
UNRESOLVED_HOP = CycleHop(campaign_id="", cycle_id="")

JobStage = LaunchStage | Literal[RunPhase.RUNNING]
_JOB_STAGES: frozenset[RunPhase] = frozenset(
    stage for arm in get_args(JobStage) for stage in get_args(arm)
)
SLOT_STAGES: frozenset[RunPhase] = frozenset({RunPhase.STARTING, RunPhase.RUNNING})
assert SLOT_STAGES < _JOB_STAGES, "a slot stage no job can be in"


@dataclass(frozen=True)
class CampaignLaunch:
    """A campaign run: its hop is UNRESOLVED until a fresh mint names the cycle."""

    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    dataset_name: str
    # The check-in a Start mints from; null for every other launch. A queued one serves its draft queued.
    draft_id: str | None = None
    kind: Literal["campaign_launch"] = "campaign_launch"


# Every paid verb that scores outside a run; each is its own rate bucket.
ScoringVerb = Literal["bench", "noise-floor", "probe-reasoning", "seed-screen", "verify"]


@dataclass(frozen=True)
class ScoringPass:
    """A paid verb scoring outside any run: its hop is the cycle it scores, or none at all."""

    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    verb: ScoringVerb
    kind: Literal["scoring_pass"] = "scoring_pass"


@dataclass(frozen=True)
class EmbeddedRun:
    """A host program's run: a share and a reservation, never a slot, since the host schedules itself."""

    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    dataset_name: str
    kind: Literal["embedded_run"] = "embedded_run"


@dataclass(frozen=True)
class InnerRun:
    """An L4 inner campaign: a share of what its enclosing run's book has left, and nothing more."""

    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    dataset_name: str
    kind: Literal["inner_run"] = "inner_run"


SlotHolder = Annotated[CampaignLaunch | ScoringPass, Field(discriminator="kind")]
JobHolder = Annotated[
    CampaignLaunch | ScoringPass | EmbeddedRun | InnerRun, Field(discriminator="kind")
]


@dataclass(frozen=False)
class Job:
    """Outlives its release to the end of its UTC day: the daily launch quota counts it."""

    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    job_id: str
    user_id: str
    # WHO launched it, never the account: a delegate shares its owner's `user_id`.
    principal_id: str
    # The store its hop names a cycle in: an L4 sandbox repeats content-addressed campaign ids.
    workspace: str
    campaign_id: str
    cycle_id: str
    # WHAT holds (or waits for) the slot, or the spend alone.
    holder: JobHolder
    stage: JobStage
    created_at: str
    started_at: str | None
    # A release is its own act, never a fourth stage: `stage` stays where the launch got to.
    released_at: str | None
    # What the account holds back for it, which its run's book reads live; its cycle's share of the
    # campaign's ceiling is the cycle's (`CycleShareRecord`).
    reserve: Budget = field(default_factory=Budget)
    # Unset = never granted a run, so its failure is a refusal.
    admitted_at: str | None = None
    producer_id: str = ""
    # Non-empty IS the refused launch: set only where it was never admitted.
    refusal: str = ""

    @property
    def what(self) -> str:
        """What holds the slot, in words: every surface naming a job says it this way."""
        resolved = self.hop != UNRESOLVED_HOP
        where = f"{self.campaign_id}/{self.cycle_id}"
        match self.holder:
            case CampaignLaunch(dataset_name=dataset):
                return where if resolved else f"a new campaign on {dataset}"
            case ScoringPass(verb=verb):
                return f"{verb} on {where if resolved else 'the workspace'}"
            case EmbeddedRun(dataset_name=dataset):
                return f"an embedded run on {where if resolved else dataset}"
            case InnerRun(dataset_name=dataset):
                return f"an inner run on {where if resolved else dataset}"
            case _:
                assert_never(self.holder)

    @property
    def hop(self) -> CycleHop:
        return CycleHop(campaign_id=self.campaign_id, cycle_id=self.cycle_id)

    def holds(self, workspace: WorkspaceDir, hop: CycleHop) -> bool:
        return self.workspace == str(workspace) and self.hop == hop

    def on_campaign(self, workspace: WorkspaceDir, campaign_id: str) -> bool:
        return self.workspace == str(workspace) and self.campaign_id == campaign_id

    @property
    def admitted(self) -> bool:
        """Its reservation is stamped and its cycle's share landed: before this, a failure is a refusal."""
        return self.admitted_at is not None

    @property
    def released(self) -> bool:
        return self.released_at is not None

    def launched_on(self, day: str) -> bool:
        return self.created_at.startswith(day)

    @property
    def queued(self) -> bool:
        return not self.released and self.stage is RunPhase.QUEUED

    @property
    def holds_spend(self) -> bool:
        """Its reservation and its share bind the account and the campaign while this holds."""
        return not self.released and self.stage in SLOT_STAGES

    @property
    def holds_slot(self) -> bool:
        return self.holds_spend and not isinstance(self.holder, EmbeddedRun | InnerRun)


_JOB = TypeAdapter(Job)


class JobRegistry:
    """No process reaps another's live job without proving its producer gone."""

    @classmethod
    def attach(cls, jobs_dir: Path) -> JobRegistry:
        return cls(jobs_dir, capacity=resolve_run_capacity)

    def __init__(self, jobs_dir: Path, *, capacity: Callable[[int], int]) -> None:
        self._dir = jobs_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        # Reentrant: `request_slot` → `list_running` → `_reap_if_orphaned` → `release` retake it.
        self._lock = threading.RLock()
        # Its cross-process peer, nesting the same way.
        self._gate = AdmissionGate(jobs_dir)
        self._capacity = capacity
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def create(
        self,
        *,
        user_id: str,
        principal_id: str,
        workspace: WorkspaceDir,
        hop: CycleHop,
        holder: JobHolder,
        stage: JobStage = RunPhase.STARTING,
    ) -> Job:
        job_id = secrets.token_urlsafe(12)
        now = utcnow_iso()
        job = Job(
            job_id=job_id,
            user_id=user_id,
            principal_id=principal_id,
            workspace=str(workspace),
            campaign_id=hop.campaign_id,
            cycle_id=hop.cycle_id,
            holder=holder,
            stage=stage,
            created_at=now,
            started_at=None,
            released_at=None,
            # Not at construction: the token takes a process-lifetime lock, and a reader is no producer.
            producer_id=this_producer(self._dir),
        )
        self._persist(job)
        return job

    @contextmanager
    def admission_gate(self) -> Iterator[None]:
        """Every ceiling read belongs inside: outside, two admissions read one free slot and take it."""
        with self._lock, self._gate.held():
            yield

    def request_slot(
        self,
        *,
        user_id: str,
        principal_id: str,
        workspace: WorkspaceDir,
        holder: SlotHolder,
        hop: CycleHop = UNRESOLVED_HOP,
    ) -> Job:
        """Count, capacity and write stay under the gate with no ``await`` between them."""
        with self.admission_gate():
            running = self.list_running()
            live = len(running)
            stage: JobStage = RunPhase.STARTING if live < self._capacity(live) else RunPhase.QUEUED
            return self.create(
                user_id=user_id,
                principal_id=principal_id,
                workspace=workspace,
                hop=hop,
                holder=holder,
                stage=stage,
            )

    async def await_claim(self, job_id: str) -> None:
        """A poll, not a signal: the slot frees in another process, and only the jobs dir is shared."""
        deadline = time.monotonic() + settings.QUEUE_MAX_WAIT_S
        while not await asyncio.to_thread(self.claim_next, job_id):
            job = self.get(job_id)
            if job is None or job.released:
                raise ConflictError(
                    "This launch was withdrawn from the queue. Nothing ran and nothing was spent.",
                    code="queue_withdrawn",
                )
            if time.monotonic() >= deadline:
                raise ConflictError(
                    f"This launch waited {settings.QUEUE_MAX_WAIT_S / 3600:.0f}h for a free slot "
                    f"and was withdrawn. Nothing ran and nothing was spent; start it again when "
                    f"the machine is quieter.",
                    code="queue_expired",
                )
            await asyncio.sleep(_QUEUE_POLL_S)

    def queue_order(self) -> list[Job]:
        """Least-served first: plain FIFO lets one account's burst push everyone else behind it."""
        held = Counter(j.user_id for j in self.list_running())
        return sorted(self.list_queued(), key=lambda j: (held[j.user_id], j.created_at, j.job_id))

    def claim_next(self, job_id: str) -> bool:
        """Only the head of :meth:`queue_order`: a waiter that skipped past it would starve it."""
        with self.admission_gate():
            running = self.list_running()
            live = len(running)
            if live >= self._capacity(live):
                return False
            first = next(iter(self.queue_order()), None)
            if first is None or first.job_id != job_id:
                return False
            first.stage = RunPhase.STARTING
            self._persist(first)
            return True

    def cancel_queued(self, job_id: str, *, principal_id: str) -> bool:
        with self.admission_gate():
            job = self.get(job_id)
            if job is None or not job.queued or job.principal_id != principal_id:
                return False
            self.release(job_id)
            return True

    def mark_admitted(self, job_id: str, reserve: Budget) -> None:
        job = self.get(job_id)
        if job is None:
            return
        job.admitted_at = utcnow_iso()
        job.reserve = reserve
        self._persist(job)

    def set_reserve(self, job_id: str, reserve: Budget) -> None:
        job = self.get(job_id)
        if job is None:
            return
        job.reserve = reserve
        self._persist(job)

    def update_target(self, job_id: str, *, hop: CycleHop) -> None:
        job = self.get(job_id)
        if job is None:
            return
        job.campaign_id = hop.campaign_id
        job.cycle_id = hop.cycle_id
        self._persist(job)

    @property
    def jobs_dir(self) -> Path:
        return self._dir

    def job_file(self, job_id: str) -> Path:
        """What a live spend book reads its reserve off: the job is the reserve's one store."""
        return self._path(job_id)

    def claimant_lock(self) -> Path:
        return this_producer_lock(self._dir)

    def adopt(self, job_id: str) -> bool:
        with self.admission_gate():
            job = self.get(job_id)
            if job is None or job.released:
                return False
            job.producer_id = this_producer(self._dir)
            self._persist(job)
            return True

    def attach_task(self, job_id: str, task: asyncio.Task[None]) -> None:
        with self._lock:
            self._tasks[job_id] = task

    def mark_started(self, job_id: str) -> None:
        job = self.get(job_id)
        if job is None:
            return
        job.stage = RunPhase.RUNNING
        job.started_at = utcnow_iso()
        self._persist(job)

    def release(self, job_id: str, *, refusal: str = "") -> None:
        job = self.get(job_id)
        if job is None:
            return
        job.released_at = utcnow_iso()
        job.refusal = refusal
        self._persist(job)
        with self._lock:
            self._tasks.pop(job_id, None)

    def get(self, job_id: str) -> Job | None:
        path = self._path(job_id)
        if not path.is_file():
            return None
        try:
            raw = read_json(path)
        except (OSError, JSONDecodeError):
            logger.warning("job %s file unreadable", job_id)
            return None
        return _JOB.validate_python(raw)

    def list_all(self, *, user_id: str | None = None) -> list[Job]:
        """A released job launched before today is deleted here: every reader past a release counts today's."""
        today = utcnow_iso()[:10]
        out: list[Job] = []
        for path in self._dir.glob("*.json"):
            try:
                raw = read_json(path)
            except (OSError, JSONDecodeError):
                continue
            job = _JOB.validate_python(raw)
            if job.released and not job.launched_on(today):
                unlink_robust(path)
                continue
            if user_id is not None and job.user_id != user_id:
                continue
            out.append(job)
        out.sort(key=lambda j: j.created_at, reverse=True)
        return out

    def _reap_if_orphaned(self, job: Job) -> Job:
        """``_tasks`` holds only THIS process's jobs: "no task" is not "dead" — ask the producer lock."""
        if job.released:
            return job
        with self._lock:
            task = self._tasks.get(job.job_id)
        if task is not None and not task.done():
            return job
        # Adopted: ask the lock — on Windows the spawned process is the venv launcher, not the run.
        adopted = task is not None and job.producer_id != this_producer(self._dir)
        if (task is None or adopted) and producer_alive(self._dir, job.producer_id):
            return job
        logger.warning(
            "job %s claims %s but its producer is gone — releasing", job.job_id, job.stage.value
        )
        self.release(job.job_id)
        return self.get(job.job_id) or job

    def list_running(self, *, user_id: str | None = None) -> list[Job]:
        """Oldest first, and a reconciling READ: a job whose producer is gone is released here."""
        out = self._reconciled(lambda j: j.holds_slot, user_id=user_id)
        out.sort(key=lambda j: (j.created_at, j.job_id))
        return out

    def list_holding_spend(self, *, user_id: str | None = None) -> list[Job]:
        """Oldest first: every slot holder, and every embedded and inner run beside them."""
        out = self._reconciled(lambda j: j.holds_spend, user_id=user_id)
        out.sort(key=lambda j: (j.created_at, j.job_id))
        return out

    def list_queued(self, *, user_id: str | None = None) -> list[Job]:
        out = self._reconciled(lambda j: j.queued, user_id=user_id)
        out.sort(key=lambda j: (j.created_at, j.job_id))
        return out

    def _reconciled(self, wanted: Callable[[Job], bool], *, user_id: str | None) -> list[Job]:
        out: list[Job] = []
        for j in self.list_all(user_id=user_id):
            if not wanted(j):
                continue
            j = self._reap_if_orphaned(j)
            if wanted(j):
                out.append(j)
        return out

    def running_job_for(self, workspace: WorkspaceDir, hop: CycleHop) -> Job | None:
        """The RUN holding *hop*, however started: a scoring pass on it bills no ceiling of the cycle's."""
        return next(
            (
                j
                for j in self.list_holding_spend()
                if j.holds(workspace, hop) and not isinstance(j.holder, ScoringPass)
            ),
            None,
        )

    def pass_on(self, workspace: WorkspaceDir, hop: CycleHop) -> Job | None:
        """A scoring pass billing *hop*: a run there would be a second biller its ceiling never sees."""
        return next(
            (
                j
                for j in self.list_running()
                if j.holds(workspace, hop) and isinstance(j.holder, ScoringPass)
            ),
            None,
        )

    def list_launches(self, *, user_id: str | None = None) -> list[Job]:
        """Campaign launches in flight, queued included: what an account's concurrency limit holds."""
        return [
            j
            for j in (*self.list_running(user_id=user_id), *self.list_queued(user_id=user_id))
            if isinstance(j.holder, CampaignLaunch)
        ]

    def list_launched_today(self, *, user_id: str | None = None) -> list[Job]:
        """Campaign launches only: a scoring pass is load, never a campaign the daily quota counts."""
        today = utcnow_iso()[:10]
        return [
            j
            for j in self.list_all(user_id=user_id)
            if j.launched_on(today) and isinstance(j.holder, CampaignLaunch)
        ]

    def queued_drafts(self, *, user_id: str) -> frozenset[str]:
        """The check-ins a queued Start waits on: a reload serves each queued, never Start again."""
        return frozenset(
            j.holder.draft_id
            for j in self.list_queued(user_id=user_id)
            if isinstance(j.holder, CampaignLaunch) and j.holder.draft_id is not None
        )

    def _persist(self, job: Job) -> None:
        write_json(self._path(job.job_id), _JOB.dump_python(job, mode="json"))

    def _path(self, job_id: str) -> Path:
        if not job_id or "/" in job_id or "\\" in job_id or job_id.startswith("."):
            raise ValueError(f"invalid job_id: {job_id!r}")
        return self._dir / f"{job_id}.json"
