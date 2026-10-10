from __future__ import annotations

import asyncio
import logging
import secrets
import threading
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from json import JSONDecodeError
from pathlib import Path
from typing import ClassVar, Literal, get_args

from filelock import Timeout
from pydantic import ConfigDict, TypeAdapter

from promptpotter.application.jobs.capacity import resolve_run_capacity
from promptpotter.application.jobs.interlock import (
    admission_lock,
    producer_alive,
    this_producer,
    this_producer_lock,
)
from promptpotter.config.paths import default_jobs_dir
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.phases import LaunchStage, RunPhase
from promptpotter.domain.spend import SpendCeilings
from promptpotter.infrastructure.store.io import read_json, write_json
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import ServiceUnavailableError

logger = logging.getLogger(__name__)

# The slot is held before the mint resolves the cycle it will name; `update_target` fills it in.
UNRESOLVED_HOP = CycleHop(campaign_id="", cycle_id="")

JobStage = LaunchStage | Literal[RunPhase.RUNNING]
_JOB_STAGES: frozenset[RunPhase] = frozenset(
    stage for arm in get_args(JobStage) for stage in get_args(arm)
)
SLOT_STAGES: frozenset[RunPhase] = frozenset({RunPhase.STARTING, RunPhase.RUNNING})
assert SLOT_STAGES < _JOB_STAGES, "a slot stage no job can be in"


@dataclass(frozen=False)
class Job:
    """Outlives its release: the daily launch quota counts it."""

    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    job_id: str
    user_id: str
    # WHO launched it, never the account: a delegate shares its owner's `user_id`.
    principal_id: str
    campaign_id: str
    cycle_id: str
    dataset_name: str
    stage: JobStage
    created_at: str
    started_at: str | None
    # A release is its own act, never a fourth stage: `stage` stays where the launch got to.
    released_at: str | None
    # What the account holds back for it (``HeldLimits.reserve``).
    reserve: SpendCeilings = field(default_factory=SpendCeilings)
    # Unset = never granted a run, so its failure is a refusal.
    admitted_at: str | None = None
    producer_id: str = ""
    # Non-empty IS the refused launch: set only where it was never admitted.
    refusal: str = ""

    @property
    def hop(self) -> CycleHop:
        return CycleHop(campaign_id=self.campaign_id, cycle_id=self.cycle_id)

    @property
    def released(self) -> bool:
        return self.released_at is not None

    @property
    def queued(self) -> bool:
        return not self.released and self.stage is RunPhase.QUEUED

    @property
    def holds_slot(self) -> bool:
        return not self.released and self.stage in SLOT_STAGES


_JOB = TypeAdapter(Job)


class JobRegistry:
    """No process sweeps another's jobs: one is cleared only by proving its producer gone."""

    @classmethod
    def attach(cls) -> JobRegistry:
        return cls(default_jobs_dir(), capacity=resolve_run_capacity)

    def __init__(self, jobs_dir: Path, *, capacity: Callable[[int], int]) -> None:
        self._dir = jobs_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        # Reentrant: `request_slot` → `list_running` → `_reap_if_orphaned` → `release` retake it.
        self._lock = threading.RLock()
        # Its cross-process peer, and ONE instance so nesting is reentrant the same way.
        self._gate = admission_lock(jobs_dir)
        self._capacity = capacity
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def create(
        self,
        *,
        user_id: str,
        principal_id: str,
        hop: CycleHop,
        dataset_name: str,
        stage: JobStage = RunPhase.STARTING,
    ) -> Job:
        job_id = secrets.token_urlsafe(12)
        now = utcnow_iso()
        job = Job(
            job_id=job_id,
            user_id=user_id,
            principal_id=principal_id,
            campaign_id=hop.campaign_id,
            cycle_id=hop.cycle_id,
            dataset_name=dataset_name,
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
        try:
            with self._lock, self._gate:
                yield
        except Timeout as exc:
            raise ServiceUnavailableError(
                "The machine's admission gate is held by another process that has not released "
                "it. Nothing was started; retry shortly.",
                code="admission_gate_stuck",
            ) from exc

    def request_slot(
        self,
        *,
        user_id: str,
        principal_id: str,
        dataset_name: str,
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
                hop=hop,
                dataset_name=dataset_name,
                stage=stage,
            )

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

    def holder(self) -> Job | None:
        running = self.list_running()
        return min(running, key=lambda j: j.created_at) if running else None

    def mark_admitted(self, job_id: str, reserve: SpendCeilings) -> None:
        job = self.get(job_id)
        if job is None:
            return
        job.admitted_at = utcnow_iso()
        job.reserve = reserve
        self._persist(job)

    def set_reserve(self, job_id: str, reserve: SpendCeilings) -> None:
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
        out: list[Job] = []
        for path in self._dir.glob("*.json"):
            try:
                raw = read_json(path)
            except (OSError, JSONDecodeError):
                continue
            job = _JOB.validate_python(raw)
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
        """A reconciling READ, deliberately: a job whose producer is gone is released here."""
        return self._reconciled(lambda j: j.holds_slot, user_id=user_id)

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

    def running_job_for(self, hop: CycleHop) -> Job | None:
        return next((j for j in self.list_running() if j.hop == hop), None)

    def list_created_today(self, *, user_id: str | None = None) -> list[Job]:
        today = utcnow_iso()[:10]
        return [j for j in self.list_all(user_id=user_id) if j.created_at.startswith(today)]

    def _persist(self, job: Job) -> None:
        write_json(self._path(job.job_id), asdict(job))

    def _path(self, job_id: str) -> Path:
        if not job_id or "/" in job_id or "\\" in job_id or job_id.startswith("."):
            raise ValueError(f"invalid job_id: {job_id!r}")
        return self._dir / f"{job_id}.json"


__all__ = [
    "UNRESOLVED_HOP",
    "Job",
    "JobRegistry",
    "JobStage",
]
