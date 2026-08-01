"""``JobRegistry`` — per-job audit + in-memory asyncio task tracking.

One JSON file per job at ``.promptpotter/jobs/{job_id}.json``. The
in-memory ``_tasks`` map holds live ``asyncio.Task`` handles; persistence
captures everything an audit reader needs without depending on the
process being alive.

At startup the registry walks `jobs/` and marks any ``pending`` /
``running`` job as ``stopped`` with ``stop_reason="server_restart"`` —
a torn task can't be recovered, and the stale "still running" status
would mislead the next operator.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from json import JSONDecodeError
from pathlib import Path
from typing import Literal

from promptpotter.config.paths import user_data_root
from promptpotter.infrastructure.store.io import read_json, write_json
from promptpotter.shared.clock import utcnow_iso

logger = logging.getLogger(__name__)

JobStatus = Literal["pending", "running", "completed", "failed", "stopped"]


@dataclass(frozen=False)
class Job:
    """Persisted job record. Lives at ``jobs/{job_id}.json``."""

    job_id: str
    user_id: str
    campaign_id: str
    cycle_id: str
    dataset_name: str
    status: JobStatus
    created_at: str
    started_at: str | None
    finished_at: str | None
    stop_reason: str | None


@dataclass(frozen=True)
class ReserveResult:
    """Outcome of an admission attempt (:meth:`JobRegistry.reserve`).

    Exactly one side is set: ``job`` is the reservation when a slot was free;
    ``holder`` is the run that owns the slot when the machine is at capacity.
    The launcher maps a ``holder`` to a 409 ``MachineBusyError``.
    """

    job: Job | None
    holder: Job | None


def default_jobs_dir() -> Path:
    """Jobs dir beside `projects/` in the user-data tree."""
    return user_data_root() / "jobs"


class JobRegistry:
    """Process-wide job tracker + run-admission gate.

    Thread-safe; persists per-job state on every status change. ``capacity`` is
    the number of runs admitted concurrently — the single sequential slot is
    ``capacity = 1`` (the default). Admission rides :meth:`reserve`, an atomic
    count-then-claim that closes the launch race; raising ``capacity`` is the
    concurrent-serving lever (gated on per-user keys + per-tenant rate limits —
    see ``docs/specs/roadmap.md § Run admission + concurrent serving``).
    """

    def __init__(
        self,
        jobs_dir: Path,
        *,
        capacity: int = 1,
        on_reap: Callable[[Job], None] | None = None,
    ) -> None:
        self._dir = jobs_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._capacity = capacity
        self._tasks: dict[str, asyncio.Task[None]] = {}
        # Fired whenever a job is proven dead (torn task, or stale-on-restart) so
        # the same liveness owner can stamp the cycle terminal — the second half
        # of reconciling the two owners this class's docstring names. Store-free:
        # the wiring in main.py resolves the cycle and writes it.
        self._on_reap = on_reap
        self._mark_stale_on_startup()

    def _fire_reap(self, job: Job) -> None:
        """Invoke the reap callback, never letting its failure break job tracking."""
        if self._on_reap is None:
            return
        try:
            self._on_reap(job)
        except Exception:
            logger.exception("on_reap callback failed for job %s", job.job_id)

    def create(
        self,
        *,
        user_id: str,
        campaign_id: str,
        cycle_id: str,
        dataset_name: str,
    ) -> Job:
        job_id = secrets.token_urlsafe(12)
        now = utcnow_iso()
        job = Job(
            job_id=job_id,
            user_id=user_id,
            campaign_id=campaign_id,
            cycle_id=cycle_id,
            dataset_name=dataset_name,
            status="pending",
            created_at=now,
            started_at=None,
            finished_at=None,
            stop_reason=None,
        )
        self._persist(job)
        return job

    def reserve(
        self,
        *,
        user_id: str,
        dataset_name: str,
        campaign_id: str = "",
        cycle_id: str = "",
    ) -> ReserveResult:
        """Atomically admit a run against ``capacity``, or report the holder.

        The slot count read and the reservation write happen under one lock with
        **no ``await`` between them** — that atomicity is what closes the launch
        race (two near-simultaneous launches cannot both pass the gate
        before either's record lands). At ``capacity = 1`` any in-flight run
        blocks a new admit (strictly sequential). The reservation is a ``pending``
        :class:`Job`; the mint path fills real ids later via :meth:`update_target`,
        and the launcher releases it (``mark_finished``) if the launch fails
        before the task starts — so a slot is never wedged.
        """
        with self._lock:
            running = self.list_running()
            if len(running) >= self._capacity:
                holder = min(running, key=lambda j: j.created_at)
                return ReserveResult(job=None, holder=holder)
            job = self.create(
                user_id=user_id,
                campaign_id=campaign_id,
                cycle_id=cycle_id,
                dataset_name=dataset_name,
            )
            return ReserveResult(job=job, holder=None)

    def update_target(self, job_id: str, *, campaign_id: str, cycle_id: str) -> None:
        """Fill the campaign/cycle ids on a reservation once the mint resolves them."""
        job = self.get(job_id)
        if job is None:
            return
        job.campaign_id = campaign_id
        job.cycle_id = cycle_id
        self._persist(job)

    def attach_task(self, job_id: str, task: asyncio.Task[None]) -> None:
        with self._lock:
            self._tasks[job_id] = task

    def mark_started(self, job_id: str) -> None:
        job = self.get(job_id)
        if job is None:
            return
        job.status = "running"
        job.started_at = utcnow_iso()
        self._persist(job)

    def mark_finished(
        self, job_id: str, *, status: JobStatus, stop_reason: str | None = None
    ) -> None:
        job = self.get(job_id)
        if job is None:
            return
        job.status = status
        job.finished_at = utcnow_iso()
        job.stop_reason = stop_reason
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
        return self._job_from_dict(raw)

    def list_all(self, *, user_id: str | None = None) -> list[Job]:
        out: list[Job] = []
        for path in self._dir.glob("*.json"):
            try:
                raw = read_json(path)
            except (OSError, JSONDecodeError):
                continue
            job = self._job_from_dict(raw)
            if user_id is not None and job.user_id != user_id:
                continue
            out.append(job)
        out.sort(key=lambda j: j.created_at, reverse=True)
        return out

    def _reap_if_orphaned(self, job: Job) -> Job:
        """Self-heal a ``running`` job whose in-process asyncio task is gone.

        Jobs run inside THIS process (single construction in the server lifespan,
        ``--workers 1``), so a running-status file with no live task in
        ``self._tasks`` is a zombie — the producer died without reaching
        ``mark_finished`` (e.g. a ``BaseException`` that skipped the teardown).
        Without this, the dead job holds the machine slot (409s every launch)
        until a server restart, while ``derive_run_phase`` already reads the same
        cycle as DETACHED — two liveness owners disagreeing. ``pending`` is left
        alone: the reserve→attach window legitimately has no task yet, and a
        pre-launch failure releases it via the launcher's ``mark_finished``."""
        if job.status != "running":
            return job
        with self._lock:
            task = self._tasks.get(job.job_id)
        if task is not None and not task.done():
            return job
        logger.warning("job %s claims running but its task is gone — reaping", job.job_id)
        self.mark_finished(job.job_id, status="stopped", stop_reason="producer_vanished")
        self._fire_reap(job)
        return self.get(job.job_id) or job

    def list_running(self, *, user_id: str | None = None) -> list[Job]:
        out: list[Job] = []
        for j in self.list_all(user_id=user_id):
            if j.status not in ("pending", "running"):
                continue
            j = self._reap_if_orphaned(j)
            if j.status in ("pending", "running"):
                out.append(j)
        return out

    def machine_holder(self, *, exclude_user_id: str) -> Job | None:
        """The oldest still-running job owned by a user other than *exclude_user_id*.

        The single global "is the machine busy for this user, and by whom"
        query. Both the launch gate (``check_launch_quotas``) and the
        ``/machine-status`` read call it, so the 409 a blocked user gets and
        the banner everyone sees can never disagree. Oldest-first so the holder
        is the run that actually owns the slot, not a later contender. ``None``
        when the machine is free for this user. Process-wide + ``--workers 1``
        makes this authoritative across every tenant.
        """
        others = [j for j in self.list_running() if j.user_id != exclude_user_id]
        if not others:
            return None
        return min(others, key=lambda j: j.created_at)

    def list_created_today(self, *, user_id: str | None = None) -> list[Job]:
        """Daily-campaigns quota probe — jobs created since UTC midnight.

        The prefix is sliced off ``utcnow_iso`` rather than composed, because
        ``created_at`` was MINTED by that function: any second spelling of "today" is a
        chance for the key and the stamp to disagree. One did — ``date.today()`` is the
        LOCAL date, so on a UTC+2 machine this quota rolled over two hours before the
        daily-spend quota beside it (``spend.py::start_of_utc_day``) and matched no
        ``created_at`` at all during the gap."""
        today = utcnow_iso()[:10]
        return [j for j in self.list_all(user_id=user_id) if j.created_at.startswith(today)]

    def _persist(self, job: Job) -> None:
        write_json(self._path(job.job_id), asdict(job))

    def _path(self, job_id: str) -> Path:
        if not job_id or "/" in job_id or "\\" in job_id or job_id.startswith("."):
            raise ValueError(f"invalid job_id: {job_id!r}")
        return self._dir / f"{job_id}.json"

    @staticmethod
    def _job_from_dict(raw: dict[str, object]) -> Job:
        return Job(
            job_id=str(raw.get("job_id", "")),
            user_id=str(raw.get("user_id", "")),
            campaign_id=str(raw.get("campaign_id", "")),
            cycle_id=str(raw.get("cycle_id", "")),
            dataset_name=str(raw.get("dataset_name", "")),
            status=_coerce_status(raw.get("status")),
            created_at=str(raw.get("created_at", "")),
            started_at=_optional_str(raw.get("started_at")),
            finished_at=_optional_str(raw.get("finished_at")),
            stop_reason=_optional_str(raw.get("stop_reason")),
        )

    def _mark_stale_on_startup(self) -> None:
        now = utcnow_iso()
        for path in self._dir.glob("*.json"):
            try:
                raw = read_json(path)
            except (OSError, JSONDecodeError):
                continue
            status = raw.get("status")
            if status not in ("pending", "running"):
                continue
            raw["status"] = "stopped"
            raw["finished_at"] = now
            raw["stop_reason"] = "server_restart"
            write_json(path, raw)
            logger.info("marked stale job %s as stopped (server_restart)", path.stem)
            # Reap the cycle too: an API-launched run that outlived its process
            # left a non-terminal cycle on disk. This is the boot-time bulk clear.
            self._fire_reap(self._job_from_dict(raw))


def _coerce_status(raw: object) -> JobStatus:
    if raw == "pending":
        return "pending"
    if raw == "running":
        return "running"
    if raw == "completed":
        return "completed"
    if raw == "failed":
        return "failed"
    return "stopped"


def _optional_str(raw: object) -> str | None:
    if raw is None:
        return None
    return str(raw)


__all__ = ["Job", "JobRegistry", "JobStatus", "ReserveResult", "default_jobs_dir"]
