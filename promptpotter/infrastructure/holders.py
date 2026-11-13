"""Who holds a resource. Every holder is a job, alive while it is unreleased and its producer's OS
lock is held; a cycle names its holder by the last `Hold` on its ledger, and its run's own hold is
the cycle lock, the one hold a process outside the registry can take."""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from pydantic import ValidationError

from promptpotter.domain.job import JOB_ADAPTER, Job, ScoringPass
from promptpotter.domain.phases import LaunchStage, RunPhase
from promptpotter.infrastructure import producer_lock
from promptpotter.infrastructure.store.campaign_store.ledger_scan import scan_cycle_hold
from promptpotter.infrastructure.store.io import open_text_robust, validate_path_component
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.shared.errors import (
    FileKind,
    FileUnreadableError,
    NotFoundError,
    validation_reason,
)


def job_path(jobs_dir: Path, job_id: str) -> Path:
    try:
        return jobs_dir / f"{validate_path_component(job_id)}.json"
    except ValueError:
        # Names no file a registry could hold, so no job: refused as any absent one is.
        raise NotFoundError(f"no job {job_id!r}") from None


def read_job(path: Path, staged: bytes | None = None) -> Job | None:
    """``None``: no such job, or one deleted since the listing; a file that does not load raises.
    *staged* reads as the file's bytes."""
    raw: str | bytes
    if staged is not None:
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
        raise FileUnreadableError(
            FileKind.JOB, path=str(path), reason=validation_reason(exc, shown=1)
        ) from exc


def producer_alive(jobs_dir: Path, producer_id: str) -> bool:
    """An unstamped id reads as gone."""
    if not producer_id:
        return False
    try:
        path = producer_lock.producer_path(jobs_dir, producer_id)
    except ValueError:
        return False
    return producer_lock.held(path)


def in_flight(jobs_dir: Path, job: Job) -> bool:
    """THE death rule: a job holds while unreleased and its producer's lock is held, whichever
    process asks; no in-process task stands in for the lock."""
    return not job.released and producer_alive(jobs_dir, job.producer_id)


def hold_released(jobs_dir: Path, hold: str | None) -> bool:
    """The job *hold* names is no longer in flight. A hold no job names (the cycle lock alone)
    ends only at its run boundary's void."""
    if hold is None:
        return False
    job = read_job(job_path(jobs_dir, hold))
    return job is None or not in_flight(jobs_dir, job)


class CycleHolder(NamedTuple):
    # ``None``: the cycle lock is held, and no live job's hold names the cycle.
    job: Job | None
    # Its run declared itself since: the cycle lock is its hold from then on.
    declared: bool

    @property
    def stage(self) -> LaunchStage | None:
        """A launch's, claimed before its run holds the cycle; a pass reads by its lock alone."""
        if self.declared or self.job is None or isinstance(self.job.holder, ScoringPass):
            return None
        return RunPhase.QUEUED if self.job.queued else RunPhase.STARTING

    @property
    def run(self) -> Job | None:
        """The run's job, its spend held: what a ceiling of the cycle's binds."""
        job = self.job
        if job is None or not job.holds_spend or isinstance(job.holder, ScoringPass):
            return None
        return job


def holder_of(jobs_dir: Path, cycle_dir: Path) -> CycleHolder | None:
    """Who holds the cycle: the live job whose hold its ledger names last, until that job's run
    declares itself; from then on, while the cycle lock is held."""
    held = scan_cycle_hold(CycleLayout(cycle_dir).ledger)
    job = None if held is None else read_job(job_path(jobs_dir, held.hold.hold_id))
    job = job if job is not None and in_flight(jobs_dir, job) else None
    if job is not None and held is not None and not held.declared:
        return CycleHolder(job, declared=False)
    return CycleHolder(job, declared=True) if producer_lock.cycle_held(cycle_dir) else None
