"""Dataset preview router — hard-sample leaderboard for the New-Job view."""

from __future__ import annotations

import json
import math
import re
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.infrastructure.store import campaign_root_dir_for, cycle_dir_for
from promptpotter.infrastructure.store.archive_views import (
    measurement_series_for_samples,
)
from promptpotter.infrastructure.store.paths import DEFAULT_DATASETS_ROOT
from promptpotter.presentation.api.deps import StoreDep

_datasets_router = APIRouter(prefix="/datasets", tags=["Datasets"])

_DATASET_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

# Three named data scopes — same vocabulary as the heatmap artifacts and the
# webapp toggle. ``cycle`` = one cycle's own Rasch fit; ``campaign`` = the
# campaign's pooled fit; ``dataset`` = the cross-campaign archive snapshot.
# A workspace-scope heatmap is meaningless (samples differ per dataset), so
# the heatmap tier stops at ``dataset``.
HeatmapScope = Literal["cycle", "campaign", "dataset"]


def _resolve_scope_artifact(
    store: Any,
    *,
    scope: HeatmapScope,
    name: str,
    backend_id: str,
    campaign_id: str | None,
    cycle_id: str | None,
) -> dict[str, Any]:
    """Resolve the hard-samples artifact for the requested data scope.

    - ``cycle``: ``campaigns/{campaign_id}/cycles/{cycle_id}/hard_samples.json``
      — needs both ids.
    - ``campaign``: ``campaigns/{campaign_id}/hard_samples.json`` — campaign-
      pooled fit over all the campaign's cycles.
    - ``dataset``: the cross-campaign archive snapshot for ``name``.
    """
    from promptpotter.application.intelligence.hard_sample_archive import (
        build_archive_hard_samples_artifact,
    )

    # A missing ``hard_samples.json`` is a normal empty state — a campaign /
    # cycle that hasn't produced a heatmap yet (no rounds, or migrated from
    # an older layout). Return an empty artifact so the heatmap renders a
    # clean "no data" panel; a 404 here would surface as a console error and
    # read as breakage. A genuinely-absent dir is still a real 404.
    if scope == "cycle":
        if not campaign_id or not cycle_id:
            raise HTTPException(400, "scope=cycle requires campaign_id and cycle_id")
        cycle_dir = cycle_dir_for(store.base_dir, campaign_id, cycle_id)
        if not cycle_dir.exists():
            raise HTTPException(404, f"Cycle '{campaign_id}/{cycle_id}' not found")
        path = cycle_dir / "hard_samples.json"
        if not path.is_file():
            return {}
        cycle_artifact: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return cycle_artifact
    if scope == "campaign":
        if not campaign_id:
            raise HTTPException(400, "scope=campaign requires campaign_id")
        campaign_dir = campaign_root_dir_for(store.base_dir, campaign_id)
        if not campaign_dir.exists():
            raise HTTPException(404, f"Campaign '{campaign_id}' not found")
        path = campaign_dir / "hard_samples.json"
        if not path.is_file():
            return {}
        campaign_artifact: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return campaign_artifact
    # scope == "dataset" — cross-campaign archive snapshot. Cross-dataset
    # pooling would be meaningless, so this is always per-dataset.
    return build_archive_hard_samples_artifact(
        store,
        backend_id,
        dataset_name=name,
        top_k_samples=None,
    )


def _trim_unmeasured(
    order: list[int],
    measured: set[int],
    cap: int | None,
) -> list[int]:
    """Walk *order*, keep every measured sample, keep at most *cap* unmeasured.

    Preserves the sort position of the unmeasured samples that survive — the
    cap drops the tail of the contiguous unmeasured block, not a random
    slice. *cap* = 0 drops every unmeasured row; *cap* = None (default) is
    a no-op.
    """
    if cap is None:
        return list(order)
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
    pick_score: float | None = Field(
        default=None,
        description=(
            "The sample picker's blended objective for measuring this sample "
            "on a brand-new candidate (ability prior N(0, sigma_theta^2)) "
            "against the best fitted candidate: decision-information-gain "
            "plus a small explore term. The live picker (``adaptive_picker``) "
            "re-evaluates it per step against the candidate's running θ̂_c "
            "posterior. None when the sample has no measurement yet."
        ),
    )
    delta: float | None = Field(
        default=None,
        description=(
            "Rasch difficulty δ_s — higher means harder for an average "
            "candidate. None when the sample has no measurement yet."
        ),
    )
    delta_se: float | None = Field(
        default=None,
        description=(
            "Standard error of the Rasch difficulty δ_s — how well "
            "characterized the sample is; large means barely measured. "
            "None when the sample has no measurement yet."
        ),
    )
    p_hat: float | None = Field(
        default=None,
        description=(
            "Marginal hit probability the seed-centred decision-IG reads on "
            "this sample: ``sigmoid((theta_seed - delta_s) / "
            "sqrt(1 + pi * (sigma_theta**2 + se_delta_s**2) / 8))``. Close to "
            "0.5 = contested at the seed's ability; close to 0 or 1 = "
            "predictable. Explains why a low-hit-rate sample can still rank "
            "high on Info gain (tight ``se_delta_s`` from EB on a boundary "
            "sample). None when the sample has no measurement yet."
        ),
    )


class DatasetPreviewResponse(BaseModel):
    name: str
    row_count: int
    split_train: int | None = Field(
        default=None,
        description="Declared training-bank fold size from campaign.json "
        "(datasets/{name}/campaign.json::campaign_config.dataset_split). "
        "None when the dataset declares no split.",
    )
    split_test: int | None = Field(
        default=None,
        description="Declared held-out test fold size — not in the bank or "
        "the preview's items. None when the dataset declares no split.",
    )
    items: list[DatasetItem]


@_datasets_router.get("/{name}/preview", response_model=DatasetPreviewResponse)
async def get_dataset_preview(
    name: str,
    store: StoreDep,
    backend_id: str = Query(default="local"),
    limit: int = Query(default=50, ge=1, le=1000),
    max_unmeasured: int | None = Query(
        default=None,
        ge=0,
        le=1000,
        description=(
            "Optional cap on unmeasured samples (no Rasch δ_s yet) retained "
            "in the Rasch-sorted output. None (default) = no trim — every "
            "dataset-cache row is eligible, bounded only by ``limit``. Set a "
            "positive cap to truncate the unmeasured tail past that count."
        ),
    ),
    scope: Literal["cycle", "campaign", "dataset"] = Query(
        default="dataset",
        description=(
            "dataset = cross-campaign Rasch over the whole MeasurementArchive "
            "for this dataset. campaign = the campaign's pooled fit (requires "
            "campaign_id). cycle = one cycle's own fit (requires campaign_id "
            "and cycle_id)."
        ),
    ),
    campaign_id: str | None = Query(
        default=None,
        description="Required when scope is campaign or cycle.",
    ),
    cycle_id: str | None = Query(
        default=None,
        description="Required when scope=cycle; ignored otherwise.",
    ),
) -> DatasetPreviewResponse:
    """Hard-sample leaderboard for ``{name}`` — every dataset row in Rasch
    difficulty order (hardest first), with unmeasured samples appended at the
    bottom in ``sample_id`` order.

    Three data scopes:

    - ``dataset`` (default): cross-campaign Rasch fit over the whole
      ``MeasurementArchive`` for ``backend_id``.
    - ``campaign``: the campaign's pooled fit, from
      ``campaigns/{campaign_id}/hard_samples.json``.
    - ``cycle``: one cycle's own fit, from
      ``campaigns/{campaign_id}/cycles/{cycle_id}/hard_samples.json``.

    Clients derive measured/unmeasured counts from the companion
    ``/measurement-series`` response — counting samples whose series carries
    at least one dot. That is the only honest definition of "measured" in
    the selected scope: Rasch δ entries persist across rounds and inherit
    from parent fits, so they overcount.
    """
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

    artifact = _resolve_scope_artifact(
        store,
        scope=scope,
        name=name,
        backend_id=backend_id,
        campaign_id=campaign_id,
        cycle_id=cycle_id,
    )
    rasch = artifact.get("rasch", {})
    delta_map: dict[int, float] = {int(k): float(v) for k, v in rasch.get("delta", {}).items()}
    delta_se_map: dict[int, float] = {
        int(k): float(v) for k, v in rasch.get("delta_se", {}).items()
    }
    n_obs_map: dict[int, int] = {
        int(k): int(v) for k, v in rasch.get("n_obs_per_sample", {}).items()
    }
    # Pick-score is the picker's blended objective for measuring each sample
    # on a brand-new candidate — populated for both the per-cycle and the
    # cross-cycle archive artifact.
    pick_score_block = artifact.get("pick_score", {}).get("per_sample", {})
    pick_score_map: dict[int, float] = {int(k): float(v) for k, v in pick_score_block.items()}

    # Marginal hit probability ``p_hat`` per sample — what the seed-centred
    # decision-IG actually reads. ``σ((θ_seed − δ_s) / scale)`` where
    # ``scale = √(1 + π·(σ_θ² + se_δ_s²) / 8)``. Mirrors the math in
    # ``application/intelligence/adaptive_picker._sigmoid`` /
    # ``decision_information_gain`` so the column is the same number the
    # picker sees. None when the sample's δ_s is undefined.
    sigma_theta = float(rasch.get("sigma_theta", 0.0))
    theta_map: dict[str, float] = {str(k): float(v) for k, v in rasch.get("theta", {}).items()}
    candidate_order_raw = artifact.get("candidate_order") or []
    seed_theta = (
        theta_map.get(str(candidate_order_raw[0]), 0.0)
        if candidate_order_raw and theta_map
        else 0.0
    )
    probit_scale = math.pi / 8.0
    var_c = sigma_theta**2  # brand-new candidate's ability prior variance

    def _p_hat(sid: int) -> float | None:
        if sid not in delta_map:
            return None
        delta_s = delta_map[sid]
        se_delta_s = delta_se_map.get(sid, 0.0)
        v = var_c + se_delta_s * se_delta_s
        scale = math.sqrt(1.0 + probit_scale * v)
        x = (seed_theta - delta_s) / scale
        # Numerically stable sigmoid; mirrors ``_sigmoid`` in adaptive_picker.
        if x >= 0:
            return 1.0 / (1.0 + math.exp(-x))
        ex = math.exp(x)
        return ex / (1.0 + ex)

    measured = {sid for sid in delta_map if sid in sample_lookup}

    # Hardest first — desc δ_s, ascending sample_id as tiebreak. Unmeasured
    # samples fall at δ=0 (the population-mean prior) and trail the measured
    # tail via ``_trim_unmeasured``.
    full_order = sorted(sample_lookup.keys(), key=lambda s: (-delta_map.get(s, 0.0), s))
    trimmed_order = _trim_unmeasured(full_order, measured, max_unmeasured)

    items = [
        DatasetItem(
            sample_id=sid,
            query=sample_lookup[sid]["query"],
            ground_truth=sample_lookup[sid]["ground_truth"],
            task=sample_lookup[sid].get("task"),
            n_obs=n_obs_map.get(sid, 0),
            delta=delta_map.get(sid),
            delta_se=delta_se_map.get(sid),
            p_hat=_p_hat(sid),
            pick_score=pick_score_map.get(sid),
        )
        for sid in trimmed_order[:limit]
    ]

    # Declared train/test split from the dataset's campaign config — a
    # display-only fact; the held-out test fold is never materialized.
    split_train: int | None = None
    split_test: int | None = None
    campaign_path = (datasets_root / name / "campaign.json").resolve()
    if campaign_path.is_relative_to(datasets_root) and campaign_path.is_file():
        cc = json.loads(campaign_path.read_text(encoding="utf-8"))
        declared = (cc.get("campaign_config") or {}).get("dataset_split")
        if isinstance(declared, dict):
            split_train = declared.get("train")
            split_test = declared.get("test")

    return DatasetPreviewResponse(
        name=raw["name"],
        row_count=len(sample_lookup),
        split_train=split_train,
        split_test=split_test,
        items=items,
    )


class MeasurementDot(BaseModel):
    ord: str = Field(
        description=(
            "Stable composite ordinal — opaque string the client only uses "
            "for lexicographic sort + uniqueness. Dataset scope encodes "
            "created_at + run_id + index; cycle/campaign scope encodes "
            "round + scoreboard idx."
        ),
    )
    hit: bool
    label: str = Field(description="Short human label, e.g. 'R3 cand 2'.")


class SampleSeries(BaseModel):
    sample_id: int
    measurements: list[MeasurementDot]


class MeasurementSeriesResponse(BaseModel):
    name: str
    scope: HeatmapScope
    items: list[SampleSeries]


def _cycle_series(
    store: Any, campaign_id: str, cycle_id: str, sample_ids: set[int]
) -> dict[int, list[dict[str, Any]]]:
    """Walk one cycle's ``rounds/round_*.json``, returning per-sample series.

    Ord = ``{round:04d}/{cand_idx:02d}`` so series sort chronologically by
    round then by scoreboard position within the round. Candidates not
    appearing in the round's scoreboard sink to slot 99.

    Errored items (``error`` truthy or ``predicted == "ERROR"``) are dropped
    so the empirical hit-rate column and the Rasch fit see the same
    observation set — same filter as :func:`build_observations` in
    ``application/intelligence/exploration.py``.
    """
    cycle_dir = cycle_dir_for(store.base_dir, campaign_id, cycle_id)
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
                if item.get("error") or item.get("predicted") == "ERROR":
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


def _campaign_series(
    store: Any, campaign_id: str, sample_ids: set[int]
) -> dict[int, list[dict[str, Any]]]:
    """Pool every cycle's round-file series across one campaign.

    Ord is prefixed with the cycle id so series from different cycles in
    the campaign stay distinct under lexicographic sort.
    """
    out: dict[int, list[dict[str, Any]]] = {sid: [] for sid in sample_ids}
    for entry in store.campaigns.enumerate_cycles():
        if entry["campaign_id"] != campaign_id:
            continue
        cid = entry["cycle_id"]
        per_cycle = _cycle_series(store, campaign_id, cid, sample_ids)
        for sid, dots in per_cycle.items():
            for d in dots:
                out[sid].append({"ord": f"{cid}/{d['ord']}", "hit": d["hit"], "label": d["label"]})
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
    max_unmeasured: int | None = Query(
        default=None,
        ge=0,
        le=1000,
        description=(
            "Mirrors ``/preview``: optional cap on unmeasured rows. None "
            "(default) = no trim. Must match the value the caller passed to "
            "``/preview`` so the two responses stay aligned by index."
        ),
    ),
    scope: Literal["cycle", "campaign", "dataset"] = Query(
        default="dataset",
        description=(
            "dataset = cross-campaign MeasurementArchive series per sample. "
            "campaign = pool every cycle's round files in the campaign. "
            "cycle = walk one cycle's round files."
        ),
    ),
    campaign_id: str | None = Query(
        default=None,
        description="Required when scope is campaign or cycle.",
    ),
    cycle_id: str | None = Query(
        default=None,
        description="Required when scope=cycle; ignored otherwise.",
    ),
) -> MeasurementSeriesResponse:
    """Per-sample chronological measurement series feeding the hard-sample
    leaderboard's Meas heat-map column.

    Returns measurements for the same top-``limit`` samples (in the same
    Rasch-difficulty order) as ``/preview`` — clients can zip the two
    responses by ``sample_id`` without re-sorting. ``ord`` is opaque and
    only used for the roster's left-to-right alignment across rows.
    """
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

    artifact = _resolve_scope_artifact(
        store,
        scope=scope,
        name=name,
        backend_id=backend_id,
        campaign_id=campaign_id,
        cycle_id=cycle_id,
    )
    rasch = artifact.get("rasch", {})
    delta_map: dict[int, float] = {int(k): float(v) for k, v in rasch.get("delta", {}).items()}

    # Same sort key as ``/preview``: hardest first by δ_s, unmeasured samples
    # fall at δ=0 and trail the measured tail.
    full_order = sorted(sample_lookup.keys(), key=lambda s: (-delta_map.get(s, 0.0), s))
    measured = {sid for sid in delta_map if sid in sample_lookup}
    trimmed_order = _trim_unmeasured(full_order, measured, max_unmeasured)
    selected = trimmed_order[:limit]
    selected_set = set(selected)

    if scope == "cycle":
        assert campaign_id is not None and cycle_id is not None  # checked in resolver
        series = _cycle_series(store, campaign_id, cycle_id, selected_set)
    elif scope == "campaign":
        assert campaign_id is not None  # checked in resolver
        series = _campaign_series(store, campaign_id, selected_set)
    else:
        raw_series = measurement_series_for_samples(store, backend_id, selected)
        # Dataset-scope ord already carries timestamp + run_id + idx; label
        # is a short "run abbrev" — first 8 chars of run_id for tooltips.
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
