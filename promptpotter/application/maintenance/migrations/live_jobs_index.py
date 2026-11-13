"""The jobs dir before the live-jobs index: each unreleased job's id, so a run in flight across the
landing still holds its slot. A job file that does not load refuses the landing on its own."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from promptpotter.config.paths import LIVE_JOBS_INDEX, jobs_dir
from promptpotter.infrastructure.holders import read_job
from promptpotter.infrastructure.store.data_root import Staged, read_as_staged
from promptpotter.shared.errors import FileUnreadableError


def staged(projects_root: Path, before: Mapping[Path, bytes]) -> list[Staged]:
    jobs = jobs_dir(projects_root)
    index = jobs / LIVE_JOBS_INDEX
    if read_as_staged(index, before) is not None or not jobs.is_dir():
        return []
    live: list[str] = []
    for path in sorted(jobs.glob("*.json")):
        try:
            job = read_job(path, before.get(path))
        except FileUnreadableError:
            continue
        if job is not None and not job.released:
            live.append(job.job_id)
    data = json.dumps(live, indent=2).encode("utf-8")
    return [Staged(index, data, f"{len(live)} live job(s) indexed")]
