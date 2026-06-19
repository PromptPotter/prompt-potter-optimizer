"""Dataset preview router — hard-sample leaderboard for the New-Job view."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from promptpotter.application.config import load_campaign_config
from promptpotter.application.datasets.csv_ingest import (
    MAX_SAMPLES,
    IngestError,
    candidate_library_from_rows,
    parse_candidate_library,
)
from promptpotter.application.datasets.ingest import (
    MAX_UPLOAD_BYTES,
    SlugTakenError,
    draft_from_dataset,
    ingest_draft,
)
from promptpotter.application.datasets.prompts import load_node_prompt
from promptpotter.application.intelligence.adaptive_queue_mechanism import marginal_hit_probability
from promptpotter.application.intelligence.measurement_series import (
    campaign_measurement_series,
    cycle_measurement_series,
)
from promptpotter.application.jobs.launcher import draft_wire_with_locks
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.pipeline_schema import NodeConfigParam, NodeOutputSchema
from promptpotter.infrastructure.store import (
    DatasetAccessError,
    campaign_root_dir_for,
    list_readable_datasets,
    readable_dataset_dir,
)
from promptpotter.infrastructure.store.archive_views import (
    measurement_series_for_samples,
)
from promptpotter.infrastructure.store.base import read_json
from promptpotter.presentation.api.deps import StoreDep, get_cycle_dir_or_404, get_draft_registry
from promptpotter.shared.errors import (
    BadRequestError,
    ConflictError,
    ContentTooLargeError,
    NotFoundError,
    PayloadInvalidError,
)

datasets_router = APIRouter(prefix="/datasets", tags=["Datasets"])

# `cycle` (one cycle's Rasch fit) / `campaign` (pooled) / `dataset` (cross-campaign archive).
# Workspace scope would be meaningless (samples differ per dataset), so the tier stops at dataset.
HeatmapScope = Literal["cycle", "campaign", "dataset"]


class DatasetIndexEntry(BaseModel):
    """One row in the dataset registry — backs the Dashboard ``New campaign`` view.

    Wire shape pinned in ``docs/specs/m12-api-openapi.yaml::DatasetIndexEntry``.
    """

    name: str = Field(description="Slug used as the path segment under `datasets/`.")
    title: str | None = Field(default=None, description="Display title (from `dataset.md`).")
    tier: Literal["yours", "benchmark", "demo"] = Field(
        description=(
            "``yours`` = user-owned Origin under ``projects/{tenant}/datasets/{slug}/``. "
            "``benchmark`` = install-global ``datasets/{slug}/``; visible only to identities "
            "holding the ``datasets.benchmarks.read`` capability. "
            "``demo`` = the built-in try-and-learn dataset, surfaced while "
            "``User.demo_mode_enabled`` is on (independent of the benchmark capability)."
        ),
    )
    n_samples: int = Field(
        default=0,
        description="Sample bank size from ``cache.json``; ``0`` when the cache hasn't been materialized yet.",
    )


class DatasetIndexResponse(BaseModel):
    datasets: list[DatasetIndexEntry]


@datasets_router.get("", response_model=DatasetIndexResponse)
async def list_datasets(store: StoreDep) -> DatasetIndexResponse:
    """Every dataset this identity may read — tenant Origins, demo origins (while
    demo mode is on), and install benchmarks (only with ``datasets.benchmarks.read``).

    The visibility policy lives once in ``store/dataset_access.py`` and is shared
    with the per-dataset read endpoints, so the picker can never list a dataset
    that ``GET /datasets/{name}/...`` would then deny.
    """
    return DatasetIndexResponse(
        datasets=[
            DatasetIndexEntry(
                name=ref.name, title=ref.title, tier=ref.tier, n_samples=ref.n_samples
            )
            for ref in list_readable_datasets(store)
        ]
    )


def _too_large(observed: int | str) -> ContentTooLargeError:
    return ContentTooLargeError(
        f"Upload {observed} bytes exceeds the per-file cap of {MAX_UPLOAD_BYTES} bytes."
    )


async def _read_capped(request: Request, upload: UploadFile, cap: int) -> bytes:
    """Read ``upload`` into memory, aborting with 413 the moment it exceeds ``cap``.

    A buffer-then-check (``await upload.read()`` followed by a size test) would let
    a multi-GB upload exhaust memory before the test fires. We fast-reject on the
    declared ``Content-Length`` first, then stream in chunks as the real backstop
    (the header can lie or be absent under chunked transfer-encoding).
    """
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > cap:
        raise _too_large(declared)

    chunk_size = 1024 * 1024  # 1 MiB
    buf = bytearray()
    while chunk := await upload.read(chunk_size):
        buf.extend(chunk)
        if len(buf) > cap:
            raise _too_large(len(buf))
    return bytes(buf)


@datasets_router.post("/ingest")
async def ingest_dataset(
    request: Request,
    store: StoreDep,
    file: Annotated[
        UploadFile,
        File(description="Tabular upload (CSV/TSV/JSON/JSONL/XLSX); any columns."),
    ],
    slug: Annotated[str | None, Form(description="Optional slug override.")] = None,
) -> dict[str, Any]:
    """Parse an uploaded tabular file (CSV/TSV/JSON/XLSX); return a server-held
    :class:`DraftCampaign`.

    Wire contract pinned in ``docs/specs/m12-api-openapi.yaml::POST /datasets/ingest``.
    NOT a Control-remote command — no ``CommandRecord`` lands until the
    operator commits via ``/commands/mint-campaign-from-draft``.
    """
    registry = get_draft_registry(request)

    blob = await _read_capped(request, file, MAX_UPLOAD_BYTES)

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
        raise PayloadInvalidError(
            exc.message,
            code="ingest_failed",
            details={"reason": exc.reason, "max_samples": MAX_SAMPLES},
        ) from None
    except ValueError as exc:
        raise PayloadInvalidError(str(exc), details={"reason": "bad_slug"}) from None
    except SlugTakenError as exc:
        # The chat turns this into an in-flow choice (use existing / save as new),
        # so it needs BOTH names: the colliding one (to offer "use existing") and
        # the free suggestion (to offer "save as new").
        raise ConflictError(
            f"A dataset named '{exc.slug}' already exists.",
            code="slug_collision",
            details={"slug": exc.slug, "suggested_slug": exc.suggested},
        ) from None
    return draft_wire_with_locks(draft)


@datasets_router.post("/draft/candidate-library")
async def upload_candidate_library(
    request: Request,
    store: StoreDep,
    file: Annotated[
        UploadFile,
        File(description="Target library — one entry per line, or a single-column CSV/Excel."),
    ],
    draft_id: Annotated[str, Form(description="Draft to attach the library to.")],
) -> dict[str, Any]:
    """Attach a candidate library to a draft — the operator's "drop in place" for an
    unfulfilled ``candidate_source`` dependency. Returns the updated draft wire (its
    ``dependencies`` block now reports the dependency ``fulfilled``).

    Like ``/datasets/ingest`` this is NOT a Control-remote command — it mutates the
    in-flight draft only; nothing lands on a ledger until mint. Wire contract pinned
    in ``docs/specs/m12-api-openapi.yaml::POST /datasets/draft/candidate-library``.
    """
    registry = get_draft_registry(request)
    draft = registry.get(draft_id, tenant_id=store.identity.tenant_id)
    if draft is None:
        raise NotFoundError(f"draft {draft_id!r} not found.", code="command_target_not_found")

    blob = await _read_capped(request, file, MAX_UPLOAD_BYTES)
    try:
        terms = parse_candidate_library(blob, file.filename or "")
    except IngestError as exc:
        raise PayloadInvalidError(
            exc.message, code="ingest_failed", details={"reason": exc.reason}
        ) from None
    if not terms:
        raise PayloadInvalidError(
            "The candidate library has no usable entries (every line was blank).",
            code="ingest_failed",
            details={"reason": "empty"},
        )
    updated = registry.update(draft.patch(candidate_library=terms))
    return draft_wire_with_locks(updated)


class _BuildLibraryBody(BaseModel):
    """Body for building a candidate library from one of the draft's own columns."""

    model_config = ConfigDict(extra="forbid")

    draft_id: str = Field(min_length=8, max_length=64)
    column: str = Field(min_length=1, max_length=256)


@datasets_router.post("/draft/candidate-library/from-column")
async def build_candidate_library_from_column(
    request: Request, store: StoreDep, body: _BuildLibraryBody
) -> dict[str, Any]:
    """Build a draft's candidate library from the distinct values of one of its own
    columns — the unified "build from dataset" path (no external file).

    When the targets already live in the data (the ground-truth/target column, the
    union of the dataset's category sheets), the library is derived rather than
    uploaded. Returns the updated draft wire (its `candidate_library` dependency now
    `fulfilled`). NOT a Control-remote command. Wire contract pinned in
    `docs/specs/m12-api-openapi.yaml::POST /datasets/draft/candidate-library/from-column`.
    """
    registry = get_draft_registry(request)
    draft = registry.get(body.draft_id, tenant_id=store.identity.tenant_id)
    if draft is None:
        raise NotFoundError(f"draft {body.draft_id!r} not found.", code="command_target_not_found")
    if body.column not in draft.headers:
        raise PayloadInvalidError(
            f"column {body.column!r} is not one of the dataset's columns {list(draft.headers)}."
        )
    cache = store.tenant_datasets.load_draft_cache(body.draft_id)
    if cache is None:
        raise PayloadInvalidError("draft has no cached rows to build from.")
    terms = candidate_library_from_rows(cache.get("items", []), body.column)
    if not terms:
        raise PayloadInvalidError(
            f"column {body.column!r} has no usable values.",
            code="ingest_failed",
            details={"reason": "empty"},
        )
    updated = registry.update(draft.patch(candidate_library=terms))
    return draft_wire_with_locks(updated)


@datasets_router.post("/{name}/draft")
async def draft_from_existing_dataset(
    name: str, request: Request, store: StoreDep
) -> dict[str, Any]:
    """Build a server-held :class:`DraftCampaign` from an authored dataset's files.

    The direct path behind "open this dataset in the ingest panel" — a demo /
    benchmark / owned Origin becomes a prefilled draft without a browser-side CSV
    round-trip. Identity-gated through the same resolver as the other dataset
    reads; like ``/datasets/ingest`` it is NOT a Control-remote command — no
    ``CommandRecord`` lands until the operator commits via
    ``/commands/mint-campaign-from-draft``.
    """
    registry = get_draft_registry(request)
    dataset_dir = _resolve_or_404(store, name)
    try:
        draft = draft_from_dataset(
            stores=store, registry=registry, dataset_dir=dataset_dir, dataset_name=name
        )
    except IngestError as exc:
        raise PayloadInvalidError(
            exc.message, code="ingest_failed", details={"reason": exc.reason}
        ) from None
    return draft_wire_with_locks(draft)


def _resolve_or_404(store: Any, name: str) -> Path:
    """Resolve *name* through the identity-aware gateway; 404 if not readable.

    The 404 (rather than 403) keeps the existence-leak posture: a non-admin can't
    tell a benchmark apart from a non-existent dataset. All dataset-directory
    access in this router goes through here — never a raw ``benchmarks_root`` read.
    """
    try:
        return readable_dataset_dir(store, name)
    except DatasetAccessError as exc:
        raise NotFoundError(f"Dataset '{name}' not found") from exc


def _load_dataset_cache(dataset_dir: Path) -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
    """Load ``cache.json`` from a resolved dataset dir; normalise sample-id keys to int.

    Sample-id key varies on disk (``id`` canonical, BBEH HF emits ``sample_id``) —
    normalise at the read boundary. Raises ``NotFoundError`` when the resolved
    dir carries no cache. The dir is already access-checked by :func:`_resolve_or_404`.
    """
    cache_path = dataset_dir / "cache.json"
    if not cache_path.is_file():
        raise NotFoundError(f"Dataset '{dataset_dir.name}' not found")
    raw = read_json(cache_path)
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
            raise BadRequestError("scope=cycle requires campaign_id and cycle_id")
        cycle_dir = get_cycle_dir_or_404(campaign_id, cycle_id, store)
        path = cycle_dir / "hard_samples.json"
        if not path.is_file():
            return {}
        cycle_artifact: dict[str, Any] = read_json(path)
        return cycle_artifact
    if scope == "campaign":
        if not campaign_id:
            raise BadRequestError("scope=campaign requires campaign_id")
        campaign_dir = campaign_root_dir_for(store.base_dir, campaign_id)
        if not campaign_dir.exists():
            raise NotFoundError(f"Campaign '{campaign_id}' not found")
        path = campaign_dir / "hard_samples.json"
        if not path.is_file():
            return {}
        campaign_artifact: dict[str, Any] = read_json(path)
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


@datasets_router.get("/{name}/preview", response_model=DatasetPreviewResponse)
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
    dataset_dir = _resolve_or_404(store, name)
    raw, sample_lookup = _load_dataset_cache(dataset_dir)

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
    campaign_path = dataset_dir / "campaign.json"
    if campaign_path.is_file():
        cc = read_json(campaign_path)
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


@datasets_router.get(
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
    raw, sample_lookup = _load_dataset_cache(_resolve_or_404(store, name))

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
    the original-cased name for chip labelling; `origin_prompt_fields` is the origin PromptTemplate
    for the primary node (None when the dataset ships no `prompts/`).
    """

    name: str
    connector: str
    pipeline: dict[str, Any]
    view: dict[str, Any] | None
    # The full operator-editable config surface per node (model/temperature/
    # thinking/max_tokens/provider — the node's whole config minus prompt fields),
    # the steer + read-only node-detail control surface. The `optimizer_locked`
    # flag per param marks what the optimizer may not permute (model/provider
    # under a strict campaign), which the operator may still set on a fork seed.
    node_config_schema: dict[str, list[NodeConfigParam]]
    # Per-node structured-output contract (read-only) — the steer panel shows it
    # beside the config so the operator sees the WHOLE node (model + params +
    # prompt + the structured output it produces). None for nodes with no schema.
    node_output_schema: dict[str, NodeOutputSchema | None]
    origin_prompt_fields: dict[str, Any] | None


@datasets_router.get("/{name}/pipeline", response_model=DatasetPipelineResponse)
async def get_dataset_pipeline(name: str, store: StoreDep) -> DatasetPipelineResponse:
    """Return the dataset overlay's parsed pipeline schema, graph view, per-node
    config + output schema, and origin prompt.

    Identity-gated through the same resolver as the other dataset reads — there is
    no unauthenticated path to a benchmark's pipeline/overlay config.
    """
    dataset_dir = _resolve_or_404(store, name)
    pipeline_path = dataset_dir / "pipeline.json"
    if not pipeline_path.is_file():
        raise NotFoundError(f"Dataset '{name}' has no pipeline.json")
    raw = read_json(pipeline_path)
    # `parse_pipeline_response` strips lone surrogates at parse time so the
    # rendered model is already wire-safe (some overlays carry escape
    # sequences pointing at lone low surrogates that crash UTF-8 encode).
    schema = parse_pipeline_response(raw)
    connector = (raw.get("backend_name") or raw.get("name") or name).strip()
    # Model/provider lock is a campaign policy (`optimization.forbidden_axes_strict`),
    # not a pipeline fact — a node may declare `model` in `param_keys` as a capability
    # while the campaign still pins it. Read the dataset's default campaign.json; absent,
    # fall back to the CampaignConfig default (strict — the conservative floor).
    forbidden_strict = True
    campaign_path = dataset_dir / "campaign.json"
    if campaign_path.is_file():
        craw = read_json(campaign_path)
        cfg = load_campaign_config(craw.get("campaign_config", craw))
        forbidden_strict = cfg.optimization.forbidden_axes_strict
        # Apply the dataset's default search-space narrowing so the setup editor
        # opens showing the recommended per-node locks (e.g. retrieval nodes
        # origin-locked); the draft's own overlay edits layer on top client-side.
        schema = schema.narrow(cfg.optimizer_narrowing)
    # Origin prompt for the first pipeline step — read-only seed the node panel
    # shows. `load_node_prompt` resolves `{node}.json` → `default.json`; absent a
    # `prompts/` dir it raises, which we surface as null origin_prompt_fields.
    origin_prompt_fields: dict[str, Any] | None = None
    steps = schema.active_steps
    if steps:
        try:
            origin_prompt_fields = load_node_prompt(dataset_dir, steps[0]).model_dump()
        except FileNotFoundError:
            origin_prompt_fields = None
    return DatasetPipelineResponse(
        name=name,
        connector=connector,
        pipeline=schema.model_dump(by_alias=True),
        view=schema.view.model_dump(by_alias=True) if schema.view is not None else None,
        node_config_schema=schema.node_config_schema(forbidden_strict),
        node_output_schema=schema.node_output_schemas(),
        origin_prompt_fields=origin_prompt_fields,
    )


__all__ = [
    "DatasetItem",
    "DatasetPipelineResponse",
    "DatasetPreviewResponse",
    "MeasurementDot",
    "MeasurementSeriesResponse",
    "SampleSeries",
]
