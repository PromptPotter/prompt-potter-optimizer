"""Invariant: dataset pipeline.json node configs MUST NOT ship numeric max_tokens defaults.

Rationale: provider ceilings apply when max_tokens is unset. Baking a small numeric default
re-introduces the BBEH-style reasoning_budget_exhausted trap. Operators raise caps via
campaign.json overrides, not by mutating dataset defaults.
"""

from __future__ import annotations

import json
from pathlib import Path


def test_no_numeric_max_tokens_in_dataset_pipeline_configs() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    pipeline_files = sorted((repo_root / "datasets").glob("*/pipeline.json"))
    assert pipeline_files, "no datasets/*/pipeline.json found — wrong cwd?"

    offenders: list[str] = []
    for path in pipeline_files:
        spec = json.loads(path.read_text(encoding="utf-8"))
        for node_name, node in (spec.get("nodes") or {}).items():
            mt = (node.get("config") or {}).get("max_tokens", "absent")
            if isinstance(mt, int):
                offenders.append(f"{path.relative_to(repo_root)} :: {node_name} = {mt}")

    assert not offenders, (
        "Numeric max_tokens default(s) snuck into dataset pipeline.json node configs:\n  "
        + "\n  ".join(offenders)
        + "\nUse `null` or omit the field; operators override per-cycle via campaign.json."
    )
