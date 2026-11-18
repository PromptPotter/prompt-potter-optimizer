"""`jobs/*.json` as v0.8.20 wrote it, when a panel our bound cut ended its run on `panel_cut`: each
such job ends on `panel_hole`, the one stop of a holed panel. `jobs/ended/` is run data, no survivor."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from promptpotter.config.paths import jobs_dir
from promptpotter.domain.phases import StopReason
from promptpotter.infrastructure.store.data_root import Staged, read_as_staged


def staged(projects_root: Path, before: Mapping[Path, bytes]) -> list[Staged]:
    moved: list[Staged] = []
    for path in sorted(jobs_dir(projects_root).glob("*.json")):
        job = json.loads(read_as_staged(path, before) or b"{}")
        if job.get("ending") == "panel_cut":
            job["ending"] = StopReason.PANEL_HOLE.value
            data = json.dumps(job, indent=2, ensure_ascii=False).encode("utf-8")
            moved.append(Staged(path, data, f"job {path.stem} ends on {StopReason.PANEL_HOLE}"))
    return moved
