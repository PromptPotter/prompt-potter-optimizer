"""
Project-based backend storage endpoints.

Manages backend connections, syncs experiments from backends in their
native format, and exposes pipeline discovery.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.models.backend import BackendConnection
from api.services.backend_client import BackendClient
from api.services.pipeline_discovery import compute_pipeline_view
from api.services.project_store import ProjectStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/backends", tags=["Backends"])


def _get_store() -> ProjectStore:
    return ProjectStore()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class RegisterBackendRequest(BaseModel):
    name: str = Field(..., description="Human-readable name")
    backend_type: str = Field(..., description="Backend type, e.g. 'termnorm'")
    base_url: str = Field(..., description="Backend API base URL")
    id: str | None = Field(
        None, description="Custom ID (auto-generated from name if omitted)",
    )


class RegisterBackendResponse(BaseModel):
    id: str = Field(description="Backend identifier")
    name: str = Field(description="Human-readable backend name")
    backend_type: str = Field(description="Backend type (e.g. 'termnorm')")
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slugify(name: str) -> str:
    """Convert a name to a filesystem-safe slug."""
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def _get_backend_or_404(backend_id: str, store: ProjectStore | None = None) -> BackendConnection:
    store = store or _get_store()
    backend = store.backends.get(backend_id)
    if not backend:
        raise HTTPException(status_code=404, detail=f"Backend '{backend_id}' not found")
    return backend


# ---------------------------------------------------------------------------
# Backend CRUD
# ---------------------------------------------------------------------------


@router.post("", response_model=RegisterBackendResponse, status_code=201)
async def register_backend(request: RegisterBackendRequest):
    """Register a new backend connection."""
    store = _get_store()
    backend_id = request.id or _slugify(request.name)
    if store.backends.get(backend_id):
        raise HTTPException(
            status_code=409, detail=f"Backend '{backend_id}' already exists"
        )

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


@router.get("", response_model=list[RegisterBackendResponse])
async def list_backends():
    """List all registered backends."""
    store = _get_store()
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


@router.get("/{backend_id}", response_model=RegisterBackendResponse)
async def get_backend(backend_id: str):
    """Get backend details."""
    b = _get_backend_or_404(backend_id, _get_store())
    return RegisterBackendResponse(
        id=b.id,
        name=b.name,
        backend_type=b.backend_type,
        base_url=b.base_url,
        created_at=b.created_at,
    )


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------


@router.post("/{backend_id}/sync", response_model=SyncResponse)
async def sync_experiments(backend_id: str):
    """Sync experiments from backend API into project store (verbatim)."""
    store = _get_store()
    backend = _get_backend_or_404(backend_id, store)
    client = BackendClient(backend.base_url)

    try:
        count = await client.sync_experiments(store, backend_id)
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to sync from {backend.base_url}: {e}",
        )

    now = datetime.now(timezone.utc).isoformat()
    backend.last_synced_at = now
    store.backends.update(backend)

    return SyncResponse(
        backend_id=backend_id,
        experiments_synced=count,
        synced_at=now,
    )


@router.get("/{backend_id}/experiments")
async def list_experiments(backend_id: str):
    """List synced experiments (from local store, native format)."""
    store = _get_store()
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


@router.get("/{backend_id}/experiments/{experiment_id}")
async def get_experiment(backend_id: str, experiment_id: str):
    """Get a synced experiment in native backend format."""
    store = _get_store()
    _get_backend_or_404(backend_id, store)
    data = store.backends.load_sync(backend_id, f"experiments/{experiment_id}.json")
    if not data:
        raise HTTPException(
            status_code=404,
            detail=f"Experiment '{experiment_id}' not synced. Run POST /sync first.",
        )
    return data


# ---------------------------------------------------------------------------
# Pipeline discovery
# ---------------------------------------------------------------------------


@router.get("/{backend_id}/pipeline", response_model=PipelineViewResponse)
async def get_pipeline(backend_id: str):
    """Dynamic pipeline view from the backend."""
    store = _get_store()
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
