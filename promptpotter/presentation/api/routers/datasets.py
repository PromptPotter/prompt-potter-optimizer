"""Dataset preview router — hard-sample leaderboard for the New-Job view."""

from __future__ import annotations

import json
import math
import re
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from promptpotter.infrastructure.store import campaign_dir_for
from promptpotter.infrastructure.store.paths import DEFAULT_DATASETS_ROOT
from promptpotter.presentation.api.deps import StoreDep

_datasets_router = APIRouter(prefix="/datasets", tags=["Datasets"])

_DATASET_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


class DatasetItem(BaseModel):
    sample_id: int
    query: str
    ground_truth: str
    task: str | None = None
    n_obs: int = Field(description="Times this sample has been tried")
    surprise: float = Field(
        description=(
            "Miss-probability for an average candidate, derived from Rasch "
            "delta via sigmoid. 0.5 prior for unmeasured samples (no signal "
            "yet — coin flip)."
        ),
    )


class DatasetPreviewResponse(BaseModel):
    name: str
    row_count: int
    train_count: int = Field(description="Items assigned to optimizer training")
    test_count: int = Field(description="Items held out for test evaluation")
    items: list[DatasetItem]


@_datasets_router.get("/{name}/preview", response_model=DatasetPreviewResponse)
async def get_dataset_preview(
    name: str,
    store: StoreDep,
    backend_id: str = Query(default="local"),
    limit: int = Query(default=50, ge=1, le=1000),
    scope: Literal["workspace", "campaign"] = Query(
        default="workspace",
        description=(
            "workspace = cross-cycle Rasch over the whole MeasurementArchive. "
            "campaign = single-cycle Rasch (requires cycle_id)."
        ),
    ),
    cycle_id: str | None = Query(
        default=None,
        description="Required when scope=campaign; ignored when scope=workspace.",
    ),
) -> DatasetPreviewResponse:
    """Hard-sample leaderboard for ``{name}`` — every dataset row in Rasch
    difficulty order (hardest first), with unmeasured samples appended at the
    bottom in ``sample_id`` order.

    Two scopes:

    - ``workspace`` (default): cross-cycle Rasch fit over the whole
      ``MeasurementArchive`` for ``backend_id``.
    - ``campaign``: per-cycle fit, read from
      ``campaigns/{cycle_id}/hard_samples_campaign.json``. Reflects only the
      cycle's own observations — useful for "what does THIS run look like?"

    ``train_count`` = samples with at least one measurement in the selected
    scope. ``test_count`` = samples in the dataset cache that have none.
    """
    from promptpotter.application.intelligence.hard_sample_archive import (
        build_archive_hard_samples_artifact,
    )

    if not _DATASET_NAME_RE.match(name):
        raise HTTPException(400, "Invalid dataset name")
    datasets_root = DEFAULT_DATASETS_ROOT.resolve()
    cache_path = (datasets_root / name / "cache.json").resolve()
    if not cache_path.is_relative_to(datasets_root):
        raise HTTPException(400, "Invalid dataset name")
    if not cache_path.is_file():
        raise HTTPException(404, f"Dataset '{name}' not found")
    raw = json.loads(cache_path.read_text(encoding="utf-8"))

    # Sample-id keying varies on disk: canonical datasets use ``id``; BBEH's
    # HF processor emits ``sample_id``. Normalise to int at the read boundary.
    sample_lookup: dict[int, dict[str, Any]] = {}
    for item in raw["items"]:
        sid = int(item["sample_id"] if "sample_id" in item else item["id"])
        sample_lookup[sid] = item

    if scope == "campaign":
        if not cycle_id:
            raise HTTPException(400, "scope=campaign requires cycle_id")
        cycle_dir = campaign_dir_for(store.base_dir, cycle_id)
        if not cycle_dir.exists():
            raise HTTPException(404, f"Cycle '{cycle_id}' not found")
        path = cycle_dir / "hard_samples_campaign.json"
        if not path.is_file():
            raise HTTPException(
                404, "hard_samples_campaign.json not present (cycle has no rounds yet)"
            )
        artifact = json.loads(path.read_text(encoding="utf-8"))
    else:
        artifact = build_archive_hard_samples_artifact(store, backend_id, top_k_samples=None)
    rasch = artifact.get("rasch", {})
    delta_map: dict[int, float] = {int(k): float(v) for k, v in rasch.get("delta", {}).items()}
    n_obs_map: dict[int, int] = {
        int(k): int(v) for k, v in rasch.get("n_obs_per_sample", {}).items()
    }
    measured = {sid for sid in delta_map if sid in sample_lookup}

    # Surprise = miss-probability for an average candidate (theta=0), via
    # sigmoid(delta). Unmeasured samples get the 0.5 prior (no signal yet —
    # genuinely a coin flip for the optimizer).
    def surprise_of(sid: int) -> float:
        if sid in delta_map:
            return 1.0 / (1.0 + math.exp(-delta_map[sid]))
        return 0.5

    full_order = sorted(sample_lookup.keys(), key=lambda s: (-surprise_of(s), s))

    items = [
        DatasetItem(
            sample_id=sid,
            query=sample_lookup[sid]["query"],
            ground_truth=sample_lookup[sid]["ground_truth"],
            task=sample_lookup[sid].get("task"),
            n_obs=n_obs_map.get(sid, 0),
            surprise=surprise_of(sid),
        )
        for sid in full_order[:limit]
    ]

    return DatasetPreviewResponse(
        name=raw["name"],
        row_count=len(sample_lookup),
        train_count=len(measured),
        test_count=len(sample_lookup) - len(measured),
        items=items,
    )


__all__ = ["DatasetItem", "DatasetPreviewResponse"]
