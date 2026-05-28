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
import json
import logging
import secrets
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

from promptpotter.infrastructure.store.paths import DEFAULT_PROJECTS_ROOT

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


def default_jobs_dir() -> Path:
    """Jobs dir sibling of the default `projects/` root."""
    return DEFAULT_PROJECTS_ROOT.parent / "jobs"


class JobRegistry:
    """Process-wide job tracker. Thread-safe; persists per-job state on every status change."""

    def __init__(self, jobs_dir: Path) -> None:
        self._dir = jobs_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._mark_stale_on_startup()

    def create(
        self,
        *,
        user_id: str,
        campaign_id: str,
        cycle_id: str,
        dataset_name: str,
    ) -> Job:
        job_id = secrets.token_urlsafe(12)
        now = datetime.now(UTC).isoformat()
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

    def attach_task(self, job_id: str, task: asyncio.Task[None]) -> None:
        with self._lock:
            self._tasks[job_id] = task

    def mark_started(self, job_id: str) -> None:
        job = self.get(job_id)
        if job is None:
            return
        job.status = "running"
        job.started_at = datetime.now(UTC).isoformat()
        self._persist(job)

    def mark_finished(
        self, job_id: str, *, status: JobStatus, stop_reason: str | None = None
    ) -> None:
        job = self.get(job_id)
        if job is None:
            return
        job.status = status
        job.finished_at = datetime.now(UTC).isoformat()
        job.stop_reason = stop_reason
        self._persist(job)
        with self._lock:
            self._tasks.pop(job_id, None)

    def get(self, job_id: str) -> Job | None:
        path = self._path(job_id)
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("job %s file unreadable", job_id)
            return None
        return self._job_from_dict(raw)

    def list_all(self, *, user_id: str | None = None) -> list[Job]:
        out: list[Job] = []
        for path in self._dir.glob("*.json"):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            job = self._job_from_dict(raw)
            if user_id is not None and job.user_id != user_id:
                continue
            out.append(job)
        out.sort(key=lambda j: j.created_at, reverse=True)
        return out

    def list_running(self, *, user_id: str | None = None) -> list[Job]:
        return [j for j in self.list_all(user_id=user_id) if j.status in ("pending", "running")]

    def list_created_today(self, *, user_id: str | None = None) -> list[Job]:
        """Daily-campaigns quota probe — jobs created since UTC midnight."""
        today = date.today().isoformat()
        return [j for j in self.list_all(user_id=user_id) if j.created_at.startswith(today)]

    def _persist(self, job: Job) -> None:
        tmp = self._path(job.job_id).with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(job), indent=2), encoding="utf-8")
        tmp.replace(self._path(job.job_id))

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
        now = datetime.now(UTC).isoformat()
        for path in self._dir.glob("*.json"):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            status = raw.get("status")
            if status not in ("pending", "running"):
                continue
            raw["status"] = "stopped"
            raw["finished_at"] = now
            raw["stop_reason"] = "server_restart"
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(raw, indent=2), encoding="utf-8")
            tmp.replace(path)
            logger.info("marked stale job %s as stopped (server_restart)", path.stem)


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


__all__ = ["Job", "JobRegistry", "JobStatus", "default_jobs_dir"]
