from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Form, Header, Query, Request, UploadFile
from pydantic import Field

from promptpotter.application.datasets.authored import (
    dataset_campaign_path,
    load_dataset_campaign_config,
)
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
from promptpotter.application.intelligence.adaptive_queue_mechanism import marginal_hit_probability
from promptpotter.application.jobs.launcher.checkin import load_checkin_draft
from promptpotter.application.jobs.launcher.draft_build import draft_wire_with_locks
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.pipeline_schema import NodeConfigParam, NodeOutputSchema
from promptpotter.domain.results import HardSampleOrder
from promptpotter.domain.scoring import is_hit
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.archive_views import (
    campaign_measurement_series,
    cycle_measurement_series,
    measurement_series_for_samples,
)
from promptpotter.infrastructure.store.dataset_access import (
    dataset_pipeline_path,
    list_readable_datasets,
    readable_dataset_dir,
    readable_dataset_rows,
)
from promptpotter.infrastructure.store.io import read_json, read_yaml
from promptpotter.infrastructure.store.layout import campaign_root_dir_for
from promptpotter.infrastructure.store.stores import Stores, resolve_cycle_path
from promptpotter.presentation.api.deps import (
    StoresDep,
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
def list_datasets(stores: StoresDep) -> DatasetIndexResponse:
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
            for ref in list_readable_datasets(stores)
        ]
    )


def _too_large(observed: int | str) -> ContentTooLargeError:
    return ContentTooLargeError(
        f"Upload {observed} bytes exceeds the per-file cap of {MAX_UPLOAD_BYTES} bytes."
    )


async def _read_capped(request: Request, upload: UploadFile, cap: int) -> bytes:
    """Read ``upload`` into memory, aborting with 413 the moment it exceeds ``cap``. Fast-reject on
    the declared ``Content-Length``, then stream in chunks — the header can lie or be absent."""
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
    stores: StoresDep,
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
            stores=stores,
            blob=blob,
            filename=file.filename or "",
            slug=slug,
        )
    except IngestError as exc:
        exc.details["max_samples"] = MAX_SAMPLES
        raise
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
    stores: StoresDep,
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
    terms = parse_candidate_library(blob, file.filename or "")
    if not terms:
        raise PayloadInvalidError(
            "The candidate library has no usable entries (every line was blank).",
            code="ingest_failed",
            details={"reason": "empty"},
        )
    return await dispatch_draft_patch(
        stores,
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
    stores: StoresDep,
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
    draft = load_checkin_draft(stores, body.draft_id)
    if draft is None:
        raise NotFoundError(f"draft {body.draft_id!r} not found.", code="command_target_not_found")
    if body.column not in draft.headers:
        raise PayloadInvalidError(
            f"column {body.column!r} is not one of the dataset's columns {list(draft.headers)}."
        )
    bank = stores.checkin.load_bank(body.draft_id)
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
        stores,
        draft_id=body.draft_id,
        patch_raw={"candidate_library": terms},
        idempotency_key=idemp,
    )


@datasets_router.post("/{name}/draft")
def draft_from_existing_dataset(name: str, stores: StoresDep) -> dict[str, Any]:
    """Open an authored dataset's files as a durable check-in campaign.

    The direct path behind "open this dataset in the ingest panel" — a demo /
    benchmark / owned Origin becomes a prefilled check-in (``draft_id`` ==
    ``campaign_id``) without a browser-side CSV round-trip. Identity-gated through
    the same resolver as the other dataset reads; nothing runs until the operator
    starts it via ``/commands/start-checkin``.
    """
    dataset_dir = readable_dataset_dir(stores, name)
    draft = draft_from_dataset(stores=stores, dataset_dir=dataset_dir, dataset_name=name)
    return draft_wire_with_locks(draft)


def _load_dataset_rows(
    stores: Stores, name: str
) -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
    """Resolve *name*'s rows, normalising the sample-id key at the read boundary. Missing rows are
    not an unknown dataset but a bank-less one, so this answers an honest empty 200, not a 404."""
    raw = readable_dataset_rows(stores, name)
    if raw is None:
        return {"name": name, "items": []}, {}
    sample_lookup: dict[int, dict[str, Any]] = {}
    for item in raw["items"]:
        sid = int(item["sample_id"] if "sample_id" in item else item["id"])
        sample_lookup[sid] = item
    return raw, sample_lookup


def _artifact_scope_store(
    stores: Stores,
    campaign_id: str | None,
    cycle_id: str | None,
    descend: str | None,
) -> tuple[Stores, str | None, str | None]:
    """Store + ids the scope artifact lives in. With ``?descend=`` the viewed leaf is an L4 inner
    cycle in an off-registry sandbox, so the walk starts at the ROOT hop and both ids are required."""
    if not descend:
        return stores, campaign_id, cycle_id
    if not campaign_id or not cycle_id:
        raise BadRequestError("descend requires campaign_id and cycle_id (the root hop)")
    leaf_store, leaf = resolve_cycle_path(
        stores, (CycleHop(campaign_id=campaign_id, cycle_id=cycle_id), *decode_descend(descend))
    )
    return leaf_store, leaf.campaign_id, leaf.cycle_id


def _resolve_scope_artifact(
    stores: Stores,
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
        cycle_dir = get_cycle_dir_or_404(campaign_id, cycle_id, stores)
        path = cycle_dir / "hard_samples.json"
        if not path.is_file():
            return {}
        cycle_artifact: dict[str, Any] = read_json(path)
        return cycle_artifact
    if scope == "campaign":
        if not campaign_id:
            raise BadRequestError("scope=campaign requires campaign_id")
        campaign_dir = campaign_root_dir_for(stores.base_dir, campaign_id)
        if not campaign_dir.exists():
            raise NotFoundError(f"Campaign '{campaign_id}' not found")
        path = campaign_dir / "hard_samples.json"
        if not path.is_file():
            return {}
        campaign_artifact: dict[str, Any] = read_json(path)
        return campaign_artifact
    # `dataset` — always per-dataset (cross-dataset pooling is meaningless).
    return build_archive_hard_samples_artifact(
        stores,
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


@dataclass(frozen=True)
class _LeaderboardPage:
    """One page of the hard-sample leaderboard, resolved ONCE for the two routes that serve it.

    The rank lives here because it needs both halves — the Rasch maps AND the per-sample
    series — and neither route holds both alone, which is why the ordering had drifted into
    the browser, the one place they meet.
    """

    raw: dict[str, Any]
    sample_lookup: dict[int, dict[str, Any]]
    artifact: dict[str, Any]
    delta_map: dict[int, float]
    delta_se_map: dict[int, float]
    n_obs_map: dict[int, int]
    pick_score_map: dict[int, float]
    series: dict[int, list[dict[str, Any]]]
    order: HardSampleOrder
    ranked: list[int]
    """Page sample_ids in served order; index + 1 IS each row's ``hard_sample_rank``."""


def _resolve_leaderboard_page(
    stores: Stores,
    *,
    name: str,
    scope: HeatmapScope,
    campaign_id: str | None,
    cycle_id: str | None,
    descend: str | None,
    limit: int,
    max_unmeasured: int | None,
    order: HardSampleOrder | None,
) -> _LeaderboardPage:
    """Resolve the page, then rank it — the single owner of hard-sample order.

    Selection (δ_s-desc, unmeasured trimmed, then ``limit``) picks WHICH rows the page holds;
    the rank picks the order they are read in. Deliberately different keys: selection cannot
    depend on the series, which is only fetched for rows already selected.

    *Measured* is dot presence in THIS scope, not a δ entry — the ruler persists across rounds
    and inherits from parent fits, so a sample carries a δ it never earned here.
    """
    raw, sample_lookup = _load_dataset_rows(stores, name)
    art_store, art_campaign, art_cycle = _artifact_scope_store(
        stores, campaign_id, cycle_id, descend
    )
    artifact = _resolve_scope_artifact(
        art_store, scope=scope, name=name, campaign_id=art_campaign, cycle_id=art_cycle
    )
    rasch = artifact.get("rasch", {})
    delta_map: dict[int, float] = {int(k): float(v) for k, v in rasch.get("delta", {}).items()}
    delta_se_map: dict[int, float] = {
        int(k): float(v) for k, v in rasch.get("delta_se", {}).items()
    }
    n_obs_map: dict[int, int] = {
        int(k): int(v) for k, v in rasch.get("n_obs_per_sample", {}).items()
    }
    pick_score_block = artifact.get("pick_score", {}).get("per_sample", {})
    pick_score_map: dict[int, float] = {int(k): float(v) for k, v in pick_score_block.items()}

    fitted = {sid for sid in delta_map if sid in sample_lookup}
    selection = sorted(sample_lookup.keys(), key=lambda s: (-delta_map.get(s, 0.0), s))
    selected = _trim_unmeasured(selection, fitted, max_unmeasured)[:limit]

    series = _page_series(
        art_store, scope=scope, name=name, campaign=art_campaign, cycle=art_cycle, page=selected
    )

    resolved = order or _dataset_hard_sample_order(stores, name)
    key_map = pick_score_map if resolved == "info_gain" else delta_map
    measured = {sid for sid in selected if series.get(sid)}
    # Unmeasured rows carry a prior-fitted key they never earned in this scope, so they trail
    # on sample_id rather than sorting into the measured block on it.
    ranked = sorted(
        selected,
        key=lambda sid: (0, -key_map.get(sid, 0.0), sid) if sid in measured else (1, 0.0, sid),
    )
    return _LeaderboardPage(
        raw=raw,
        sample_lookup=sample_lookup,
        artifact=artifact,
        delta_map=delta_map,
        delta_se_map=delta_se_map,
        n_obs_map=n_obs_map,
        pick_score_map=pick_score_map,
        series=series,
        order=resolved,
        ranked=ranked,
    )


def _page_series(
    art_store: Stores,
    *,
    scope: HeatmapScope,
    name: str,
    campaign: str | None,
    cycle: str | None,
    page: list[int],
) -> dict[int, list[dict[str, Any]]]:
    """Three scopes, three sources, ONE dot shape — each producer emits `{ord, fitness,
    label}` itself, so nothing is re-mapped here."""
    if scope == "cycle":
        assert campaign is not None and cycle is not None  # checked in resolver
        return cycle_measurement_series(
            art_store, CycleHop(campaign_id=campaign, cycle_id=cycle), set(page)
        )
    if scope == "campaign":
        assert campaign is not None  # checked in resolver
        return campaign_measurement_series(art_store, campaign, set(page))
    return {
        sid: [{"ord": m["ord"], "fitness": m["fitness"], "label": m["label"]} for m in ms]
        for sid, ms in measurement_series_for_samples(art_store, page, dataset_name=name).items()
    }


def _dataset_hard_sample_order(stores: Stores, name: str) -> HardSampleOrder:
    """The campaign default for this dataset, read off the same authored `campaign.yaml` the
    `dataset_split` footer reads. Absent file ⇒ the field default."""
    campaign_path = dataset_campaign_path(readable_dataset_dir(stores, name))
    if not campaign_path.is_file():
        return "info_gain"
    return load_dataset_campaign_config(campaign_path).hard_sample_order


class DatasetItem(StrictModel):
    sample_id: int
    query: str
    ground_truth: str
    task: str | None = None
    hard_sample_rank: int = Field(
        description="1-based position in the served hard-sample ranking under this response's "
        "`order`. THE ordering — a client renders rows in it and never re-derives one, since "
        "an ordering is a score and a locally-sorted one silently answers a different "
        "question in the same slot. Rows measured in this scope rank first; the rest trail.",
    )
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
    order: HardSampleOrder = Field(
        description="The key `items` are ranked by — the request's `order` when it named one, "
        "else the dataset's `CampaignConfig.hard_sample_order`. Echoed so a client that sent "
        "no override can label what it is showing without guessing the default.",
    )
    items: list[DatasetItem]


@datasets_router.get("/{name}/preview", response_model=DatasetPreviewResponse)
def get_dataset_preview(
    name: str,
    stores: StoresDep,
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
    order: Annotated[
        HardSampleOrder | None,
        Query(
            description="Override the ranking key for this read; unset ⇒ the dataset's "
            "`CampaignConfig.hard_sample_order`. The resolved value comes back on `order`.",
        ),
    ] = None,
) -> DatasetPreviewResponse:
    """Hard-sample leaderboard, served in rank order — `items[i].hard_sample_rank == i + 1`.

    Rows measured in this scope rank first by the `order` key, the rest trail by sample_id.
    *Measured* is dot presence in the companion `/measurement-series`, not a δ entry: the
    Rasch ruler persists across rounds and inherits from parent fits, so it overcounts on its
    own. Both routes resolve the page through one function, so they cannot disagree.
    """
    dataset_dir = readable_dataset_dir(stores, name)
    page = _resolve_leaderboard_page(
        stores,
        name=name,
        scope=scope,
        campaign_id=campaign_id,
        cycle_id=cycle_id,
        descend=descend,
        limit=limit,
        max_unmeasured=max_unmeasured,
        order=order,
    )
    sample_lookup = page.sample_lookup

    # Seed-centred marginal hit prob — see `adaptive_queue_mechanism.marginal_hit_probability`.
    rasch = page.artifact.get("rasch", {})
    sigma_theta = float(rasch.get("sigma_theta", 0.0))
    theta_map: dict[str, float] = {str(k): float(v) for k, v in rasch.get("theta", {}).items()}
    candidate_order_raw = page.artifact.get("candidate_order") or []
    seed_theta = (
        theta_map.get(str(candidate_order_raw[0]), 0.0)
        if candidate_order_raw and theta_map
        else 0.0
    )
    var_c = sigma_theta**2  # brand-new candidate's ability prior variance

    def _p_hat(sid: int) -> float | None:
        if sid not in page.delta_map:
            return None
        return marginal_hit_probability(
            mu_c=seed_theta,
            var_c=var_c,
            delta_s=page.delta_map[sid],
            se_delta_s=page.delta_se_map.get(sid, 0.0),
        )

    items = [
        DatasetItem(
            sample_id=sid,
            query=sample_lookup[sid]["query"],
            ground_truth=sample_lookup[sid]["ground_truth"],
            task=sample_lookup[sid].get("task"),
            hard_sample_rank=rank,
            n_obs=page.n_obs_map.get(sid, 0),
            delta=page.delta_map.get(sid),
            delta_se=page.delta_se_map.get(sid),
            p_hat=_p_hat(sid),
            pick_score=page.pick_score_map.get(sid),
        )
        for rank, sid in enumerate(page.ranked, start=1)
    ]

    # Held-out test fold from campaign config — display-only; never materialized. Read off
    # the typed knob (`CampaignConfig.dataset_split`), not a raw-dict re-parse: one field,
    # one reader, one shape.
    campaign_path = dataset_campaign_path(dataset_dir)
    declared = (
        load_dataset_campaign_config(campaign_path).dataset_split
        if campaign_path.is_file()
        else None
    )

    return DatasetPreviewResponse(
        name=page.raw["name"],
        row_count=len(sample_lookup),
        split_test=declared.test if declared else None,
        order=page.order,
        items=items,
    )


class MeasurementDot(StrictModel):
    ord: str = Field(
        description="Opaque ordinal for lex sort + uniqueness (encodes ts/run/idx or round/cand).",
    )
    fitness: float = Field(
        description="Graded per-sample score in [0,1] under the active scorer. Binary "
        "scorers emit exactly 0.0 or 1.0; render a shade, not a HIT/MISS boolean.",
    )
    label: str = Field(description="Short human label, e.g. 'R3 cand 2'.")


class SampleSeries(StrictModel):
    sample_id: int
    measurements: list[MeasurementDot]
    n_hits: int = Field(
        description="How many of THIS series' measurements maxed out the active scorer "
        "(`domain.scoring.is_hit`, the one definition of that threshold). Served rather than "
        "counted client-side so the tally and the dots it summarises cannot disagree — and so "
        "a graded scorer, whose ceiling is unreachable, reports 0 against a mean that is not 0.",
    )
    mean_fitness: float | None = Field(
        description="Mean graded fitness over this series; `None` when it holds no measurement. "
        "The rate to read on a graded scorer, where `n_hits` is structurally 0.",
    )


class MeasurementSeriesResponse(StrictModel):
    name: str
    scope: HeatmapScope
    items: list[SampleSeries]
    total_measurements: int = Field(
        description="Measurements across every series in `items` — the denominator of the "
        "roster's headline, served so the reader adds nothing up.",
    )
    total_hits: int = Field(
        description="`SampleSeries.n_hits` summed across `items`. Structurally 0 on a graded "
        "scorer; read `mean_fitness` there.",
    )
    mean_fitness: float | None = Field(
        description="Mean graded fitness across every measurement in `items`; `None` when the "
        "scope holds none. The headline rate on any scorer, graded or binary.",
    )


def _sample_series(sample_id: int, dots: list[dict[str, Any]]) -> SampleSeries:
    """One sample's dots plus the aggregates over exactly those dots. Every scope builds its dots
    from a different source, so a second pass anywhere else would be counting a different set."""
    fitness = [float(d["fitness"]) for d in dots]
    return SampleSeries(
        sample_id=sample_id,
        measurements=[MeasurementDot(**d) for d in dots],
        n_hits=sum(1 for f in fitness if is_hit(f)),
        mean_fitness=sum(fitness) / len(fitness) if fitness else None,
    )


@datasets_router.get(
    "/{name}/measurement-series",
    response_model=MeasurementSeriesResponse,
)
def get_dataset_measurement_series(
    name: str,
    stores: StoresDep,
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
    order: Annotated[
        HardSampleOrder | None,
        Query(
            description="Same override as `/preview`'s; send the same value to keep the two "
            "responses in one order.",
        ),
    ] = None,
) -> MeasurementSeriesResponse:
    """Chronological per-sample series for the Meas heat-map column. Same page, same order as
    `/preview` — both resolve it through `_resolve_leaderboard_page`, so alignment is a shared
    function rather than two matching sort keys. `ord` is opaque (only row alignment).
    """
    readable_dataset_dir(stores, name)  # 404s an unknown slug before we answer with an empty bank
    page = _resolve_leaderboard_page(
        stores,
        name=name,
        scope=scope,
        campaign_id=campaign_id,
        cycle_id=cycle_id,
        descend=descend,
        limit=limit,
        max_unmeasured=max_unmeasured,
        order=order,
    )
    items = [_sample_series(sid, page.series.get(sid, [])) for sid in page.ranked]
    n_meas = sum(len(it.measurements) for it in items)
    return MeasurementSeriesResponse(
        name=page.raw["name"],
        scope=scope,
        items=items,
        total_measurements=n_meas,
        total_hits=sum(it.n_hits for it in items),
        mean_fitness=(
            sum(float(d.fitness) for it in items for d in it.measurements) / n_meas
            if n_meas
            else None
        ),
    )


class DatasetPipelineResponse(StrictModel):
    """Target pipeline view for a dataset overlay. `view` drives the webapp chat-pane hero;
    `pipeline` is the full parsed schema for consumers needing per-node config; `connector` is
    the original-cased name for chip labelling.
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
    # Every param each node carries, keyed by node — COMPLETE (prompt + nested params
    # included, carrying no value), because `optimizer_tunable` is summed per node to
    # answer "is this node optimizer-locked". The config editor filters to the widget
    # kinds; `optimizer_locked` marks what the optimizer may never permute
    # (model/provider), which the operator may still set on a fork seed.
    node_config_schema: dict[str, list[NodeConfigParam]]
    # Per-node structured-output contract (read-only) — the steer panel shows it
    # beside the config so the operator sees the WHOLE node (model + params +
    # prompt + the structured output it produces). None for nodes with no schema.
    node_output_schema: dict[str, NodeOutputSchema | None]


@datasets_router.get("/{name}/pipeline", response_model=DatasetPipelineResponse)
def get_dataset_pipeline(name: str, stores: StoresDep) -> DatasetPipelineResponse:
    """Return the dataset overlay's parsed pipeline schema, graph view, and per-node
    config + output schema.

    Identity-gated through the same resolver as the other dataset reads — there is
    no unauthenticated path to a benchmark's pipeline/overlay config.
    """
    dataset_dir = readable_dataset_dir(stores, name)
    pipeline_path = dataset_pipeline_path(dataset_dir)
    if not pipeline_path.is_file():
        raise NotFoundError(f"Dataset '{name}' has no pipeline.yaml")
    raw = read_yaml(pipeline_path)
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
    campaign_path = dataset_campaign_path(dataset_dir)
    if campaign_path.is_file():
        cfg = load_dataset_campaign_config(campaign_path)
        schema = schema.narrow(cfg.optimizer_narrowing)
    return DatasetPipelineResponse(
        name=name,
        connector=connector,
        backend_type=backend_type,
        pipeline=schema.model_dump(by_alias=True),
        view=schema.view.model_dump(by_alias=True) if schema.view is not None else None,
        node_config_schema=schema.node_config_schema(),
        node_output_schema=schema.node_output_schemas(),
    )


__all__ = [
    "DatasetItem",
    "DatasetPipelineResponse",
    "DatasetPreviewResponse",
    "MeasurementDot",
    "MeasurementSeriesResponse",
    "SampleSeries",
]
