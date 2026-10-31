from __future__ import annotations

import asyncio
import logging
import secrets
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from pydantic import ValidationError

from promptpotter.application.jobs.capacity import resolve_run_capacity
from promptpotter.application.jobs.interlock import (
    AdmissionGate,
    producer_alive,
    this_producer,
    this_producer_lock,
)
from promptpotter.config.settings import CODE_REVISION, settings
from promptpotter.domain.cycle_paths import CycleHop, WorkspaceDir
from promptpotter.domain.job import (
    JOB_ADAPTER,
    UNRESOLVED_HOP,
    CampaignLaunch,
    Job,
    JobHolder,
    JobStage,
    ScoringPass,
    SlotHolder,
)
from promptpotter.domain.phases import RunPhase
from promptpotter.domain.spend import Budget
from promptpotter.infrastructure.store.io import open_text_robust, unlink_robust, write_json
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import (
    ConflictError,
    JobUnreadableError,
    ServiceUnavailableError,
    validation_reason,
)

logger = logging.getLogger(__name__)

# Each ask takes the machine-wide admission lock.
_QUEUE_POLL_S = 2.0


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
        with self.admission_gate():
            if (landing := self._gate.landing()) is not None:
                raise ServiceUnavailableError(
                    f"This box is landing code {landing[:12]} and admits nothing else until it "
                    "restarts on it. Nothing was started; launch again shortly.",
                    code="landing",
                )
            job = Job(
                job_id=secrets.token_urlsafe(12),
                user_id=user_id,
                principal_id=principal_id,
                workspace=str(workspace),
                campaign_id=hop.campaign_id,
                cycle_id=hop.cycle_id,
                holder=holder,
                stage=stage,
                created_at=utcnow_iso(),
                started_at=None,
                released_at=None,
                code_revision=CODE_REVISION,
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
            # A landing holds the queue: what an older process promotes, it starts on older code.
            if live >= self._capacity(live) or self._gate.landing() is not None:
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
            job.code_revision = CODE_REVISION
            self._persist(job)
            return True

    def landing(self) -> str | None:
        return self._gate.landing()

    def close_for_landing(self, target: str) -> list[Job]:
        """Admission closes to every process not on *target*, unless a live job on other code holds it
        open; those are returned, and nothing is closed."""
        with self.admission_gate():
            held = [job for job in self.list_holding_spend() if job.code_revision != target]
            if not held:
                self._gate.close_for(target)
            return held

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
        return self._read(self._path(job_id))

    @staticmethod
    def _read(path: Path) -> Job | None:
        """``None``: no such job, or one deleted since the listing; a file that does not load raises."""
        try:
            with open_text_robust(path) as fh:
                raw = fh.read()
        except FileNotFoundError:
            return None
        try:
            return JOB_ADAPTER.validate_json(raw)
        except ValidationError as exc:
            raise JobUnreadableError(
                path=str(path), reason=validation_reason(exc, shown=1)
            ) from exc

    def list_all(self, *, user_id: str | None = None) -> list[Job]:
        """A released job launched before today is deleted here: every reader past a release counts today's."""
        today = utcnow_iso()[:10]
        out: list[Job] = []
        for path in self._dir.glob("*.json"):
            if (job := self._read(path)) is None:
                continue
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
        write_json(self._path(job.job_id), JOB_ADAPTER.dump_python(job, mode="json"))

    def _path(self, job_id: str) -> Path:
        if not job_id or "/" in job_id or "\\" in job_id or job_id.startswith("."):
            raise ValueError(f"invalid job_id: {job_id!r}")
        return self._dir / f"{job_id}.json"
