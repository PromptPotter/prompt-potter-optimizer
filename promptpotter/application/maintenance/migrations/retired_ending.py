"""A job that stopped on a retired ending no longer loads: each is staged with the stop that took its place."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from promptpotter.application.maintenance.migrations.job_ending import kept_refusal
from promptpotter.config.paths import jobs_dir
from promptpotter.domain.phases import StopReason
from promptpotter.infrastructure.store.data_root import Staged

# `perfect_score` went for `halt_at_fitness`, the one ceiling stop: a run that returned on it hit
# its target.
RETIRED_ENDINGS = {"perfect_score": StopReason.TARGET_HIT.value}


def retired(job: dict[str, Any]) -> bool:
    """A job naming a retired ending, which this migration stages."""
    return job.get("ending") in RETIRED_ENDINGS


def staged(projects_root: Path) -> list[Staged]:
    out: list[Staged] = []
    for path in sorted(jobs_dir(projects_root).glob("*.json")):
        job = json.loads(path.read_text(encoding="utf-8"))
        if not retired(job):
            continue
        job["ending"] = ending = RETIRED_ENDINGS[job["ending"]]
        if isinstance(words := job.get("refusal"), str):
            job["refusal"] = kept_refusal(words)
        out.append(
            Staged(
                path,
                json.dumps(job, ensure_ascii=False, indent=2).encode("utf-8"),
                f"job {path.stem}: ended as {ending}",
            )
        )
    return out
