"""Backend storage router — GET-only reads over registered backends.

Mutations (``register-backend``, ``sync-backend-experiments``) ride the
command highway: ``POST /commands/{kind}`` per
``docs/specs/m12-api-openapi.yaml``. The dispatcher writes a
``CommandRecord`` to the workspace ledger
(``projects/{tenant}/.workspace/events.jsonl``) and inline-applies
through ``CommandDispatcher._apply_register_backend`` /
``_apply_sync_backend_experiments``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from promptpotter import connectors
from promptpotter.config.settings import settings
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.infrastructure.backend import BackendClient
from promptpotter.presentation.api.deps import StoreDep, get_backend_or_404

logger = logging.getLogger(__name__)

backends_router = APIRouter(prefix="/backends", tags=["Backends"])


class BackendResponse(BaseModel):
    id: str = Field(description="Backend identifier")
    name: str = Field(description="Human-readable backend name")
    backend_type: str = Field(description="Backend type (e.g. 'default')")
    base_url: str = Field(description="Backend API base URL")
    created_at: str = Field(description="ISO 8601 creation timestamp")


class PipelineViewResponse(BaseModel):
    backend_id: str = Field(description="Backend identifier")
    backend_pipeline: dict[str, Any] = Field(description="Full PipelineSchema as dict")
    computed_nodes: list[dict[str, Any]] = Field(description="Computed PipelineNode dicts")
    fetched_at: str = Field(description="ISO 8601 fetch timestamp")
    source: str = Field(description="Pipeline source: 'live', 'cached', or 'default'")


@backends_router.get("", response_model=list[BackendResponse])
async def list_backends(store: StoreDep) -> list[BackendResponse]:
    """List all registered backends."""
    return [
        BackendResponse(
            id=b.id,
            name=b.name,
            backend_type=b.backend_type,
            base_url=b.base_url,
            created_at=b.created_at,
        )
        for b in store.backends.list_all()
    ]


@backends_router.get("/{backend_id}", response_model=BackendResponse)
async def get_backend(backend_id: str, store: StoreDep) -> BackendResponse:
    """Get backend details."""
    b = get_backend_or_404(backend_id, store)
    return BackendResponse(
        id=b.id,
        name=b.name,
        backend_type=b.backend_type,
        base_url=b.base_url,
        created_at=b.created_at,
    )


@backends_router.get("/{backend_id}/experiments")
async def list_experiments(backend_id: str, store: StoreDep) -> dict[str, Any]:
    """List synced experiments (from local store, native format)."""
    get_backend_or_404(backend_id, store)

    # First try the experiments list file
    data: dict[str, Any] | None = store.backends.load_sync(backend_id, "experiments.json")
    if data:
        return data

    # Fall back to individual files
    experiments = store.backends.list_synced_experiments(backend_id)
    if not experiments:
        raise HTTPException(
            status_code=404,
            detail="No synced experiments. Run POST /commands/sync-backend-experiments first.",
        )
    return {"experiments": experiments}


@backends_router.get("/{backend_id}/experiments/{experiment_id}")
async def get_experiment(backend_id: str, experiment_id: str, store: StoreDep) -> dict[str, Any]:
    """Get a synced experiment in native backend format."""
    get_backend_or_404(backend_id, store)
    data: dict[str, Any] | None = store.backends.load_sync(
        backend_id, f"experiments/{experiment_id}.json"
    )
    if not data:
        raise HTTPException(
            status_code=404,
            detail=f"Experiment '{experiment_id}' not synced. Run POST /commands/sync-backend-experiments first.",
        )
    return data


@backends_router.get("/{backend_id}/pipeline", response_model=PipelineViewResponse)
async def get_pipeline(backend_id: str, store: StoreDep) -> PipelineViewResponse:
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
    "BackendResponse",
    "PipelineViewResponse",
    "backends_router",
]
