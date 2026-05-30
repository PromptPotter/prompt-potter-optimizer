"""Dataset preview router — hard-sample leaderboard for the New-Job view."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field

from promptpotter.application.datasets.csv_ingest import (
    MAX_SAMPLES,
    IngestError,
)
from promptpotter.application.datasets.draft_campaign import (
    DraftCampaignRegistry,
)
from promptpotter.application.datasets.ingest import (
    MAX_UPLOAD_BYTES,
    SlugTakenError,
    ingest_draft,
)
from promptpotter.application.intelligence.adaptive_queue_mechanism import marginal_hit_probability
from promptpotter.application.intelligence.measurement_series import (
    campaign_measurement_series,
    cycle_measurement_series,
)
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.infrastructure.store import campaign_root_dir_for, cycle_dir_for
from promptpotter.infrastructure.store.archive_views import (
    measurement_series_for_samples,
)
from promptpotter.infrastructure.store.paths import (
    DEFAULT_DATASETS_ROOT,
    validate_dataset_name,
)
from promptpotter.presentation.api.deps import StoreDep
from promptpotter.shared.identity import BENCHMARKS_READ_CAP

_datasets_router = APIRouter(prefix="/datasets", tags=["Datasets"])

# `cycle` (one cycle's Rasch fit) / `campaign` (pooled) / `dataset` (cross-campaign archive).
# Workspace scope would be meaningless (samples differ per dataset), so the tier stops at dataset.
HeatmapScope = Literal["cycle", "campaign", "dataset"]


class DatasetIndexEntry(BaseModel):
    """One row in the dataset registry — backs the Dashboard ``New campaign`` view.

    Wire shape pinned in ``docs/specs/m12-api-openapi.yaml::DatasetIndexEntry``.
    """

    name: str = Field(description="Slug used as the path segment under `datasets/`.")
    title: str | None = Field(default=None, description="Display title (from `dataset.md`).")
    tier: Literal["yours", "benchmark"] = Field(
        description=(
            "``yours`` = user-owned Origin under ``projects/{tenant}/datasets/{slug}/``. "
            "``benchmark`` = install-global ``datasets/{slug}/``; visible only to identities "
            "holding the ``datasets.benchmarks.read`` capability."
        ),
    )
    n_samples: int = Field(
        default=0,
        description="Sample bank size from ``cache.json``; ``0`` when the cache hasn't been materialized yet.",
    )


class DatasetIndexResponse(BaseModel):
    datasets: list[DatasetIndexEntry]


@_datasets_router.get("", response_model=DatasetIndexResponse)
async def list_datasets(store: StoreDep) -> DatasetIndexResponse:
    """Tenant Origins + (if the identity holds ``datasets.benchmarks.read``) benchmarks.

    Identity-scoped: web tenants only see their own collection. The bleed-
    through bug (every signup seeing the developer's locally cached
    ``aime_2025`` / ``bbeh`` / ... benchmarks) is closed by gating
    ``tier: "benchmark"`` rows on :data:`BENCHMARKS_READ_CAP`.
    """
    out: list[DatasetIndexEntry] = []

    for slug in store.tenant_datasets.list_slugs():
        dataset_dir = store.tenant_datasets.dataset_dir(slug)
        out.append(
            DatasetIndexEntry(
                name=slug,
                title=_read_dataset_title(dataset_dir),
                tier="yours",
                n_samples=_read_n_samples(dataset_dir),
            )
        )

    if BENCHMARKS_READ_CAP in store.identity.capabilities and DEFAULT_DATASETS_ROOT.is_dir():
        for entry in sorted(DEFAULT_DATASETS_ROOT.iterdir()):
            if not entry.is_dir() or not (entry / "pipeline.json").is_file():
                continue
            try:
                validate_dataset_name(entry.name)
            except ValueError:
                continue
            out.append(
                DatasetIndexEntry(
                    name=entry.name,
                    title=_read_dataset_title(entry),
                    tier="benchmark",
                    n_samples=_read_n_samples(entry),
                )
            )
    return DatasetIndexResponse(datasets=out)


@_datasets_router.post("/ingest")
async def ingest_dataset(
    request: Request,
    store: StoreDep,
    file: Annotated[
        UploadFile,
        File(description="CSV blob with `query` + `ground_truth` columns."),
    ],
    slug: Annotated[str | None, Form(description="Optional slug override.")] = None,
) -> dict[str, Any]:
    """Parse an uploaded CSV; return a server-held :class:`DraftCampaign`.

    Wire contract pinned in ``docs/specs/m12-api-openapi.yaml::POST /datasets/ingest``.
    NOT a Control-remote command — no ``CommandRecord`` lands until the
    operator commits via ``/commands/mint-campaign-from-draft``.
    """
    registry = _draft_registry(request)

    blob = await file.read()
    if len(blob) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail={
                "error": "payload_invalid",
                "message": (
                    f"Upload {len(blob)} bytes exceeds the per-file cap of "
                    f"{MAX_UPLOAD_BYTES} bytes."
                ),
            },
        )

    # Parse → register the draft → persist its cache. Shared with the CLI
    # `ingest` verb (`application/datasets/ingest.py`) so both surfaces drive
    # the identical orchestration; the handler only maps errors to HTTP.
    try:
        draft = ingest_draft(
            stores=store,
            registry=registry,
            blob=blob,
            filename=file.filename or "",
            slug=slug,
        )
    except IngestError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "ingest_failed",
                "message": exc.message,
                "details": {"reason": exc.reason, "max_samples": MAX_SAMPLES},
            },
        ) from None
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "payload_invalid",
                "message": str(exc),
                "details": {"reason": "bad_slug"},
            },
        ) from None
    except SlugTakenError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "payload_invalid",
                "message": f"Slug '{exc.slug}' already exists in your collection.",
                "details": {"suggested_slug": exc.suggested},
            },
        ) from None
    return draft.to_wire()


def _draft_registry(request: Request) -> DraftCampaignRegistry:
    """Pull the registry off ``app.state``; missing registry is a programmer error."""
    registry: DraftCampaignRegistry | None = getattr(request.app.state, "draft_campaigns", None)
    if registry is None:
        raise HTTPException(500, "draft-campaign registry not initialised")
    return registry


def _read_dataset_title(dataset_dir: Path) -> str | None:
    md = dataset_dir / "dataset.md"
    if not md.is_file():
        return None
    for line in md.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip() or None
    return None


def _read_n_samples(dataset_dir: Path) -> int:
    """Cheap read of ``cache.json::row_count`` (falls back to ``items`` length)."""
    cache = dataset_dir / "cache.json"
    if not cache.is_file():
        return 0
    try:
        raw = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    row_count = raw.get("row_count")
    if isinstance(row_count, int):
        return row_count
    items = raw.get("items")
    return len(items) if isinstance(items, list) else 0


def _check_dataset_name(name: str) -> None:
    """Translate ``validate_dataset_name``'s ``ValueError`` to ``HTTPException(400)``."""
    try:
        validate_dataset_name(name)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


def _load_dataset_cache(name: str) -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
    """Validate name, load ``cache.json``, normalise sample-id keys to int.

    Sample-id key varies on disk (``id`` canonical, BBEH HF emits
    ``sample_id``) — normalise at the read boundary. Raises
    ``HTTPException`` on invalid name / missing file.
    """
    _check_dataset_name(name)
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
    return raw, sample_lookup


def _resolve_scope_artifact(
    store: Any,
    *,
    scope: HeatmapScope,
    name: str,
    backend_id: str,
    campaign_id: str | None,
    cycle_id: str | None,
) -> dict[str, Any]:
    """Resolve the hard-samples artifact for *scope*. Missing `hard_samples.json` returns `{}`
    (heatmap renders empty); missing campaign/cycle DIR is a real 404.
    """
    from promptpotter.application.intelligence.hard_sample_archive import (
        build_archive_hard_samples_artifact,
    )

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
    # `dataset` — always per-dataset (cross-dataset pooling is meaningless).
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
    """Keep all measured + at most *cap* unmeasured (drops the tail of the unmeasured block, not
    a random slice). `cap=0` ⇒ measured only; `cap=None` ⇒ no-op.
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
            "Queue-mechanism's blended objective on this sample for a brand-new candidate (prior "
            "N(0, sigma_theta**2)) vs the best fitted candidate. The live adaptive queue "
            "mechanism re-evaluates per step. None when unmeasured."
        ),
    )
    delta: float | None = Field(
        default=None,
        description="Rasch difficulty delta_s (higher = harder). None when unmeasured.",
    )
    delta_se: float | None = Field(
        default=None,
        description="SE of delta_s (large = barely measured). None when unmeasured.",
    )
    p_hat: float | None = Field(
        default=None,
        description=(
            "Marginal hit prob the seed-centred decision-IG reads — see "
            "``adaptive_queue_mechanism.marginal_hit_probability``. Near 0.5 = contested at seed; "
            "near 0/1 = predictable. None when unmeasured."
        ),
    )


class DatasetPreviewResponse(BaseModel):
    name: str
    row_count: int
    split_train: int | None = Field(
        default=None,
        description="Declared training-bank fold size from `campaign.json::dataset_split`.",
    )
    split_test: int | None = Field(
        default=None,
        description="Declared held-out test fold size (not materialized).",
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
        description="Cap on unmeasured rows kept in Rasch-sorted output; None = no trim.",
    ),
    scope: Literal["cycle", "campaign", "dataset"] = Query(
        default="dataset",
        description="dataset=cross-campaign; campaign=pooled (needs campaign_id); cycle=one cycle (needs both ids).",
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
    """Hard-sample leaderboard — hardest-first by δ_s, unmeasured trail by sample_id.

    Counts of measured/unmeasured come from the companion `/measurement-series` (a sample's
    series carrying ≥1 dot = measured). Rasch δ persists across rounds + inherits from parent
    fits, so it overcounts on its own.
    """
    raw, sample_lookup = _load_dataset_cache(name)

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
    # Queue-mechanism's blended objective for a brand-new candidate — populated in both per-cycle + archive artifacts.
    pick_score_block = artifact.get("pick_score", {}).get("per_sample", {})
    pick_score_map: dict[int, float] = {int(k): float(v) for k, v in pick_score_block.items()}

    # Seed-centred marginal hit prob — see `adaptive_queue_mechanism.marginal_hit_probability`.
    sigma_theta = float(rasch.get("sigma_theta", 0.0))
    theta_map: dict[str, float] = {str(k): float(v) for k, v in rasch.get("theta", {}).items()}
    candidate_order_raw = artifact.get("candidate_order") or []
    seed_theta = (
        theta_map.get(str(candidate_order_raw[0]), 0.0)
        if candidate_order_raw and theta_map
        else 0.0
    )
    var_c = sigma_theta**2  # brand-new candidate's ability prior variance

    def _p_hat(sid: int) -> float | None:
        if sid not in delta_map:
            return None
        return marginal_hit_probability(
            mu_c=seed_theta,
            var_c=var_c,
            delta_s=delta_map[sid],
            se_delta_s=delta_se_map.get(sid, 0.0),
        )

    measured = {sid for sid in delta_map if sid in sample_lookup}

    # Hardest first — desc δ_s, asc sample_id tiebreak; unmeasured fall at δ=0 and trail.
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

    # Train/test split from campaign config — display-only; held-out test never materialized.
    split_train: int | None = None
    split_test: int | None = None
    datasets_root = DEFAULT_DATASETS_ROOT.resolve()
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
        description="Opaque ordinal for lex sort + uniqueness (encodes ts/run/idx or round/cand).",
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
        description="Must match `/preview`'s value so the two responses align by index.",
    ),
    scope: Literal["cycle", "campaign", "dataset"] = Query(
        default="dataset",
        description="Same scopes as `/preview` (dataset/campaign/cycle).",
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
    """Chronological per-sample series for the Meas heat-map column. Aligned to `/preview` order
    + limit so clients can zip by sample_id. `ord` is opaque (only used for row alignment).
    """
    raw, sample_lookup = _load_dataset_cache(name)

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

    # Same sort key as `/preview`.
    full_order = sorted(sample_lookup.keys(), key=lambda s: (-delta_map.get(s, 0.0), s))
    measured = {sid for sid in delta_map if sid in sample_lookup}
    trimmed_order = _trim_unmeasured(full_order, measured, max_unmeasured)
    selected = trimmed_order[:limit]
    selected_set = set(selected)

    if scope == "cycle":
        assert campaign_id is not None and cycle_id is not None  # checked in resolver
        series = cycle_measurement_series(store, campaign_id, cycle_id, selected_set)
    elif scope == "campaign":
        assert campaign_id is not None  # checked in resolver
        series = campaign_measurement_series(store, campaign_id, selected_set)
    else:
        raw_series = measurement_series_for_samples(store, backend_id, selected)
        # Dataset-scope ord carries ts/run/idx; label = first 8 chars of run_id for tooltips.
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
    """Target pipeline view for a dataset overlay. `view` drives the webapp chat-pane hero;
    `pipeline` is the full parsed schema for consumers needing per-node config; `connector` is
    the original-cased name for chip labelling.
    """

    name: str
    connector: str
    pipeline: dict[str, Any]
    view: dict[str, Any] | None


@_datasets_router.get("/{name}/pipeline", response_model=DatasetPipelineResponse)
async def get_dataset_pipeline(name: str) -> DatasetPipelineResponse:
    """Return the dataset overlay's parsed pipeline schema and graph view."""
    _check_dataset_name(name)
    datasets_root = DEFAULT_DATASETS_ROOT.resolve()
    pipeline_path = (datasets_root / name / "pipeline.json").resolve()
    if not pipeline_path.is_relative_to(datasets_root):
        raise HTTPException(400, "Invalid dataset name")
    if not pipeline_path.is_file():
        raise HTTPException(404, f"Dataset '{name}' has no pipeline.json")
    raw = json.loads(pipeline_path.read_text(encoding="utf-8"))
    # `parse_pipeline_response` strips lone surrogates at parse time so the
    # rendered model is already wire-safe (some overlays carry escape
    # sequences pointing at lone low surrogates that crash UTF-8 encode).
    schema = parse_pipeline_response(raw)
    connector = (raw.get("backend_name") or raw.get("name") or name).strip()
    return DatasetPipelineResponse(
        name=name,
        connector=connector,
        pipeline=schema.model_dump(by_alias=True),
        view=schema.view.model_dump(by_alias=True) if schema.view is not None else None,
    )


__all__ = [
    "DatasetItem",
    "DatasetPipelineResponse",
    "DatasetPreviewResponse",
    "MeasurementDot",
    "MeasurementSeriesResponse",
    "SampleSeries",
]
