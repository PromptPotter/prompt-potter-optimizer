"""Dataset preview router — hard-sample leaderboard for the New-Job view."""

from __future__ import annotations

import json
import math
import re
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.infrastructure.store import campaign_dir_for
from promptpotter.infrastructure.store.archive_views import (
    measurement_series_for_samples,
)
from promptpotter.infrastructure.store.paths import DEFAULT_DATASETS_ROOT
from promptpotter.presentation.api.deps import StoreDep

_datasets_router = APIRouter(prefix="/datasets", tags=["Datasets"])

_DATASET_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def _trim_unmeasured(
    order: list[int],
    measured: set[int],
    cap: int,
) -> list[int]:
    """Walk *order*, keep every measured sample, keep at most *cap* unmeasured.

    Preserves the sort position of the unmeasured samples that survive — the
    cap drops the tail of the contiguous unmeasured block, not a random
    slice. With *cap* = 0 the unmeasured rows vanish entirely; with
    *cap* >= ``len(order)`` the function is a no-op.
    """
    kept_unmeasured = 0
    out: list[int] = []
    for sid in order:
        if sid in measured:
            out.append(sid)
        elif kept_unmeasured < cap:
            out.append(sid)
            kept_unmeasured += 1
    return out


class DatasetItem(BaseModel):
    sample_id: int
    query: str
    ground_truth: str
    task: str | None = None
    n_obs: int = Field(description="Times this sample has been tried")
    miss_prob: float = Field(
        description=(
            "Miss-probability for an average candidate (θ=0), derived from "
            "Rasch δ_s via sigmoid. 0.5 prior for unmeasured samples (no "
            "signal yet — coin flip). Drives the static-mode sort."
        ),
    )
    pick_score: float | None = Field(
        default=None,
        description=(
            "Expected information gain of measuring this sample on a "
            "brand-new candidate (ability prior N(0, sigma_theta^2)). Reads "
            "the Rasch delta_s standard error, so a barely-measured sample "
            "scores high — there is more to learn. The live picker "
            "(``adaptive_picker``) re-evaluates it per step against the "
            "candidate's running θ̂_c posterior. None when the sample has "
            "no measurement yet."
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
    max_unmeasured: int = Query(
        default=20,
        ge=0,
        le=1000,
        description=(
            "Cap on unmeasured (miss_prob=0.5 prior) samples retained in the "
            "Rasch-sorted output. Measured samples are always kept; the "
            "unmeasured tail is truncated past this count so the heat-map "
            "doesn't drown signal in 'no evidence yet' rows."
        ),
    ),
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
    Both counts always reflect the FULL dataset — ``max_unmeasured`` only
    trims the rows returned in ``items``, not the totals.
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
        # Scope=archive is the per-dataset workspace view; cross-dataset
        # pooling on sample_id would be meaningless. ``name`` is the
        # dataset name from the URL path.
        artifact = build_archive_hard_samples_artifact(
            store, backend_id, dataset_name=name, top_k_samples=None
        )
    rasch = artifact.get("rasch", {})
    delta_map: dict[int, float] = {int(k): float(v) for k, v in rasch.get("delta", {}).items()}
    n_obs_map: dict[int, int] = {
        int(k): int(v) for k, v in rasch.get("n_obs_per_sample", {}).items()
    }
    # Pick-score is the expected information gain of measuring each sample
    # on a brand-new candidate — populated for both the per-cycle and the
    # cross-cycle archive artifact.
    pick_score_block = artifact.get("pick_score", {}).get("per_sample", {})
    pick_score_map: dict[int, float] = {int(k): float(v) for k, v in pick_score_block.items()}
    measured = {sid for sid in delta_map if sid in sample_lookup}

    # Miss-prob = miss-probability for an average candidate (theta=0), via
    # sigmoid(delta). Unmeasured samples get the 0.5 prior (no signal yet —
    # genuinely a coin flip for the optimizer).
    def miss_prob_of(sid: int) -> float:
        if sid in delta_map:
            return 1.0 / (1.0 + math.exp(-delta_map[sid]))
        return 0.5

    full_order = sorted(sample_lookup.keys(), key=lambda s: (-miss_prob_of(s), s))
    trimmed_order = _trim_unmeasured(full_order, measured, max_unmeasured)

    items = [
        DatasetItem(
            sample_id=sid,
            query=sample_lookup[sid]["query"],
            ground_truth=sample_lookup[sid]["ground_truth"],
            task=sample_lookup[sid].get("task"),
            n_obs=n_obs_map.get(sid, 0),
            miss_prob=miss_prob_of(sid),
            pick_score=pick_score_map.get(sid),
        )
        for sid in trimmed_order[:limit]
    ]

    return DatasetPreviewResponse(
        name=raw["name"],
        row_count=len(sample_lookup),
        train_count=len(measured),
        test_count=len(sample_lookup) - len(measured),
        items=items,
    )


class MeasurementDot(BaseModel):
    ord: str = Field(
        description=(
            "Stable composite ordinal — opaque string the client only uses "
            "for lexicographic sort + uniqueness. Workspace scope encodes "
            "created_at + run_id + index; campaign scope encodes round + "
            "scoreboard idx."
        ),
    )
    hit: bool
    label: str = Field(description="Short human label, e.g. 'R3 cand 2'.")


class SampleSeries(BaseModel):
    sample_id: int
    measurements: list[MeasurementDot]


class MeasurementSeriesResponse(BaseModel):
    name: str
    scope: Literal["workspace", "campaign"]
    items: list[SampleSeries]


def _campaign_series(
    store: Any, cycle_id: str, sample_ids: set[int]
) -> dict[int, list[dict[str, Any]]]:
    """Walk ``rounds/round_*.json`` for *cycle_id*, returning per-sample series.

    Ord = ``{round:04d}/{cand_idx:02d}`` so series sort chronologically by
    round then by scoreboard position within the round. Candidates not
    appearing in the round's scoreboard sink to slot 99.
    """
    cycle_dir = campaign_dir_for(store.base_dir, cycle_id)
    rounds_dir = cycle_dir / "rounds"
    out: dict[int, list[dict[str, Any]]] = {sid: [] for sid in sample_ids}
    if not rounds_dir.is_dir():
        return out
    for round_path in sorted(rounds_dir.glob("round_*.json")):
        try:
            doc = json.loads(round_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        round_no = doc.get("round")
        if not isinstance(round_no, int):
            continue
        idx_of: dict[str, int] = {}
        for i, c in enumerate(doc.get("scoreboard") or []):
            cid = c.get("candidate_id") if isinstance(c, dict) else None
            if isinstance(cid, str):
                idx_of[cid] = i
        acr = doc.get("all_candidate_results") or {}
        if not isinstance(acr, dict):
            continue
        for cand_id, results in acr.items():
            ci = idx_of.get(cand_id, 99)
            if not isinstance(results, list):
                continue
            for item in results:
                if not isinstance(item, dict):
                    continue
                sid = item.get("sample_id")
                hit = item.get("hit")
                if not isinstance(sid, int) or not isinstance(hit, bool):
                    continue
                if sid not in sample_ids:
                    continue
                out[sid].append(
                    {
                        "ord": f"{round_no:04d}/{ci:02d}",
                        "hit": hit,
                        "label": f"R{round_no} cand {ci}",
                    }
                )
    for bucket in out.values():
        bucket.sort(key=lambda m: m["ord"])
    return out


@_datasets_router.get(
    "/{name}/measurement-series",
    response_model=MeasurementSeriesResponse,
)
async def get_dataset_measurement_series(
    name: str,
    store: StoreDep,
    backend_id: str = Query(default="local"),
    limit: int = Query(default=50, ge=1, le=1000),
    max_unmeasured: int = Query(
        default=20,
        ge=0,
        le=1000,
        description=(
            "Mirrors ``/preview``: cap on unmeasured rows kept in the "
            "Rasch-sorted output. Must match the value the client passed "
            "to ``/preview`` so the two responses stay aligned by index."
        ),
    ),
    scope: Literal["workspace", "campaign"] = Query(
        default="workspace",
        description=(
            "workspace = cross-cycle MeasurementArchive series per sample. "
            "campaign = walk this cycle's round files."
        ),
    ),
    cycle_id: str | None = Query(
        default=None,
        description="Required when scope=campaign; ignored when scope=workspace.",
    ),
) -> MeasurementSeriesResponse:
    """Per-sample chronological measurement series feeding the hard-sample
    leaderboard's Meas heat-map column.

    Returns measurements for the same top-``limit`` samples (in the same
    Rasch-difficulty order) as ``/preview`` — clients can zip the two
    responses by ``sample_id`` without re-sorting. ``ord`` is opaque and
    only used for the roster's left-to-right alignment across rows.
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
        artifact = build_archive_hard_samples_artifact(
            store, backend_id, dataset_name=name, top_k_samples=None
        )
    rasch = artifact.get("rasch", {})
    delta_map: dict[int, float] = {int(k): float(v) for k, v in rasch.get("delta", {}).items()}

    def miss_prob_of(sid: int) -> float:
        if sid in delta_map:
            return 1.0 / (1.0 + math.exp(-delta_map[sid]))
        return 0.5

    full_order = sorted(sample_lookup.keys(), key=lambda s: (-miss_prob_of(s), s))
    measured = {sid for sid in delta_map if sid in sample_lookup}
    trimmed_order = _trim_unmeasured(full_order, measured, max_unmeasured)
    selected = trimmed_order[:limit]
    selected_set = set(selected)

    if scope == "campaign":
        assert cycle_id is not None  # checked above; appeases mypy
        series = _campaign_series(store, cycle_id, selected_set)
    else:
        raw_series = measurement_series_for_samples(store, backend_id, selected)
        # Workspace ord already carries timestamp + run_id + idx; label is a
        # short "run abbrev" — first 8 chars of run_id is plenty for tooltips.
        series = {
            sid: [
                {
                    "ord": m["ord"],
                    "hit": m["hit"],
                    "label": f"run {str(m.get('run_id', ''))[:8]}",
                }
                for m in ms
            ]
            for sid, ms in raw_series.items()
        }

    items = [
        SampleSeries(
            sample_id=sid,
            measurements=[MeasurementDot(**m) for m in series.get(sid, [])],
        )
        for sid in selected
    ]
    return MeasurementSeriesResponse(name=raw["name"], scope=scope, items=items)


class DatasetPipelineResponse(BaseModel):
    """Target pipeline view for a dataset overlay.

    The dataset's ``pipeline.json`` is the source of truth for which nodes
    actually run for a campaign — different datasets sharing one backend
    (e.g. JustLogic ⇒ ``llm_only`` only, lca-termnorm ⇒ the full 6-node
    chain, both via the ``termnorm`` backend) carry distinct
    ``pipelines.default`` lists. The webapp consumes ``view`` to render the
    chat-pane hero; ``pipeline`` carries the full parsed schema for
    consumers that need per-node config (kind, model, params).
    ``connector`` is the original-cased connector name from the overlay
    (e.g. "TermNorm Local") for elegant chip labelling.
    """

    name: str
    connector: str
    pipeline: dict[str, Any]
    view: dict[str, Any] | None


def _strip_lone_surrogates(obj: Any) -> Any:
    """Recursively replace unpaired surrogate codepoints with ``?``.

    Some dataset overlays (notably ``lca-termnorm/pipeline.json``) carry
    description strings whose JSON escape sequences point at lone low
    surrogates (e.g. ``\\udc9d``). Those codepoints are valid Python strings
    but cannot encode to UTF-8 when FastAPI serializes the response — they
    raise ``UnicodeEncodeError`` at the wire. Strip them at the boundary
    so the rest of the (well-formed) payload still gets out the door.
    """
    if isinstance(obj, str):
        return obj.encode("utf-8", errors="replace").decode("utf-8")
    if isinstance(obj, dict):
        return {k: _strip_lone_surrogates(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_strip_lone_surrogates(v) for v in obj]
    return obj


@_datasets_router.get("/{name}/pipeline", response_model=DatasetPipelineResponse)
async def get_dataset_pipeline(name: str) -> DatasetPipelineResponse:
    """Return the dataset overlay's parsed pipeline schema and graph view."""
    if not _DATASET_NAME_RE.match(name):
        raise HTTPException(400, "Invalid dataset name")
    datasets_root = DEFAULT_DATASETS_ROOT.resolve()
    pipeline_path = (datasets_root / name / "pipeline.json").resolve()
    if not pipeline_path.is_relative_to(datasets_root):
        raise HTTPException(400, "Invalid dataset name")
    if not pipeline_path.is_file():
        raise HTTPException(404, f"Dataset '{name}' has no pipeline.json")
    raw = json.loads(pipeline_path.read_text(encoding="utf-8"))
    schema = parse_pipeline_response(raw)
    pipeline_dump = _strip_lone_surrogates(schema.model_dump(by_alias=True))
    view_dump = (
        _strip_lone_surrogates(schema.view.model_dump(by_alias=True))
        if schema.view is not None
        else None
    )
    connector = (raw.get("backend_name") or raw.get("name") or name).strip()
    return DatasetPipelineResponse(
        name=name,
        connector=connector,
        pipeline=pipeline_dump,
        view=view_dump,
    )


__all__ = [
    "DatasetItem",
    "DatasetPipelineResponse",
    "DatasetPreviewResponse",
    "MeasurementDot",
    "MeasurementSeriesResponse",
    "SampleSeries",
]
