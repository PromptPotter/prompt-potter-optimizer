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
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from promptpotter import connectors
from promptpotter.config.settings import settings
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.infrastructure.backend import BackendClient
from promptpotter.presentation.api.deps import StoreDep, get_backend_or_404
from promptpotter.shared.clock import utcnow_iso

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


class BackendHealthResponse(BaseModel):
    backend_id: str = Field(description="Backend identifier")
    base_url: str = Field(description="Backend API base URL probed")
    status: str = Field(description="Reachability: 'live', 'unreachable', or 'error'")
    checked_at: str = Field(description="ISO 8601 probe timestamp")
    detail: str | None = Field(default=None, description="Error detail when not 'live'")


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
        fetched_at=utcnow_iso(),
        source=source,
    )


@backends_router.get("/{backend_id}/health", response_model=BackendHealthResponse)
async def get_backend_health(backend_id: str, store: StoreDep) -> BackendHealthResponse:
    """Probe the connector's own ``GET /status`` for live reachability.

    Thin wrapper over ``BackendClient.check_status()`` — the read half of the
    connector-state probe the webapp polls (slow cadence) to show the backend's
    true up/down on the connector node. ``check_status`` already maps a TCP-level
    failure to ``{"status": "unreachable"}``, so this never raises on a down
    backend; only a genuinely missing ``backend_id`` 404s (via ``get_backend_or_404``).
    """
    backend = get_backend_or_404(backend_id, store)
    connector = connectors.get(backend.backend_type)
    client = BackendClient(
        backend.base_url,
        wire_adapter=connector.wire_adapter,
        session=connector.session_factory(),
        auth_token=settings.TERMNORM_TOKEN or None,
    )
    try:
        probe = await client.check_status()
    finally:
        await client.aclose()
    raw = probe.get("status")
    # Reachable: any successful /status response that isn't our own failure
    # sentinel. `check_status` returns the backend's status dict on success
    # (its `status` may be absent or backend-specific) and {status:unreachable|error}
    # on failure — only those two sentinels are non-live.
    status = raw if raw in ("unreachable", "error") else "live"
    detail = probe.get("error") if status != "live" else None
    return BackendHealthResponse(
        backend_id=backend_id,
        base_url=backend.base_url,
        status=status,
        checked_at=utcnow_iso(),
        detail=str(detail) if detail is not None else None,
    )


__all__ = [
    "BackendHealthResponse",
    "BackendResponse",
    "PipelineViewResponse",
    "backends_router",
]
