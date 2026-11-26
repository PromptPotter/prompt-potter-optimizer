"""`jobs/*.json` as v0.8.20 wrote it, ended on a stop this code no longer names: each lands on the stop
that replaced it. `jobs/ended/` is run data, no survivor."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from promptpotter.config.paths import jobs_dir
from promptpotter.domain.phases import StopReason
from promptpotter.infrastructure.store.data_root import Staged, read_as_staged

# A panel our bound cut is a holed panel's one stop; `converged` was the bench's word for the
# optimizer's own successful search end.
REPLACED = {"panel_cut": StopReason.PANEL_HOLE, "converged": StopReason.OPTIMIZER_EXHAUSTED}


def staged(projects_root: Path, before: Mapping[Path, bytes]) -> list[Staged]:
    moved: list[Staged] = []
    for path in sorted(jobs_dir(projects_root).glob("*.json")):
        job = json.loads(read_as_staged(path, before) or b"{}")
        if (now := REPLACED.get(job.get("ending"))) is not None:
            job["ending"] = now.value
            data = json.dumps(job, indent=2, ensure_ascii=False).encode("utf-8")
            moved.append(Staged(path, data, f"job {path.stem} ends on {now}"))
    return moved
