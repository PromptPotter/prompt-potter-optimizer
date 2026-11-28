"""`jobs/*.json` as v0.8.21 wrote it, its cycle as two strings, empty until its LAND. Re-held as
`Job.hop`, ``null`` until then."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from promptpotter.config.paths import jobs_dir
from promptpotter.infrastructure.store.data_root import Staged, read_as_staged


def staged(projects_root: Path, before: Mapping[Path, bytes]) -> list[Staged]:
    moved: list[Staged] = []
    for path in sorted(jobs_dir(projects_root).glob("*.json")):
        job = json.loads(read_as_staged(path, before) or b"{}")
        if "campaign_id" not in job:
            continue
        campaign_id, cycle_id = job.pop("campaign_id"), job.pop("cycle_id", "")
        hop = {"campaign_id": campaign_id, "cycle_id": cycle_id} if campaign_id else None
        data = json.dumps(job | {"hop": hop}, indent=2, ensure_ascii=False).encode("utf-8")
        moved.append(Staged(path, data, f"job {path.stem} holds its cycle as one hop"))
    return moved
