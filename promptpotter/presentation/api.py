"""HTTP read API — backend storage + campaign registry endpoints.

Two routers in one module so they share the storage / pipeline-discovery
imports and the ``StoreDep`` dependency:

1. **Backend storage** (``backends_router``) — manages backend connections,
   syncs experiments from backends in their native format, and exposes
   pipeline discovery.

2. **Campaign registry** (``campaigns_router``) — lists + views
   optimization campaigns, nested under ``/backends/{backend_id}/campaigns``.
"""

import logging
import re
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from promptpotter.application.pipeline_discovery import compute_pipeline_view
from promptpotter.domain.backend import BackendConnection
from promptpotter.infrastructure.backend import BackendClient
from promptpotter.infrastructure.store import Stores, build_stores

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
