from __future__ import annotations

import asyncio
import logging
import secrets
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import ValidationError

from promptpotter.application.jobs.capacity import resolve_run_capacity
from promptpotter.application.jobs.interlock import (
    AdmissionGate,
    producer_alive,
    reclaim_producer,
    this_producer,
    this_producer_lock,
)
from promptpotter.application.runner.termination import run_stop_reason
from promptpotter.config.paths import ENDED_JOBS_DIR
from promptpotter.config.settings import CODE_REVISION, settings
from promptpotter.domain.cycle_paths import CycleHop, WorkspaceDir
from promptpotter.domain.job import (
    JOB_ADAPTER,
    UNRESOLVED_HOP,
    CampaignLaunch,
    Job,
    JobHolder,
    JobLapse,
    JobStage,
    ScoringPass,
    SlotHolder,
)
from promptpotter.domain.phases import LaunchStage, RunPhase, StopReason
from promptpotter.domain.spend import Budget
from promptpotter.infrastructure.store.io import open_text_robust, unlink_robust, write_json
from promptpotter.shared.clock import iso_z, utcnow_iso
from promptpotter.shared.errors import (
    ConflictError,
    JobUnreadableError,
    NotFoundError,
    ServiceUnavailableError,
    validation_reason,
)

logger = logging.getLogger(__name__)

# Each ask takes the machine-wide admission lock.
_QUEUE_POLL_S = 2.0
# How long a reaped job's ending stays readable by its id.
_ENDED_KEPT = timedelta(days=7)


def launch_interrupted(exc: BaseException) -> bool:
    """Someone ASKED the launch to stop; neither type is visible to ``except Exception``."""
    return isinstance(exc, KeyboardInterrupt | asyncio.CancelledError)


class JobRegistry:
    """No process reaps another's live job without proving its producer gone."""

    @classmethod
    def attach(cls, jobs_dir: Path) -> JobRegistry:
        return cls(jobs_dir, capacity=resolve_run_capacity)

    @classmethod
    def as_staged(cls, jobs_dir: Path, staged: Mapping[Path, bytes]) -> JobRegistry:
        """The jobs as a landing would leave them: each file *staged* names reads as staged."""
        return cls(jobs_dir, capacity=resolve_run_capacity, staged=staged)

    def __init__(
        self,
        jobs_dir: Path,
        *,
        capacity: Callable[[int], int],
        staged: Mapping[Path, bytes] | None = None,
    ) -> None:
        self._dir = jobs_dir
        self._staged = staged or {}
        # Made by its first write (a job, a lock), never here: a landing's plan reads it and writes nothing.
        # Reentrant: under `admission_gate`, `_orphaned` and `release` retake it.
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

    def has_room(self, count: int) -> bool:
        """Whether *count* slots asked together all start now; ask it under the gate that takes them."""
        with self.admission_gate():
            live = len(self.list_running())
            return all(live + i < self._capacity(live + i) for i in range(count))

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
            self._end(job, lapse="withdrawn")
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

    def release(
        self, job_id: str, ended: StopReason | BaseException | None = None, *, stepped: bool = False
    ) -> Job | None:
        """Stamps what ended the job, the one reading of it, which the cycle's declared stop takes
        (`admission.py::release_slot`): the `StopReason` its run returned (*stepped* where it
        paused at its round allowance); a raise before its admission refused it, and an interrupt
        or a raise after it is the stop it reads as (`run_stop_reason`); nothing at all is a
        scoring pass that returned."""
        job = self.get(job_id)
        if job is None:
            return None
        match ended:
            case StopReason():
                self._end(job, ending=ended, stepped=stepped)
            case BaseException() if not job.admitted and not launch_interrupted(ended):
                self._end(job, lapse="refused", refusal=str(ended))
            case BaseException():
                self._end(job, ending=run_stop_reason(ended))
            case None:
                self._end(job)
        return job

    def _end(
        self,
        job: Job,
        *,
        ending: StopReason | None = None,
        stepped: bool = False,
        lapse: JobLapse | None = None,
        refusal: str = "",
    ) -> None:
        # Stamped once: a queue waiter waking on a job already ended raises into a second release.
        if job.released:
            return
        job.released_at = utcnow_iso()
        job.ending, job.stepped, job.lapse, job.refusal = ending, stepped, lapse, refusal
        job.__post_init__()
        self._persist(job)
        with self._lock:
            self._tasks.pop(job.job_id, None)

    def launched_by(self, job_ids: Sequence[str], *, principal_id: str) -> list[Job] | None:
        """Each of *job_ids* as *principal_id* launched it, a reaped one as its release stamped it;
        ``None`` where any is unknown or another's launch. A live job whose producer is gone reads
        `orphaned` here before the sweep releases it: a reader never waits on one that will not
        end."""
        jobs = [self.get(job_id) or self._read(self._ended_path(job_id)) for job_id in job_ids]
        known = [job for job in jobs if job is not None and job.principal_id == principal_id]
        if not job_ids or len(known) != len(jobs):
            return None
        for job in known:
            if not job.released and self._orphaned(job):
                job.released_at, job.lapse = utcnow_iso(), "orphaned"
        return known

    def get(self, job_id: str) -> Job | None:
        return self._read(self._path(job_id))

    def _read(self, path: Path) -> Job | None:
        """``None``: no such job, or one deleted since the listing; a file that does not load raises."""
        raw: str | bytes
        if (staged := self._staged.get(path)) is not None:
            raw = staged
        else:
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
        """Released jobs included, until `reap_orphans` ends one launched before today."""
        out = [
            job
            for path in self._dir.glob("*.json")
            if (job := self._read(path)) is not None and user_id in (None, job.user_id)
        ]
        out.sort(key=lambda j: j.created_at, reverse=True)
        return out

    def _orphaned(self, job: Job) -> bool:
        """``_tasks`` holds only THIS process's jobs: "no task" is not "dead" — ask the producer lock."""
        with self._lock:
            task = self._tasks.get(job.job_id)
        if task is not None and not task.done():
            return False
        # Adopted: ask the lock — on Windows the spawned process is the venv launcher, not the run.
        adopted = task is not None and job.producer_id != this_producer(self._dir)
        return not ((task is None or adopted) and producer_alive(self._dir, job.producer_id))

    def list_running(self, *, user_id: str | None = None) -> list[Job]:
        """Oldest first, and a plain read: a job whose producer is gone is left out, never released."""
        out = self._live(lambda j: j.holds_slot, user_id=user_id)
        out.sort(key=lambda j: (j.created_at, j.job_id))
        return out

    def list_holding_spend(self, *, user_id: str | None = None) -> list[Job]:
        """Oldest first: every slot holder, and every embedded and inner run beside them."""
        out = self._live(lambda j: j.holds_spend, user_id=user_id)
        out.sort(key=lambda j: (j.created_at, j.job_id))
        return out

    def list_queued(self, *, user_id: str | None = None) -> list[Job]:
        out = self._live(lambda j: j.queued, user_id=user_id)
        out.sort(key=lambda j: (j.created_at, j.job_id))
        return out

    def _live(self, wanted: Callable[[Job], bool], *, user_id: str | None) -> list[Job]:
        return [j for j in self.list_all(user_id=user_id) if wanted(j) and not self._orphaned(j)]

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

    def draft_starts(self, *, user_id: str) -> dict[str, LaunchStage]:
        """Each check-in a live Start holds, at its stage."""
        starts: dict[str, LaunchStage] = {}
        for job in self._live(lambda j: not j.released, user_id=user_id):
            if not isinstance(job.holder, CampaignLaunch) or job.holder.draft_id is None:
                continue
            match job.stage:
                case RunPhase.QUEUED | RunPhase.STARTING as stage:
                    starts[job.holder.draft_id] = stage
        return starts

    def reap_orphans(self) -> None:
        """The sweep's, and no read's: releases each job whose producer is gone, reclaims that
        producer's file, moves a release launched before today among the ended, and deletes an
        ended record released over `_ENDED_KEPT` ago."""
        today = utcnow_iso()[:10]
        kept_from = iso_z(datetime.now(UTC) - _ENDED_KEPT)
        with self.admission_gate():
            for path in self._dir.joinpath(ENDED_JOBS_DIR).glob("*.json"):
                if (ended := self._read(path)) is not None and (
                    ended.released_at or ""
                ) < kept_from:
                    unlink_robust(path)
            for job in self.list_all():
                if job.released:
                    if not job.launched_on(today):
                        self._persist(job, at=self._ended_path(job.job_id))
                        unlink_robust(self._path(job.job_id))
                elif self._orphaned(job):
                    logger.warning(
                        "job %s claims %s but its producer is gone — releasing",
                        job.job_id,
                        job.stage.value,
                    )
                    self._end(job, lapse="orphaned")
                    reclaim_producer(self._dir, job.producer_id)

    def _persist(self, job: Job, *, at: Path | None = None) -> None:
        write_json(at or self._path(job.job_id), JOB_ADAPTER.dump_python(job, mode="json"))

    def _path(self, job_id: str) -> Path:
        if not job_id or "/" in job_id or "\\" in job_id or job_id.startswith("."):
            # Names no file this registry could hold, so no job: refused as any absent one is.
            raise NotFoundError(f"no job {job_id!r}")
        return self._dir / f"{job_id}.json"

    def _ended_path(self, job_id: str) -> Path:
        return self._path(job_id).parent / ENDED_JOBS_DIR / f"{job_id}.json"
