"""A run's job released before its release stamped an ending names none, and no longer loads: each
is staged with the lapse its own record says — `refused` where it carries a refusal, and otherwise
`orphaned`, as the registry reads any job no live producer speaks for (`JobRegistry.launched_by`):
a withdrawn, interrupted, swept or finished run left one record shape alike. A scoring pass that
returned names none, as now. Every released record maps, so a landing never waits on a sweep."""

from __future__ import annotations

import json
from pathlib import Path

from promptpotter.config.paths import jobs_dir
from promptpotter.infrastructure.store.data_root import Staged


def staged(projects_root: Path) -> list[Staged]:
    out: list[Staged] = []
    for path in sorted(jobs_dir(projects_root).glob("*.json")):
        job = json.loads(path.read_text(encoding="utf-8"))
        holder = job.get("holder")
        returned_pass = isinstance(holder, dict) and holder.get("kind") == "scoring_pass"
        refused = bool(job.get("refusal"))
        if (
            job.get("released_at") is None
            or job.get("ending") is not None
            or job.get("lapse") is not None
            or (returned_pass and not refused)
        ):
            continue
        job["lapse"] = lapse = "refused" if refused else "orphaned"
        out.append(
            Staged(
                path,
                json.dumps(job, ensure_ascii=False, indent=2).encode("utf-8"),
                f"job {path.stem}: released as {lapse}",
            )
        )
    return out
