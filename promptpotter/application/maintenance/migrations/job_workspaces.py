"""`jobs/*.json` as v0.8.21 wrote it, its `workspace` spelled from a data root neither resolved nor
in Windows' long form. Re-spelled as `config/paths.py::user_data_root` spells it now, so a run in
flight across the landing still matches its campaign (`Job.on_campaign` compares the string)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from promptpotter.config.paths import jobs_dir, long_form
from promptpotter.infrastructure.store.data_root import Staged, read_as_staged


def staged(projects_root: Path, before: Mapping[Path, bytes]) -> list[Staged]:
    moved: list[Staged] = []
    for path in sorted(jobs_dir(projects_root).glob("*.json")):
        job = json.loads(read_as_staged(path, before) or b"{}")
        held = job.get("workspace")
        if isinstance(held, str) and (spelled := long_form(Path(held).resolve())) != held:
            data = json.dumps(job | {"workspace": spelled}, indent=2, ensure_ascii=False)
            moved.append(
                Staged(path, data.encode("utf-8"), f"job {path.stem} re-spells its workspace")
            )
    return moved
