"""Dataset preview router — hard-sample leaderboard for the New-Job view."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Form, Header, Query, Request, UploadFile
from pydantic import Field

from promptpotter.application.datasets.authored import load_dataset_campaign_config
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
from promptpotter.application.jobs.launcher.checkin import load_checkin_draft
from promptpotter.application.jobs.launcher.draft_build import draft_wire_with_locks
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.pipeline_schema import NodeConfigParam, NodeOutputSchema
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.archive_views import (
    measurement_series_for_samples,
)
from promptpotter.infrastructure.store.dataset_access import (
    DatasetAccessError,
    list_readable_datasets,
    readable_dataset_dir,
)
from promptpotter.infrastructure.store.io import read_json
from promptpotter.infrastructure.store.layout import campaign_root_dir_for
from promptpotter.infrastructure.store.stores import Stores, resolve_cycle_path
from promptpotter.presentation.api.deps import (
    StoreDep,
    decode_descend,
    get_cycle_dir_or_404,
)
from promptpotter.presentation.api.routers.commands import (
    dispatch_draft_patch,
    ensure_idempotency_key,
)
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


class DatasetIndexEntry(StrictModel):
    """One row in the dataset registry — backs the Dashboard ``New campaign`` view.

    Wire shape pinned in ``docs/specs/m12-api-openapi.yaml::DatasetIndexEntry``.
    """

    name: str = Field(description="Slug used as the path segment under `datasets/`.")
    title: str | None = Field(default=None, description="Display title (from `dataset.md`).")
    tier: Literal["yours", "install"] = Field(
        description=(
            "``yours`` = user-owned Origin under ``projects/{tenant}/datasets/{slug}/``. "
            "``install`` = content that ships with the product at ``datasets/{slug}/`` "
            "(benchmarks, demos, ``promptpotter-self``) — tracked in git, so readable by "
            "anyone using the install. A ``yours`` slug shadows an ``install`` one."
        ),
    )
    n_samples: int = Field(
        default=0,
        description="Sample bank size from ``cache.json``; ``0`` when the cache hasn't been materialized yet.",
    )


class DatasetIndexResponse(StrictModel):
    datasets: list[DatasetIndexEntry]


@datasets_router.get("", response_model=DatasetIndexResponse)
def list_datasets(store: StoreDep) -> DatasetIndexResponse:
    """Every dataset this identity may read — its own tenant Origins, then install content.

    The rule lives once in ``store/dataset_access.py`` and is shared with the
    per-dataset read endpoints, so the picker can never list a dataset that
    ``GET /datasets/{name}/...`` would then deny.
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
    """Parse an uploaded tabular file (CSV/TSV/JSON/XLSX); mint a durable check-in
    campaign and return its :class:`DraftCampaign` (``draft_id`` == ``campaign_id``).

    Wire contract pinned in ``docs/specs/m12-api-openapi.yaml::POST /datasets/ingest``.
    The check-in campaign appears in the sidebar immediately and survives a restart;
    nothing runs until the operator starts it via ``/commands/start-checkin``.
    """
    blob = await _read_capped(request, file, MAX_UPLOAD_BYTES)

    # Parse → mint the check-in campaign → persist its bank. Shared with the CLI
    # `new <file>` path (`application/datasets/ingest.py`) so both surfaces drive
    # the identical orchestration; the handler only maps errors to HTTP.
    try:
        draft = ingest_draft(
            stores=store,
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
    draft_id: Annotated[
        str, Form(description="Draft to attach the library to.", min_length=8, max_length=128)
    ],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    """Attach a candidate library to a draft — the operator's "drop in place" for an
    unfulfilled ``candidate_source`` dependency. Returns the updated draft wire (its
    ``dependencies`` block now reports the dependency ``fulfilled``).

    A multipart ingress for an `edit-draft-campaign`: the upload is parsed into the
    patch value, then dispatched, so the edit lands on the check-in ledger like every
    other origin mutation. Nothing runs until Start. Wire contract pinned in
    ``docs/specs/m12-api-openapi.yaml::POST /datasets/draft/candidate-library``.
    ``draft_id`` is the check-in campaign id.
    """
    idemp = ensure_idempotency_key(idempotency_key)
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
    return await dispatch_draft_patch(
        store,
        draft_id=draft_id,
        patch_raw={"candidate_library": terms},
        idempotency_key=idemp,
    )


class _BuildLibraryBody(StrictModel):
    """Body for building a candidate library from one of the draft's own columns."""

    # `draft_id` IS the owning `campaign_id`; bound it exactly as every other
    # check-in route does (`commands.py::_require_checkin_id`), not 64.
    draft_id: str = Field(min_length=8, max_length=128)
    column: str = Field(min_length=1, max_length=256)


@datasets_router.post("/draft/candidate-library/from-column")
async def build_candidate_library_from_column(
    store: StoreDep,
    body: _BuildLibraryBody,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    """Build a draft's candidate library from the distinct values of one of its own
    columns — the unified "build from dataset" path (no external file).

    When the targets already live in the data (the ground-truth/target column, the
    union of the dataset's category sheets), the library is derived rather than
    uploaded. The derived terms become an `edit-draft-campaign` patch, so this lands
    on the check-in ledger exactly like the upload does. Returns the updated draft
    wire (its `candidate_library` dependency now `fulfilled`). Wire contract pinned in
    `docs/specs/m12-api-openapi.yaml::POST /datasets/draft/candidate-library/from-column`.
    `draft_id` is the check-in campaign id.
    """
    idemp = ensure_idempotency_key(idempotency_key)
    draft = load_checkin_draft(store, body.draft_id)
    if draft is None:
        raise NotFoundError(f"draft {body.draft_id!r} not found.", code="command_target_not_found")
    if body.column not in draft.headers:
        raise PayloadInvalidError(
            f"column {body.column!r} is not one of the dataset's columns {list(draft.headers)}."
        )
    bank = store.checkin.load_bank(body.draft_id)
    if bank is None:
        raise PayloadInvalidError("draft has no cached rows to build from.")
    terms = candidate_library_from_rows(bank.get("items", []), body.column)
    if not terms:
        raise PayloadInvalidError(
            f"column {body.column!r} has no usable values.",
            code="ingest_failed",
            details={"reason": "empty"},
        )
    return await dispatch_draft_patch(
        store,
        draft_id=body.draft_id,
        patch_raw={"candidate_library": terms},
        idempotency_key=idemp,
    )


@datasets_router.post("/{name}/draft")
def draft_from_existing_dataset(name: str, store: StoreDep) -> dict[str, Any]:
    """Open an authored dataset's files as a durable check-in campaign.

    The direct path behind "open this dataset in the ingest panel" — a demo /
    benchmark / owned Origin becomes a prefilled check-in (``draft_id`` ==
    ``campaign_id``) without a browser-side CSV round-trip. Identity-gated through
    the same resolver as the other dataset reads; nothing runs until the operator
    starts it via ``/commands/start-checkin``.
    """
    dataset_dir = _resolve_or_404(store, name)
    try:
        draft = draft_from_dataset(stores=store, dataset_dir=dataset_dir, dataset_name=name)
    except IngestError as exc:
        raise PayloadInvalidError(
            exc.message, code="ingest_failed", details={"reason": exc.reason}
        ) from None
    return draft_wire_with_locks(draft)


def _resolve_or_404(store: Stores, name: str) -> Path:
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


def _artifact_scope_store(
    store: Stores,
    campaign_id: str | None,
    cycle_id: str | None,
    descend: str | None,
) -> tuple[Any, str | None, str | None]:
    """Store + ids the scope artifact lives in — the caller's own tree, or an L4 sandbox.

    A plain per-cycle/campaign read stays in the caller's ``store``. When
    ``?descend=`` is present the viewed leaf is an L4 inner cycle whose
    hard-samples + measurement archive live in the off-registry
    ``.inner/<outer cycle>`` sandbox, so walk there via :func:`resolve_cycle_path`
    (the same seam the dashboard + event-stream readers ride) and read every scope
    from the leaf store. Descend walks from the ROOT hop, so both root ids are
    required alongside it.
    """
    if not descend:
        return store, campaign_id, cycle_id
    if not campaign_id or not cycle_id:
        raise BadRequestError("descend requires campaign_id and cycle_id (the root hop)")
    leaf_store, leaf = resolve_cycle_path(
        store, (CycleHop(campaign_id=campaign_id, cycle_id=cycle_id), *decode_descend(descend))
    )
    return leaf_store, leaf.campaign_id, leaf.cycle_id


def _resolve_scope_artifact(
    store: Stores,
    *,
    scope: HeatmapScope,
    name: str,
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


class DatasetItem(StrictModel):
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


class DatasetPreviewResponse(StrictModel):
    name: str
    row_count: int
    split_test: int | None = Field(
        default=None,
        description="Declared held-out test fold size (not materialized). The training-bank "
        "size is `row_count` above — the bank IS the preview, so a second field restated it.",
    )
    items: list[DatasetItem]


@datasets_router.get("/{name}/preview", response_model=DatasetPreviewResponse)
def get_dataset_preview(
    name: str,
    store: StoreDep,
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
    descend: str | None = Query(
        default=None,
        description=(
            "L4 inner-cycle descent tail (`~`-joined `campaign::cycle` hops below the root, "
            "mirrors `?descend=` on the dashboard route). Present → read every scope from the "
            "inner `.inner/` sandbox; needs campaign_id + cycle_id (the root hop) to walk from."
        ),
    ),
) -> DatasetPreviewResponse:
    """Hard-sample leaderboard — hardest-first by δ_s, unmeasured trail by sample_id.

    Counts of measured/unmeasured come from the companion `/measurement-series` (a sample's
    series carrying ≥1 dot = measured). Rasch δ persists across rounds + inherits from parent
    fits, so it overcounts on its own.
    """
    dataset_dir = _resolve_or_404(store, name)
    raw, sample_lookup = _load_dataset_cache(dataset_dir)

    art_store, art_campaign, art_cycle = _artifact_scope_store(
        store, campaign_id, cycle_id, descend
    )
    artifact = _resolve_scope_artifact(
        art_store,
        scope=scope,
        name=name,
        campaign_id=art_campaign,
        cycle_id=art_cycle,
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

    # Held-out test fold from campaign config — display-only; never materialized. Read off
    # the typed knob (`CampaignConfig.dataset_split`), not a raw-dict re-parse: one field,
    # one reader, one shape.
    campaign_path = dataset_dir / "campaign.json"
    declared = (
        load_dataset_campaign_config(campaign_path).dataset_split
        if campaign_path.is_file()
        else None
    )

    return DatasetPreviewResponse(
        name=raw["name"],
        row_count=len(sample_lookup),
        split_test=declared.test if declared else None,
        items=items,
    )


class MeasurementDot(StrictModel):
    ord: str = Field(
        description="Opaque ordinal for lex sort + uniqueness (encodes ts/run/idx or round/cand).",
    )
    hit: bool
    label: str = Field(description="Short human label, e.g. 'R3 cand 2'.")


class SampleSeries(StrictModel):
    sample_id: int
    measurements: list[MeasurementDot]


class MeasurementSeriesResponse(StrictModel):
    name: str
    scope: HeatmapScope
    items: list[SampleSeries]


@datasets_router.get(
    "/{name}/measurement-series",
    response_model=MeasurementSeriesResponse,
)
def get_dataset_measurement_series(
    name: str,
    store: StoreDep,
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
    descend: str | None = Query(
        default=None,
        description="L4 inner-cycle descent tail — same seam as `/preview`'s `descend`.",
    ),
) -> MeasurementSeriesResponse:
    """Chronological per-sample series for the Meas heat-map column. Aligned to `/preview` order
    + limit so clients can zip by sample_id. `ord` is opaque (only used for row alignment).
    """
    raw, sample_lookup = _load_dataset_cache(_resolve_or_404(store, name))

    art_store, art_campaign, art_cycle = _artifact_scope_store(
        store, campaign_id, cycle_id, descend
    )
    artifact = _resolve_scope_artifact(
        art_store,
        scope=scope,
        name=name,
        campaign_id=art_campaign,
        cycle_id=art_cycle,
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
        assert art_campaign is not None and art_cycle is not None  # checked in resolver
        series = cycle_measurement_series(art_store, art_campaign, art_cycle, selected_set)
    elif scope == "campaign":
        assert art_campaign is not None  # checked in resolver
        series = campaign_measurement_series(art_store, art_campaign, selected_set)
    else:
        raw_series = measurement_series_for_samples(art_store, selected, dataset_name=name)
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


class DatasetPipelineResponse(StrictModel):
    """Target pipeline view for a dataset overlay. `view` drives the webapp chat-pane hero;
    `pipeline` is the full parsed schema for consumers needing per-node config; `connector` is
    the original-cased name for chip labelling; `origin_prompt_fields` is the origin PromptTemplate
    for the primary node (None when the dataset ships no `prompts/`).
    """

    name: str
    connector: str
    # The connector KIND read straight off the raw overlay (`termnorm` / `promptpotter` / …).
    # A connector-level fact, peer of `connector` — NOT a `PipelineSchema` field, so it is
    # surfaced here rather than smuggled through `pipeline` (the parser drops unknown keys).
    # The webapp branches self-optimization (pp-self) rendering on it.
    backend_type: str | None
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
def get_dataset_pipeline(name: str, store: StoreDep) -> DatasetPipelineResponse:
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
    backend_type = raw.get("backend_type")
    # Apply the dataset's default search-space narrowing so the setup editor opens
    # showing the recommended per-node locks (e.g. retrieval nodes origin-locked); the
    # draft's own overlay edits layer on top client-side. model/provider are always
    # optimizer-locked (operator-owned axes), so no per-campaign policy is read here.
    campaign_path = dataset_dir / "campaign.json"
    if campaign_path.is_file():
        cfg = load_dataset_campaign_config(campaign_path)
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
        backend_type=backend_type,
        pipeline=schema.model_dump(by_alias=True),
        view=schema.view.model_dump(by_alias=True) if schema.view is not None else None,
        node_config_schema=schema.node_config_schema(),
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
