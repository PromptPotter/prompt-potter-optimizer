"""A job's refusal is its door's `domain/refusal.py::Refusal`, never its words alone: a job written
before carries a string, `""` where none was refused (every live job, a survivor) and the
exception's words where one was. Each is staged as `job_ending.kept_refusal` keeps it; a job
`job_ending` or `retired_ending` stages carries its refusal there."""

from __future__ import annotations

import json
from pathlib import Path

from promptpotter.application.maintenance.migrations.job_ending import kept_refusal, unended
from promptpotter.application.maintenance.migrations.retired_ending import retired
from promptpotter.config.paths import jobs_dir
from promptpotter.infrastructure.store.data_root import Staged


def staged(projects_root: Path) -> list[Staged]:
    out: list[Staged] = []
    for path in sorted(jobs_dir(projects_root).glob("*.json")):
        job = json.loads(path.read_text(encoding="utf-8"))
        words = job.get("refusal")
        if not isinstance(words, str) or unended(job) or retired(job):
            continue
        job["refusal"] = kept_refusal(words)
        out.append(
            Staged(
                path,
                json.dumps(job, ensure_ascii=False, indent=2).encode("utf-8"),
                f"job {path.stem}: its refusal is a `Refusal`",
            )
        )
    return out
