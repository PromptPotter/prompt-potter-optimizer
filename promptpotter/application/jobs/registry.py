from __future__ import annotations

import asyncio
import contextlib
import secrets
import subprocess
import threading
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from promptpotter.application.jobs import spawning
from promptpotter.application.jobs.capacity import resolve_run_capacity
from promptpotter.application.jobs.interlock import AdmissionGate
from promptpotter.application.run_stop import run_stop_reason
from promptpotter.config.paths import (
    ENDED_JOBS_DIR,
    LIVE_JOBS_INDEX,
    REFUSED_JOBS_DIR,
    UNSWEPT_DIR,
)
from promptpotter.config.settings import CODE_REVISION, settings
from promptpotter.domain.campaign import CampaignRefusedError
from promptpotter.domain.command_events import QueuedLaunch
from promptpotter.domain.cycle_paths import CycleHop, WorkspaceDir
from promptpotter.domain.job import (
    JOB_ADAPTER,
    LAUNCH_MOVES,
    LIVE_JOB_IDS,
    UNRESOLVED_HOP,
    UNSWEPT_FAULTS,
    CampaignLaunch,
    Job,
    JobHolder,
    LaunchMove,
    LaunchMoveError,
    SlotHolder,
)
from promptpotter.domain.phases import LaunchStage, RunPhase, StopReason
from promptpotter.domain.refusal import Refusal
from promptpotter.infrastructure.holders import in_flight, job_path, queued_path, read_job
from promptpotter.infrastructure.producer_lock import this_producer
from promptpotter.infrastructure.store.io import (
    read_bytes_optional,
    read_text_optional,
    unlink_robust,
    write_bytes,
    write_json,
)
from promptpotter.infrastructure.store.read_model import read_file
from promptpotter.shared.clock import iso_z, utcnow_iso
from promptpotter.shared.errors import (
    FileUnreadableError,
    PotterError,
    ServiceUnavailableError,
)

# What runs a promoted launch's ask in its own process: a process boundary, never an import.
_QUEUED_LAUNCH_MODULE = "promptpotter.application.queued_launch"
# How long a reaped job's ending stays readable by its id.
_ENDED_KEPT = timedelta(days=7)


def _counted_on(job: Job, day: str) -> bool:
    """What the daily quota counts: a campaign launch of *day*; a scoring pass is load, never one."""
    return job.launched_on(day) and isinstance(job.holder, CampaignLaunch)


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
        # Every thread's and process's, reentrant: under `admission_gate`, `release` retakes it.
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
    def admission_gate(self, *, launching: bool = False) -> Iterator[None]:
        """Every ceiling read belongs inside: outside, two admissions read one free slot and take it."""
        with self._gate.held(launching=launching):
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
            live = len(self.list_running())
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

    def file_ask(self, ask: QueuedLaunch) -> None:
        """A queued launch's ask, filed beside its job once its command is acked: from here the
        job waits on disk, its asker free to go (`holders.py::in_flight`)."""
        with self.admission_gate():
            if (job := self.get(ask.job_id)) is not None and job.queued:
                write_bytes(queued_path(self._dir, ask.job_id), ask.model_dump_json().encode())

    def queue_order(self) -> list[Job]:
        """Least-served first: plain FIFO lets one account's burst push everyone else behind it."""
        held = Counter(j.user_id for j in self.list_running())
        return sorted(self.list_queued(), key=lambda j: (held[j.user_id], j.created_at, j.job_id))

    def promote(self) -> None:
        """Each queued launch a free slot takes, the head of `queue_order` first, run in its own
        process: what frees a slot (a release, the sweep) promotes, never a waiter's clock. One
        whose process exits before taking its job is ended, and the next promoted. Off the gate:
        the process it starts takes it."""
        while (claimed := self._claim_head()) is not None:
            job_id, ask = claimed
            child = spawning.start(_QUEUED_LAUNCH_MODULE, ask, log=None)

            def watch(job_id: str = job_id, child: subprocess.Popen[bytes] = child) -> None:
                if not spawning.adopted(child):
                    self.move(job_id, LaunchMove.END)
                    self.promote()

            # A daemon: this process may exit first, and the sweep then ends a job never taken.
            threading.Thread(target=watch, name=f"job-{job_id}-promote", daemon=True).start()

    def _claim_head(self) -> tuple[str, bytes] | None:
        """The first queued job with its ask filed, claimed for this process; ``None`` while the
        machine is full or a landing holds the queue (what an older process promotes, it starts on
        older code)."""
        with self.admission_gate():
            live = len(self.list_running())
            if live >= self._capacity(live) or self._gate.landing() is not None:
                return None
            for job in self.queue_order():
                if (ask := read_bytes_optional(queued_path(self._dir, job.job_id))) is not None:
                    self.move(job.job_id, LaunchMove.CLAIM)
                    return job.job_id, ask
        return None

    def cancel_queued(self, job_id: str) -> Job | None:
        """The job withdrawn; ``None`` where it is no longer queued. Whose act it is, the caller asks."""
        with self.admission_gate():
            job = self.get(job_id)
            if job is None or not job.queued:
                return None
            return self.move(job_id, LaunchMove.END)

    def move(self, job_id: str, move: LaunchMove, *, hop: CycleHop | None = None) -> Job | None:
        """THE one writer of a launch's phase, every door's: *move* by its `LAUNCH_MOVES` row, a LAND
        onto *hop*. ``None`` where the job is gone, or an END finds it ended (stamped once: a
        queue waiter waking on a job already ended raises into a second release); an undeclared
        move raises `LaunchMoveError`."""
        sources, targets = LAUNCH_MOVES[move]
        stamp: dict[str, Any]
        match move:
            case LaunchMove.CLAIM:
                # The claimer holds it until the process it starts adopts it.
                stamp = {
                    "stage": RunPhase.STARTING,
                    "producer_id": this_producer(self._dir),
                    "code_revision": CODE_REVISION,
                }
            case LaunchMove.ADMIT:
                stamp = {"admitted_at": utcnow_iso()}
            case LaunchMove.LAND:
                assert hop is not None, "a launch lands on a cycle"
                stamp = {"campaign_id": hop.campaign_id, "cycle_id": hop.cycle_id}
            case LaunchMove.START:
                stamp = {"stage": RunPhase.RUNNING, "started_at": utcnow_iso()}
            case LaunchMove.END:
                stamp = {"released_at": utcnow_iso()}

        def change(job: Job) -> Mapping[str, Any] | None:
            if move is LaunchMove.END and job.released:
                return None
            if job.state not in sources:
                raise LaunchMoveError(f"job {job_id} is {job.state}: no {move} from there")
            moved = replace(job, **stamp)
            if moved.state not in targets:
                raise LaunchMoveError(f"job {job_id}: {move} from {job.state} reads {moved.state}")
            return stamp

        moved = self._stamp(job_id, change)
        if moved is not None and not moved.queued:
            unlink_robust(queued_path(self._dir, job_id))
        return moved

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

    def release(self, job_id: str, ended: BaseException | None = None) -> StopReason | None:
        """Ends the job, answering the stop its raise *ended* reads as, which the cycle's declared
        stop takes (`opening.py::Opening`'s exit; a run declares its own): an interrupt, or a raise
        once admitted, is the stop it reads as (`run_stop_reason`); ``None`` where it was withdrawn
        from the queue, refused (before its run started, or by its campaign at any point), or
        returned. How it ended is its cycle's stop, never the job's (`command_reading.py`); a
        refusal is filed beside it, before its release, so no reader finds it released unsaid. The
        slot it frees promotes the queue's head."""
        with self.admission_gate():
            job = self.get(job_id)
            if job is None or job.released:
                return None
            refused = ended is not None and (
                isinstance(ended, CampaignRefusedError)
                or (job.started_at is None and not launch_interrupted(ended))
            )
            if refused and not job.queued:
                said = ended if isinstance(ended, PotterError) else PotterError(str(ended))
                write_json(self._refused_path(job_id), Refusal.of(said).model_dump(mode="json"))
            self.move(job_id, LaunchMove.END)
        self.promote()
        return None if ended is None or refused or job.queued else run_stop_reason(ended)

    def refusal(self, job_id: str) -> Refusal | None:
        """What *job_id* was refused with, where its release refused it."""
        return read_file(self._refused_path(job_id), Refusal.model_validate_json)

    def launched_by(self, job_ids: Sequence[str], *, principal_id: str) -> list[Job] | None:
        """Each of *job_ids* as *principal_id* launched it, a reaped one as its release stamped it;
        ``None`` where any is unknown or another's launch. A live job whose producer is gone reads
        released here before the sweep releases it: a reader never waits on one that will not
        end."""
        jobs = [self.get(job_id) or self._read(self._ended_path(job_id)) for job_id in job_ids]
        known = [job for job in jobs if job is not None and job.principal_id == principal_id]
        if not job_ids or len(known) != len(jobs):
            return None
        for job in known:
            if not job.released and not self.in_flight(job):
                job.released_at = utcnow_iso()
        return known

    def get(self, job_id: str) -> Job | None:
        return self._read(self._path(job_id))

    def _read(self, path: Path) -> Job | None:
        return read_job(path, self._staged.get(path))

    def list_all(self, *, user_id: str | None = None) -> list[Job]:
        """Released jobs included, until `reap_orphans` ends one launched before today."""
        out = [j for j in self._loaded(self._dir.glob("*.json")) if user_id in (None, j.user_id)]
        out.sort(key=lambda j: j.created_at, reverse=True)
        return out

    def _loaded(self, paths: Iterable[Path]) -> Iterator[Job]:
        """Each job of *paths* that loads: one that does not is left out of every listing, never
        raised out of it, and named by `unloadable` (`machine-status`); a landing refuses on it."""
        for path in paths:
            with contextlib.suppress(FileUnreadableError):
                if (job := self._read(path)) is not None:
                    yield job

    def unloadable(self) -> list[str]:
        """Each job file no listing counts, with why it does not load."""
        named: list[str] = []
        for path in sorted(self._dir.glob("*.json")):
            try:
                self._read(path)
            except FileUnreadableError as exc:
                named.append(f"{path.name}: {exc}")
        return named

    def unswept(self) -> list[str]:
        """Each periodic step whose last pass, in any process, left something, with what."""
        named: list[str] = []
        for path in sorted(self._dir.joinpath(UNSWEPT_DIR).glob("*.json")):
            try:
                faults = read_file(path, UNSWEPT_FAULTS.validate_json) or ()
            except FileUnreadableError as exc:
                faults = (str(exc),)
            named += [f"{path.stem}: {fault}" for fault in faults]
        return named

    def record_pass(self, step: str, faults: Sequence[str]) -> None:
        """*step*'s last pass, whole: a clean one clears what an earlier one left."""
        path = self._dir / UNSWEPT_DIR / f"{step}.json"
        if faults:
            write_json(path, list(faults))
        else:
            unlink_robust(path)

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
        """Read off the live-jobs index, never a glob of every job file."""
        return [
            j
            for j in self._loaded(self._path(job_id) for job_id in self._indexed())
            if user_id in (None, j.user_id) and wanted(j) and self.in_flight(j)
        ]

    def _indexed(self) -> tuple[str, ...]:
        path = self._dir / LIVE_JOBS_INDEX
        raw = self._staged.get(path) or read_text_optional(path)
        return LIVE_JOB_IDS.validate_json(raw) if raw.strip() else ()

    def _index(self, ids: Iterable[str]) -> None:
        """Written under the gate by the writer of the job files it names, after them: a reader
        between the two reads a released job, which no live listing counts."""
        write_json(self._dir / LIVE_JOBS_INDEX, sorted(set(ids)))

    def list_launches(self, *, user_id: str | None = None) -> list[Job]:
        """Campaign launches in flight, queued included: what an account's concurrency limit holds."""
        return [
            j
            for j in (*self.list_running(user_id=user_id), *self.list_queued(user_id=user_id))
            if isinstance(j.holder, CampaignLaunch)
        ]

    def list_launched_today(self, *, user_id: str | None = None) -> list[Job]:
        today = utcnow_iso()[:10]
        return [j for j in self.list_all(user_id=user_id) if _counted_on(j, today)]

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
        """`shares.py::reap_dead_runs`'s alone: releases each job whose producer is gone, moves a
        release launched before today among the ended, and deletes an
        ended record released over `_ENDED_KEPT` ago. The jobs it released, as they stood. Its scans
        are off the gate, which takes each orphan alone, asked again: an adoption made it live."""
        today = utcnow_iso()[:10]
        kept_from = iso_z(datetime.now(UTC) - _ENDED_KEPT)
        reaped: list[Job] = []
        for path in self._dir.joinpath(ENDED_JOBS_DIR).glob("*.json"):
            if (ended := self._read(path)) is not None and (ended.released_at or "") < kept_from:
                unlink_robust(self._refused_path(ended.job_id))
                unlink_robust(path)
        waited_from = iso_z(datetime.now(UTC) - timedelta(seconds=settings.QUEUE_MAX_WAIT_S))
        for job in self.list_all():
            if job.released and not _counted_on(job, today):
                self._persist(job)
            # A launch queued past the longest wait is withdrawn: nothing ran or was spent.
            if job.queued and job.created_at < waited_from:
                self.move(job.job_id, LaunchMove.END)
                continue
            if job.released or self.in_flight(job):
                continue
            with self.admission_gate():
                if self.in_flight(self.get(job.job_id) or job):
                    continue
                if self.move(job.job_id, LaunchMove.END) is None:
                    continue
            reaped.append(job)
        # A slot a dead producer held, or a landing reopened, is the queue's head's.
        self.promote()
        return reaped

    def _persist(self, job: Job) -> None:
        """A live job file is a survivor; an ended one is run data, filed among the ended once no
        count reads it: every release but today's campaign launches, which the daily quota counts
        off the jobs dir under the gate."""
        ended = job.released and not _counted_on(job, utcnow_iso()[:10])
        path = self._ended_path(job.job_id) if ended else self._path(job.job_id)
        write_json(path, JOB_ADAPTER.dump_python(job, mode="json"))
        if ended:
            unlink_robust(self._path(job.job_id))
        indexed = set(self._indexed())
        if (job.job_id in indexed) == job.released:
            self._index(indexed ^ {job.job_id})

    def _path(self, job_id: str) -> Path:
        return job_path(self._dir, job_id)

    def _ended_path(self, job_id: str) -> Path:
        return self._path(job_id).parent / ENDED_JOBS_DIR / f"{job_id}.json"

    def _refused_path(self, job_id: str) -> Path:
        return self._path(job_id).parent / REFUSED_JOBS_DIR / f"{job_id}.json"
