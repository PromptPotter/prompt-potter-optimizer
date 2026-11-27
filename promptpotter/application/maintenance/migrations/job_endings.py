"""`jobs/*.json` as v0.8.21 wrote it, with how its run ended: the ending, the step, the lapse and
the refusal go, since its cycle's stop and `jobs/refused/` hold them now. A refusal it held is
wiped with it, as `jobs/ended/` is: run data, no survivor."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from promptpotter.config.paths import jobs_dir
from promptpotter.infrastructure.store.data_root import Staged, read_as_staged

RUN_FIELDS = ("ending", "stepped", "lapse", "refusal")


def staged(projects_root: Path, before: Mapping[Path, bytes]) -> list[Staged]:
    moved: list[Staged] = []
    for path in sorted(jobs_dir(projects_root).glob("*.json")):
        job = json.loads(read_as_staged(path, before) or b"{}")
        if gone := [field for field in RUN_FIELDS if field in job]:
            kept = {key: value for key, value in job.items() if key not in gone}
            data = json.dumps(kept, indent=2, ensure_ascii=False).encode("utf-8")
            moved.append(Staged(path, data, f"job {path.stem} sheds its {', '.join(gone)}"))
    return moved
