"""HTTP read API — backend storage + campaign registry + per-cycle live reads.

Three routers in one module so they share the storage / pipeline-discovery
imports and the ``StoreDep`` dependency:

1. **Backend storage** (``backends_router``) — manages backend connections,
   syncs experiments from backends in their native format, and exposes
   pipeline discovery.

2. **Campaign registry** (``campaigns_router``) — lists + views
   optimization campaigns, nested under ``/backends/{backend_id}/campaigns``.

3. **Per-cycle live reads** (also on ``campaigns_router``) — dashboard
   passthrough, output.log tail, log.md, ledger reads + filtered views
   (decisions, forks). The first real consumer of the per-cycle
   ``RunLedger``; validates the record types carry what a webapp needs.
"""

import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from promptpotter.application.pipeline_discovery import compute_pipeline_view
from promptpotter.domain.backend import BackendConnection
from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.run_records import Decision, DecisionKind
from promptpotter.infrastructure.backend import BackendClient
from promptpotter.infrastructure.ledger import RunLedger
from promptpotter.infrastructure.store import Stores, build_stores
from promptpotter.infrastructure.store.stores import campaign_dir_for, root_dir_for

StoreDep = Annotated[Stores, Depends(build_stores)]

logger = logging.getLogger(__name__)

backends_router = APIRouter(prefix="/backends", tags=["Backends"])


class RegisterBackendRequest(BaseModel):
    name: str = Field(..., description="Human-readable name")
    backend_type: str = Field(..., description="Backend type, e.g. 'default'")
    base_url: str = Field(..., description="Backend API base URL")
    id: str | None = Field(
        None,
        description="Custom ID (auto-generated from name if omitted)",
    )


class RegisterBackendResponse(BaseModel):
    id: str = Field(description="Backend identifier")
    name: str = Field(description="Human-readable backend name")
    backend_type: str = Field(description="Backend type (e.g. 'default')")
    base_url: str = Field(description="Backend API base URL")
    created_at: str = Field(description="ISO 8601 creation timestamp")


class SyncResponse(BaseModel):
    backend_id: str = Field(description="Backend identifier")
    experiments_synced: int = Field(description="Number of experiments synced from backend")
    synced_at: str = Field(description="ISO 8601 sync timestamp")


class PipelineViewResponse(BaseModel):
    backend_id: str = Field(description="Backend identifier")
    backend_pipeline: dict[str, Any] = Field(description="Full PipelineSchema as dict")
    computed_nodes: list[dict[str, Any]] = Field(description="Computed PipelineNode dicts")
    fetched_at: str = Field(description="ISO 8601 fetch timestamp")
    source: str = Field(description="Pipeline source: 'live', 'cached', or 'default'")


def _get_backend_or_404(backend_id: str, store: Stores) -> BackendConnection:
    backend = store.backends.get(backend_id)
    if not backend:
        raise HTTPException(status_code=404, detail=f"Backend '{backend_id}' not found")
    return backend


@backends_router.post("", response_model=RegisterBackendResponse, status_code=201)
async def register_backend(request: RegisterBackendRequest, store: StoreDep):
    """Register a new backend connection."""
    backend_id = request.id or re.sub(r"[^a-z0-9]+", "-", request.name.lower().strip()).strip("-")
    if store.backends.get(backend_id):
        raise HTTPException(status_code=409, detail=f"Backend '{backend_id}' already exists")

    backend = BackendConnection(
        id=backend_id,
        name=request.name,
        backend_type=request.backend_type,
        base_url=request.base_url.rstrip("/"),
    )
    store.backends.register(backend)

    return RegisterBackendResponse(
        id=backend.id,
        name=backend.name,
        backend_type=backend.backend_type,
        base_url=backend.base_url,
        created_at=backend.created_at,
    )


@backends_router.get("", response_model=list[RegisterBackendResponse])
async def list_backends(store: StoreDep):
    """List all registered backends."""
    return [
        RegisterBackendResponse(
            id=b.id,
            name=b.name,
            backend_type=b.backend_type,
            base_url=b.base_url,
            created_at=b.created_at,
        )
        for b in store.backends.list_all()
    ]


@backends_router.get("/{backend_id}", response_model=RegisterBackendResponse)
async def get_backend(backend_id: str, store: StoreDep):
    """Get backend details."""
    b = _get_backend_or_404(backend_id, store)
    return RegisterBackendResponse(
        id=b.id,
        name=b.name,
        backend_type=b.backend_type,
        base_url=b.base_url,
        created_at=b.created_at,
    )


@backends_router.post("/{backend_id}/sync", response_model=SyncResponse)
async def sync_experiments(backend_id: str, store: StoreDep):
    """Sync experiments from backend API into project store (verbatim)."""
    backend = _get_backend_or_404(backend_id, store)
    client = BackendClient(backend.base_url)

    try:
        count = await client.sync_experiments(store, backend_id)
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to sync from {backend.base_url}: {e}",
        ) from e

    now = datetime.now(UTC).isoformat()
    backend.last_synced_at = now
    store.backends.update(backend)

    return SyncResponse(
        backend_id=backend_id,
        experiments_synced=count,
        synced_at=now,
    )


@backends_router.get("/{backend_id}/experiments")
async def list_experiments(backend_id: str, store: StoreDep):
    """List synced experiments (from local store, native format)."""
    _get_backend_or_404(backend_id, store)

    # First try the experiments list file
    data = store.backends.load_sync(backend_id, "experiments.json")
    if data:
        return data

    # Fall back to individual files
    experiments = store.backends.list_synced_experiments(backend_id)
    if not experiments:
        raise HTTPException(
            status_code=404,
            detail="No synced experiments. Run POST /sync first.",
        )
    return {"experiments": experiments}


@backends_router.get("/{backend_id}/experiments/{experiment_id}")
async def get_experiment(backend_id: str, experiment_id: str, store: StoreDep):
    """Get a synced experiment in native backend format."""
    _get_backend_or_404(backend_id, store)
    data = store.backends.load_sync(backend_id, f"experiments/{experiment_id}.json")
    if not data:
        raise HTTPException(
            status_code=404,
            detail=f"Experiment '{experiment_id}' not synced. Run POST /sync first.",
        )
    return data


@backends_router.get("/{backend_id}/pipeline", response_model=PipelineViewResponse)
async def get_pipeline(backend_id: str, store: StoreDep):
    """Dynamic pipeline view from the backend."""
    backend = _get_backend_or_404(backend_id, store)
    client = BackendClient(backend.base_url)

    view = await compute_pipeline_view(client)

    return PipelineViewResponse(
        backend_id=backend_id,
        backend_pipeline=view["backend_pipeline"],
        computed_nodes=view["computed_nodes"],
        fetched_at=view["fetched_at"],
        source=view["source"],
    )


# ===========================================================================
# Campaign registry endpoints — REST API for listing + viewing campaigns,
# nested under /backends/{backend_id}/campaigns.
# ===========================================================================

campaigns_router = APIRouter()


class CampaignSummary(BaseModel):
    campaign_id: str = Field(description="Unique campaign identifier")
    name: str = Field(description="Human-readable campaign name")
    status: str = Field(description="Campaign status: active, completed, or stopped")
    n_trials: int = Field(description="Total number of completed trial rounds")
    best_accuracy: float = Field(description="Highest accuracy achieved across all trials")
    baseline_accuracy: float = Field(description="Initial accuracy before optimization")
    created_at: str = Field(description="ISO 8601 creation timestamp")
    updated_at: str = Field(description="ISO 8601 last-update timestamp")


class CampaignListResponse(BaseModel):
    campaigns: list[CampaignSummary] = Field(description="List of campaign summaries")
    total: int = Field(description="Total number of campaigns")


class TrialSummary(BaseModel):
    trial_id: str = Field(description="Unique trial identifier")
    round: int = Field(description="Round number within the campaign")
    label: str = Field(description="Human-readable label (e.g. 'round_3')")
    prompt_fields_id: str = Field(description="OptSearchPoint ID for this trial's prompt")
    accuracy: float = Field(description="Accuracy achieved in this trial")
    hits: int = Field(description="Number of correct matches")
    total: int = Field(description="Total number of evaluated queries")
    improved: bool = Field(description="Whether this trial improved over the previous best")
    created_at: str = Field(description="ISO 8601 creation timestamp")


class CampaignDetailResponse(BaseModel):
    campaign_id: str = Field(description="Unique campaign identifier")
    name: str = Field(description="Human-readable campaign name")
    backend_id: str = Field(description="Backend this campaign optimizes against")
    status: str = Field(description="Campaign status: active, completed, or stopped")
    config: dict[str, Any] = Field(description="Full campaign configuration used for this run")
    n_trials: int = Field(description="Total number of completed trial rounds")
    best_accuracy: float = Field(description="Highest accuracy achieved across all trials")
    best_trial_id: str | None = Field(description="Trial ID of the best-performing round")
    baseline_accuracy: float = Field(description="Initial accuracy before optimization")
    created_at: str = Field(description="ISO 8601 creation timestamp")
    updated_at: str = Field(description="ISO 8601 last-update timestamp")
    trials: list[TrialSummary] = Field(description="Ordered list of trial summaries")
    langfuse_trace_id: str | None = Field(
        default=None,
        description="Langfuse trace ID if observability is enabled",
    )


@campaigns_router.get(
    "/backends/{backend_id}/campaigns",
    response_model=CampaignListResponse,
)
async def list_campaigns(
    store: StoreDep,
    backend_id: str,
):
    """List all campaigns for a backend."""
    campaigns = store.campaigns.list_all(backend_id)
    return CampaignListResponse(
        campaigns=[CampaignSummary(**c) for c in campaigns],
        total=len(campaigns),
    )


@campaigns_router.get(
    "/backends/{backend_id}/campaigns/{campaign_id}",
    response_model=CampaignDetailResponse,
)
async def get_campaign(
    store: StoreDep,
    backend_id: str,
    campaign_id: str,
):
    """Get campaign detail with trial summaries."""
    data = store.campaigns.load(backend_id, campaign_id)
    if data is None:
        raise HTTPException(404, f"Campaign not found: {campaign_id}")
    return CampaignDetailResponse(**data)


@campaigns_router.get(
    "/backends/{backend_id}/campaigns/{campaign_id}/trials/{round_num}",
    response_model=dict[str, Any],
)
async def get_trial(
    store: StoreDep,
    backend_id: str,
    campaign_id: str,
    round_num: int,
):
    """Get full trial detail for a specific round."""
    trial = store.campaigns.load_trial(backend_id, campaign_id, round_num)
    if trial is None:
        raise HTTPException(404, f"Trial round {round_num} not found")
    return trial


# ===========================================================================
# Per-cycle live reads — dashboard, log, ledger
# Webapp-facing endpoints that pass through the on-disk artifacts the
# operator workflows already use, plus filtered ledger views (decisions,
# forks) derived from the typed RunRecord stream.
# ===========================================================================


def _read_text_or_404(path: Path, label: str) -> str:
    if not path.exists():
        raise HTTPException(404, f"{label} not found at {path.name}")
    return path.read_text(encoding="utf-8")


class DashboardSnapshot(BaseModel):
    """Pass-through envelope for ``dashboard.json``.

    Shape matches whatever ``LiveDashboardProjection`` writes — kept as
    a free-form ``data`` dict so dashboard schema changes don't break
    the API contract.
    """

    cycle_id: str = Field(description="Cycle the dashboard belongs to")
    data: dict[str, Any] = Field(description="Verbatim dashboard.json contents")


class LogTailResponse(BaseModel):
    cycle_id: str = Field(description="Cycle the log belongs to")
    lines: list[str] = Field(description="Last N lines of output.log")
    truncated: bool = Field(description="True if older lines exist beyond this tail")


class RunRecordEnvelope(BaseModel):
    """One typed ledger record + its offset.

    ``record_type`` discriminates Decision / Phase / Snapshot;
    ``payload`` carries the model's fields verbatim (Pydantic
    model_dump). Consumers either route by record_type or filter on
    Decision.kind for gating events.
    """

    offset: int = Field(description="Position in events.jsonl, monotonic per cycle")
    record_type: str = Field(description="One of 'decision' | 'phase' | 'snapshot'")
    payload: dict[str, Any] = Field(description="Verbatim record fields")


class LedgerSliceResponse(BaseModel):
    cycle_id: str = Field(description="Cycle the records belong to")
    records: list[RunRecordEnvelope] = Field(description="Records since the requested offset")
    next_offset: int = Field(description="Pass back as ?since= for incremental polling")


class DecisionEnvelope(BaseModel):
    offset: int = Field(description="Position in the cycle's ledger")
    kind: str = Field(description="DecisionKind value (round_winner, fork_cut, ...)")
    round: int | None = Field(default=None, description="Round the decision was made in")
    inputs_ref: dict[str, Any] = Field(description="Inputs the decision was derived from")
    outcome: Any = Field(description="The decision's outcome")
    data: dict[str, Any] = Field(description="Archival sidecar (LLM output, diagnostics)")
    timestamp: str = Field(description="ISO 8601 emit time")


class DecisionsResponse(BaseModel):
    cycle_id: str = Field(description="Cycle the decisions belong to")
    decisions: list[DecisionEnvelope] = Field(description="All Decision records in append order")


class ForkRef(BaseModel):
    fork_cycle_id: str = Field(description="The forked sibling cycle's id")
    from_round: int = Field(description="Round at which the parent was cut")
    forked_at: str = Field(description="ISO 8601 fork time (from FORK_CUT.data.forked_at)")


class ForksResponse(BaseModel):
    parent_cycle_id: str = Field(description="The parent cycle this list applies to")
    forks: list[ForkRef] = Field(description="Children minted from this parent")


def _open_cycle_ledger_or_404(cycle_id: str, store: Stores) -> RunLedger:
    """Open the per-cycle ledger; 404 if the cycle dir doesn't exist."""
    cycle_dir = campaign_dir_for(store.base_dir, cycle_id)
    if not cycle_dir.exists():
        raise HTTPException(404, f"Cycle '{cycle_id}' not found")
    return RunLedger.open(CycleDir(cycle_dir))


def _record_to_envelope(record: Any, offset: int) -> RunRecordEnvelope:
    return RunRecordEnvelope(
        offset=offset,
        record_type=record.record_type,
        payload=record.model_dump(),
    )


@campaigns_router.get(
    "/campaigns/{cycle_id}/dashboard",
    response_model=DashboardSnapshot,
)
async def get_cycle_dashboard(store: StoreDep, cycle_id: str):
    """Live dashboard.json snapshot for a cycle (family-root-bound)."""
    import json

    root_dir = root_dir_for(store.base_dir, cycle_id)
    text = _read_text_or_404(root_dir / "dashboard.json", "dashboard.json")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HTTPException(500, f"dashboard.json corrupt: {exc}") from exc
    return DashboardSnapshot(cycle_id=cycle_id, data=data)


@campaigns_router.get(
    "/campaigns/{cycle_id}/log",
    response_model=LogTailResponse,
)
async def get_cycle_log_tail(
    store: StoreDep,
    cycle_id: str,
    tail: int = Query(200, ge=1, le=10_000, description="Last N lines to return"),
):
    """Last N lines of output.log (family-root-bound)."""
    root_dir = root_dir_for(store.base_dir, cycle_id)
    text = _read_text_or_404(root_dir / "output.log", "output.log")
    all_lines = text.splitlines()
    if len(all_lines) <= tail:
        return LogTailResponse(cycle_id=cycle_id, lines=all_lines, truncated=False)
    return LogTailResponse(cycle_id=cycle_id, lines=all_lines[-tail:], truncated=True)


class LogMdResponse(BaseModel):
    cycle_id: str = Field(description="Cycle the markdown belongs to")
    markdown: str = Field(description="Verbatim log.md source")


@campaigns_router.get(
    "/campaigns/{cycle_id}/log_md",
    response_model=LogMdResponse,
)
async def get_cycle_log_md(store: StoreDep, cycle_id: str) -> LogMdResponse:
    """Pre-rendered log.md (per-cycle audit) — markdown source in a typed envelope."""
    cycle_dir = campaign_dir_for(store.base_dir, cycle_id)
    text = _read_text_or_404(cycle_dir / "log.md", "log.md")
    return LogMdResponse(cycle_id=cycle_id, markdown=text)


@campaigns_router.get(
    "/campaigns/{cycle_id}/ledger",
    response_model=LedgerSliceResponse,
)
async def get_cycle_ledger(
    store: StoreDep,
    cycle_id: str,
    since: int = Query(0, ge=0, description="Skip records before this offset"),
    limit: int = Query(1000, ge=1, le=50_000, description="Max records to return"),
):
    """Typed RunRecord stream from the cycle's events.jsonl, since the given offset.

    Pass back ``next_offset`` as ``?since=`` for incremental polling —
    the webapp's primary live-state read path. Up to ``limit`` records
    per call so the operator can stream a long history without one-shot
    huge responses.
    """
    ledger = _open_cycle_ledger_or_404(cycle_id, store)
    records: list[RunRecordEnvelope] = []
    offset = since
    for rec in ledger.iter(since=since):
        records.append(_record_to_envelope(rec, offset))
        offset += 1
        if len(records) >= limit:
            break
    return LedgerSliceResponse(
        cycle_id=cycle_id,
        records=records,
        next_offset=offset,
    )


@campaigns_router.get(
    "/campaigns/{cycle_id}/decisions",
    response_model=DecisionsResponse,
)
async def get_cycle_decisions(store: StoreDep, cycle_id: str):
    """All Decision records from the cycle's ledger, in append order.

    Filtered view over ``GET /ledger`` for the common gating-event use
    case. Includes archival kinds (probe, fork_cut) — consumers filter
    by ``kind`` if they only want REPLAYED ones.
    """
    ledger = _open_cycle_ledger_or_404(cycle_id, store)
    out: list[DecisionEnvelope] = []
    for offset, rec in enumerate(ledger.iter()):
        if isinstance(rec, Decision):
            out.append(
                DecisionEnvelope(
                    offset=offset,
                    kind=rec.kind.value,
                    round=rec.round,
                    inputs_ref=rec.inputs_ref,
                    outcome=rec.outcome,
                    data=rec.data,
                    timestamp=rec.timestamp,
                )
            )
    return DecisionsResponse(cycle_id=cycle_id, decisions=out)


@campaigns_router.get(
    "/campaigns/{cycle_id}/forks",
    response_model=ForksResponse,
)
async def get_cycle_forks(store: StoreDep, cycle_id: str):
    """Sibling forks minted from this cycle, derived from FORK_CUT records.

    Each fork's metadata comes from a single ``Decision(kind=FORK_CUT)``
    in the parent's ledger — ``outcome`` is the child cycle_id,
    ``inputs_ref.from_round`` is the cut point, ``data.forked_at`` is
    the wall-clock fork time.
    """
    ledger = _open_cycle_ledger_or_404(cycle_id, store)
    forks: list[ForkRef] = []
    for rec in ledger.iter():
        if isinstance(rec, Decision) and rec.kind is DecisionKind.FORK_CUT:
            forks.append(
                ForkRef(
                    fork_cycle_id=str(rec.outcome),
                    from_round=int(rec.inputs_ref.get("from_round", -1)),
                    forked_at=str(rec.data.get("forked_at", "")),
                )
            )
    return ForksResponse(parent_cycle_id=cycle_id, forks=forks)
