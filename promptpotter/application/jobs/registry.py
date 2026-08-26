"""``JobRegistry`` — per-job audit + in-memory asyncio task tracking. At startup it marks every
``pending`` / ``running`` job ``stopped``: a torn task cannot be recovered, and would mislead."""

from __future__ import annotations

import asyncio
import logging
import secrets
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from json import JSONDecodeError
from pathlib import Path
from typing import Literal, cast, get_args

from promptpotter.config.paths import user_data_root
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.infrastructure.store.io import read_json, write_json
from promptpotter.shared.clock import utcnow_iso

logger = logging.getLogger(__name__)

# A reservation admitted BEFORE the mint resolves its ids — the slot is held, the cycle it
# will name does not exist yet. `update_target` fills it in once the mint returns.
UNRESOLVED_HOP = CycleHop(campaign_id="", cycle_id="")

JobStatus = Literal["pending", "running", "completed", "failed", "stopped"]
_JOB_STATUSES: frozenset[str] = frozenset(get_args(JobStatus))
# The two that still hold the machine slot — ``pending`` is the reserve→attach window, ``running``
# the task itself. One name, because three sites spelled the pair: a fourth reader that forgot
# ``pending`` would hand the slot out twice across that window and see nothing wrong.
LIVE_JOB_STATUSES: frozenset[JobStatus] = frozenset({"pending", "running"})
# A typo here matches nothing and reads as "not live", so the slot is handed out twice or never
# released — no error either way. The Literal is the source; fail at import instead.
assert LIVE_JOB_STATUSES <= _JOB_STATUSES, (
    f"LIVE_JOB_STATUSES names unknown JobStatuses: {sorted(LIVE_JOB_STATUSES - _JOB_STATUSES)}"
)


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
    # The ceilings this run was admitted at — until it finishes they are the account's outstanding
    # reservation, which is what stops two concurrent launches sharing one remainder.
    cap_usd: float | None = None
    cap_tokens: int | None = None

    @property
    def hop(self) -> CycleHop:
        """The cycle this job runs, as the pair that addresses it. Reads :data:`UNRESOLVED_HOP`
        between admission and mint — the slot is held before the cycle it will name exists."""
        return CycleHop(campaign_id=self.campaign_id, cycle_id=self.cycle_id)


@dataclass(frozen=True)
class ReserveResult:
    """Outcome of an admission attempt. Exactly one side is set — ``job`` when a slot was free,
    ``holder`` when the machine is at capacity, which the launcher maps to a 409."""

    job: Job | None
    holder: Job | None


def default_jobs_dir() -> Path:
    """Jobs dir beside `projects/` in the user-data tree."""
    return user_data_root() / "jobs"


class JobRegistry:
    """Process-wide job tracker + run-admission gate, thread-safe. ``capacity`` is how many runs are
    admitted concurrently; raising it is the concurrent-serving lever, gated per user and tenant."""

    def __init__(
        self,
        jobs_dir: Path,
        *,
        capacity: int = 1,
        on_reap: Callable[[Job], None] | None = None,
    ) -> None:
        self._dir = jobs_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        # Reentrant on purpose: `reserve` holds it across `list_running` →
        # `_reap_if_orphaned` → `mark_finished`, each of which takes it again.
        # A plain Lock deadlocks the event-loop thread on the SECOND launch.
        self._lock = threading.RLock()
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
        hop: CycleHop,
        dataset_name: str,
    ) -> Job:
        job_id = secrets.token_urlsafe(12)
        now = utcnow_iso()
        job = Job(
            job_id=job_id,
            user_id=user_id,
            campaign_id=hop.campaign_id,
            cycle_id=hop.cycle_id,
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
        hop: CycleHop = UNRESOLVED_HOP,
    ) -> ReserveResult:
        """Atomically admit a run against ``capacity``, or report the holder: the count read and the
        reservation write take one lock with **no ``await`` between them**, which closes the race."""
        with self._lock:
            running = self.list_running()
            if len(running) >= self._capacity:
                holder = min(running, key=lambda j: j.created_at)
                return ReserveResult(job=None, holder=holder)
            job = self.create(
                user_id=user_id,
                hop=hop,
                dataset_name=dataset_name,
            )
            return ReserveResult(job=job, holder=None)

    def set_caps(self, job_id: str, *, cap_usd: float | None, cap_tokens: int | None) -> None:
        """Stamp what the launch was admitted at, once composed — the reservation the NEXT admission
        counts against, so it must land before the run reaches an LLM."""
        job = self.get(job_id)
        if job is None:
            return
        job.cap_usd = cap_usd
        job.cap_tokens = cap_tokens
        self._persist(job)

    def update_target(self, job_id: str, *, hop: CycleHop) -> None:
        job = self.get(job_id)
        if job is None:
            return
        job.campaign_id = hop.campaign_id
        job.cycle_id = hop.cycle_id
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
        """Self-heal a ``running`` job whose in-process asyncio task is gone — a zombie holding the
        machine slot. ``pending`` is left alone: the reserve→attach window legitimately has no task."""
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
        """The live jobs — a reconciling READ, deliberately: a ``running`` job whose task is gone
        is stamped ``stopped`` here and its cycle reaped, so no caller is answered a zombie. The
        write is bounded to once per zombie (the next call filters it out above the check), and
        this is the only judgment that sees a torn task at all — ``reaper.periodic_sweep`` needs
        900 s of on-disk staleness, which is how long the zombie would hold the machine slot."""
        out: list[Job] = []
        for j in self.list_all(user_id=user_id):
            if j.status not in LIVE_JOB_STATUSES:
                continue
            j = self._reap_if_orphaned(j)
            if j.status in LIVE_JOB_STATUSES:
                out.append(j)
        return out

    def running_job_for(self, hop: CycleHop) -> Job | None:
        """The in-flight job running this cycle — how a mid-flight ceiling change reaches the
        reservation that job holds, which would otherwise keep quoting the launch's number."""
        return next((j for j in self.list_running() if j.hop == hop), None)

    def machine_holder(self, *, exclude_user_id: str) -> Job | None:
        """The oldest still-running job owned by another user — the single "is the machine busy, and by
        whom" query, so the 409 a blocked user gets and the banner everyone sees cannot disagree."""
        others = [j for j in self.list_running() if j.user_id != exclude_user_id]
        if not others:
            return None
        return min(others, key=lambda j: j.created_at)

    def list_created_today(self, *, user_id: str | None = None) -> list[Job]:
        """Daily-campaigns quota probe. The prefix is SLICED off ``utcnow_iso`` rather than composed,
        because ``created_at`` was minted by it and a second spelling of "today" once disagreed."""
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
            cap_usd=_optional_float(raw.get("cap_usd")),
            cap_tokens=_optional_int(raw.get("cap_tokens")),
        )

    def _mark_stale_on_startup(self) -> None:
        now = utcnow_iso()
        for path in self._dir.glob("*.json"):
            try:
                raw = read_json(path)
            except (OSError, JSONDecodeError):
                continue
            if raw.get("status") not in LIVE_JOB_STATUSES:
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
    """A status that did not survive its file reads ``stopped`` — an unreadable job is not one to
    resume. Membership derives from the Literal, so a new status needs no arm here."""
    if isinstance(raw, str) and raw in _JOB_STATUSES:
        return cast(JobStatus, raw)
    return "stopped"


def _optional_str(raw: object) -> str | None:
    if raw is None:
        return None
    return str(raw)


def _optional_float(raw: object) -> float | None:
    return float(raw) if isinstance(raw, int | float) and not isinstance(raw, bool) else None


def _optional_int(raw: object) -> int | None:
    return int(raw) if isinstance(raw, int | float) and not isinstance(raw, bool) else None


__all__ = ["Job", "JobRegistry", "JobStatus", "ReserveResult", "default_jobs_dir"]
