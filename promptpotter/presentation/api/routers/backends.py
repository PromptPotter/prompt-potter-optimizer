"""Backend storage router — backends, sync, experiments, pipeline discovery."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from promptpotter import connectors
from promptpotter.config.settings import settings
from promptpotter.domain.backend import BackendConnection
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.infrastructure.backend import BackendClient
from promptpotter.presentation.api.deps import StoreDep, get_backend_or_404

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
    b = get_backend_or_404(backend_id, store)
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
    backend = get_backend_or_404(backend_id, store)
    connector = connectors.get(backend.backend_type)
    client = BackendClient(
        backend.base_url,
        wire_adapter=connector.wire_adapter,
        session=connector.session_factory(),
        auth_token=settings.TERMNORM_TOKEN or None,
    )

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
    get_backend_or_404(backend_id, store)

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
    get_backend_or_404(backend_id, store)
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
    backend = get_backend_or_404(backend_id, store)
    connector = connectors.get(backend.backend_type)
    client = BackendClient(
        backend.base_url,
        wire_adapter=connector.wire_adapter,
        session=connector.session_factory(),
        auth_token=settings.TERMNORM_TOKEN or None,
    )

    try:
        raw: dict[str, Any] | None = await client.fetch_pipeline()
        source = "live"
    except Exception:
        logger.warning("Backend unreachable at %s; returning empty schema", client.base_url)
        raw = None
        source = "default"

    schema = parse_pipeline_response(raw) if raw is not None else PipelineSchema()

    return PipelineViewResponse(
        backend_id=backend_id,
        backend_pipeline=schema.model_dump(),
        computed_nodes=[s.model_dump() for s in schema.nodes],
        fetched_at=datetime.now(UTC).isoformat(),
        source=source,
    )


__all__ = [
    "PipelineViewResponse",
    "RegisterBackendRequest",
    "RegisterBackendResponse",
    "SyncResponse",
    "backends_router",
]
