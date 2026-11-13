from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from promptpotter.application.jobs.capacity import resolve_run_capacity
from promptpotter.application.jobs.interlock import AdmissionGate
from promptpotter.application.runner.termination import run_stop_reason
from promptpotter.config.paths import ENDED_JOBS_DIR
from promptpotter.config.settings import CODE_REVISION, settings
from promptpotter.domain.campaign import CampaignRefusedError
from promptpotter.domain.cycle_paths import CycleHop, WorkspaceDir
from promptpotter.domain.job import (
    JOB_ADAPTER,
    LAUNCH_EXITS,
    LAUNCH_MOVES,
    UNRESOLVED_HOP,
    CampaignLaunch,
    Job,
    JobEnding,
    JobHolder,
    LaunchMove,
    LaunchMoveError,
    SlotHolder,
)
from promptpotter.domain.phases import LaunchStage, RunPhase, StopReason
from promptpotter.domain.refusal import Refusal
from promptpotter.infrastructure import producer_lock
from promptpotter.infrastructure.holders import in_flight, job_path, read_job
from promptpotter.infrastructure.producer_lock import this_producer
from promptpotter.infrastructure.store.io import unlink_robust, write_json
from promptpotter.shared.clock import iso_z, utcnow_iso
from promptpotter.shared.errors import (
    ConflictError,
    FileUnreadableError,
    PotterError,
    ServiceUnavailableError,
)

logger = logging.getLogger(__name__)

# Each ask takes the machine-wide admission lock.
_QUEUE_POLL_S = 2.0
# How long a reaped job's ending stays readable by its id.
_ENDED_KEPT = timedelta(days=7)
# Each job file a listing could not load, named once per process: every poll lists the jobs.
_named: set[str] = set()


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
        # Reentrant: under `admission_gate`, `release` retakes it.
        self._lock = threading.RLock()
        # Its cross-process peer, nesting the same way.
        self._gate = AdmissionGate(jobs_dir)
        self._capacity = capacity

    def create(
        self,
        *,
        user_id: str,
        principal_id: str,
        workspace: WorkspaceDir,
        hop: CycleHop,
        holder: JobHolder,
        stage: LaunchStage = RunPhase.STARTING,
    ) -> Job:
        """A launch ASKED: with a slot (``STARTING``) or a place in the queue."""
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
            stage: LaunchStage = (
                RunPhase.STARTING if live < self._capacity(live) else RunPhase.QUEUED
            )
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
            return self.move(job_id, LaunchMove.CLAIM) is not None

    def cancel_queued(self, job_id: str) -> Job | None:
        """The job withdrawn; ``None`` where it is no longer queued. Whose act it is, the caller asks."""
        with self.admission_gate():
            job = self.get(job_id)
            if job is None or not job.queued:
                return None
            return self.move(job_id, LaunchMove.END, ended=JobEnding(lapse="withdrawn"))

    def move(
        self,
        job_id: str,
        move: LaunchMove,
        *,
        hop: CycleHop | None = None,
        ended: JobEnding | None = None,
    ) -> Job | None:
        """THE one writer of a launch's phase, every door's: *move* by its `LAUNCH_MOVES` row, a LAND
        onto *hop*, an END stamping *ended*. ``None`` where the job is gone, or an END finds it
        ended (stamped once: a queue waiter waking on a job already ended raises into a second
        release); an undeclared move raises `LaunchMoveError`."""
        sources, targets = LAUNCH_MOVES[move]
        stamp: dict[str, Any]
        match move:
            case LaunchMove.CLAIM:
                stamp = {"stage": RunPhase.STARTING}
            case LaunchMove.ADMIT:
                stamp = {"admitted_at": utcnow_iso()}
            case LaunchMove.LAND:
                assert hop is not None, "a launch lands on a cycle"
                stamp = {"campaign_id": hop.campaign_id, "cycle_id": hop.cycle_id}
            case LaunchMove.START:
                stamp = {"stage": RunPhase.RUNNING, "started_at": utcnow_iso()}
            case LaunchMove.END:
                stamp = {"released_at": utcnow_iso(), **(ended or JobEnding())._asdict()}

        def change(job: Job) -> Mapping[str, Any] | None:
            if move is LaunchMove.END and job.released:
                return None
            if job.state not in sources:
                raise LaunchMoveError(f"job {job_id} is {job.state}: no {move} from there")
            moved = replace(job, **stamp)
            if moved.state not in targets:
                raise LaunchMoveError(f"job {job_id}: {move} from {job.state} reads {moved.state}")
            if (left := moved.exit) is not None and job.state not in LAUNCH_EXITS[left]:
                raise LaunchMoveError(f"job {job_id} is {job.state}: no {left} exit from there")
            return stamp

        return self._stamp(job_id, change)

    def _stamp(self, job_id: str, change: Callable[[Job], Mapping[str, Any] | None]) -> Job | None:
        """The one read-modify-write of a job file, read and persisted under the gate: a stamp
        outside it overwrote one inside (a lowered reserve, a release). *change* answers the fields
        it stamps on the job it is shown, or ``None`` to leave it; ``None`` where nothing stamped."""
        with self.admission_gate():
            if (job := self.get(job_id)) is None or (fields := change(job)) is None:
                return None
            self._persist(stamped := replace(job, **fields))
            return stamped

    @property
    def jobs_dir(self) -> Path:
        return self._dir

    def adopt(self, job_id: str) -> Job | None:
        """The job as this process adopted it; ``None`` where it was released first."""
        adopted = {"producer_id": this_producer(self._dir), "code_revision": CODE_REVISION}
        return self._stamp(job_id, lambda job: None if job.released else adopted)

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

    def release(
        self, job_id: str, ended: StopReason | BaseException | None = None, *, stepped: bool = False
    ) -> Job | None:
        """Stamps what ended the job, the one reading of it, which the cycle's declared stop takes
        (`admission.py::RunStart.release`): the `StopReason` its run returned (*stepped* where it
        paused at its round allowance); any raise in the queue withdrew it, one before its admission
        or a campaign's refusal at any point refused it, and an interrupt or a raise after it is the
        stop it reads as (`run_stop_reason`); nothing at all is a scoring pass that returned."""
        with self.admission_gate():
            job = self.get(job_id)
            if job is None:
                return None
            match ended:
                case StopReason():
                    ending = JobEnding(ending=ended, stepped=stepped)
                case BaseException() if job.queued:
                    ending = JobEnding(lapse="withdrawn")
                case BaseException() if isinstance(ended, CampaignRefusedError) or (
                    not job.admitted and not launch_interrupted(ended)
                ):
                    refused = ended if isinstance(ended, PotterError) else PotterError(str(ended))
                    ending = JobEnding(lapse="refused", refusal=Refusal.of(refused))
                case BaseException():
                    ending = JobEnding(ending=run_stop_reason(ended))
                case None:
                    ending = JobEnding()
            return self.move(job_id, LaunchMove.END, ended=ending) or job

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
            if not job.released and not self.in_flight(job):
                job.released_at, job.lapse = utcnow_iso(), "orphaned"
        return known

    def get(self, job_id: str) -> Job | None:
        return self._read(self._path(job_id))

    def _read(self, path: Path) -> Job | None:
        return read_job(path, self._staged.get(path))

    def list_all(self, *, user_id: str | None = None) -> list[Job]:
        """Released jobs included, until `reap_orphans` ends one launched before today. A file that
        does not load is named in the log and left out, never raised out of every listing; a
        landing refuses on it (a job file is a survivor)."""
        out: list[Job] = []
        for path in self._dir.glob("*.json"):
            try:
                job = self._read(path)
            except FileUnreadableError as exc:
                if str(path) not in _named:
                    _named.add(str(path))
                    logger.error("job listing leaves out %s: %s", path, exc)
                continue
            if job is not None and user_id in (None, job.user_id):
                out.append(job)
        out.sort(key=lambda j: j.created_at, reverse=True)
        return out

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

    def in_flight(self, job: Job) -> bool:
        """`holders.py::in_flight` over this jobs dir: what every live listing reads, and the quota."""
        return in_flight(self._dir, job)

    def _live(self, wanted: Callable[[Job], bool], *, user_id: str | None) -> list[Job]:
        return [j for j in self.list_all(user_id=user_id) if wanted(j) and self.in_flight(j)]

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

    def reap_orphans(self) -> list[Job]:
        """`quota.reap_dead_runs`'s alone: releases each job whose producer is gone, reclaims that
        producer's file, moves a release launched before today among the ended, and deletes an
        ended record released over `_ENDED_KEPT` ago. The jobs it released, as they stood."""
        today = utcnow_iso()[:10]
        kept_from = iso_z(datetime.now(UTC) - _ENDED_KEPT)
        reaped: list[Job] = []
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
                elif not self.in_flight(job):
                    logger.warning(
                        "job %s claims %s but its producer is gone — releasing",
                        job.job_id,
                        job.stage.value,
                    )
                    self.move(job.job_id, LaunchMove.END, ended=JobEnding(lapse="orphaned"))
                    # An unstamped or unspellable id names no file.
                    with contextlib.suppress(ValueError):
                        producer_lock.reclaim_producer(
                            producer_lock.producer_path(self._dir, job.producer_id)
                        )
                    reaped.append(job)
        return reaped

    def _persist(self, job: Job, *, at: Path | None = None) -> None:
        """A live job file is a survivor (`data_root.py::Kind.durable`); an ended one is run data."""
        write_json(
            at or self._path(job.job_id),
            JOB_ADAPTER.dump_python(job, mode="json"),
            durable=at is None,
        )

    def _path(self, job_id: str) -> Path:
        return job_path(self._dir, job_id)

    def _ended_path(self, job_id: str) -> Path:
        return self._path(job_id).parent / ENDED_JOBS_DIR / f"{job_id}.json"
