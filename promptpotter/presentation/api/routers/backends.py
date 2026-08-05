"""Backend storage router — GET-only reads over registered backends.

Mutations (``register-backend``) ride the command highway:
``POST /commands/{kind}`` per ``docs/specs/m12-api-openapi.yaml``. The dispatcher
writes a ``CommandRecord`` to the workspace ledger
(``projects/{tenant}/.workspace/events.jsonl``) and inline-applies through
``CommandDispatcher._apply_register_backend``.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import Field

from promptpotter import connectors
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.backend import build_backend_client
from promptpotter.presentation.api.deps import StoresDep, get_backend_or_404
from promptpotter.shared.clock import utcnow_iso

backends_router = APIRouter(prefix="/backends", tags=["Backends"])


class BackendResponse(StrictModel):
    id: str = Field(description="Backend identifier")
    name: str = Field(description="Human-readable backend name")
    backend_type: str = Field(description="Backend type (e.g. 'default')")
    base_url: str = Field(description="Backend API base URL")
    created_at: str = Field(description="ISO 8601 creation timestamp")


class BackendHealthResponse(StrictModel):
    backend_id: str = Field(description="Backend identifier")
    base_url: str = Field(description="Backend API base URL probed")
    status: str = Field(description="Reachability: 'live', 'unreachable', or 'error'")
    checked_at: str = Field(description="ISO 8601 probe timestamp")
    detail: str | None = Field(default=None, description="Error detail when not 'live'")


@backends_router.get("", response_model=list[BackendResponse])
def list_backends(stores: StoresDep) -> list[BackendResponse]:
    """List all registered backends."""
    return [
        BackendResponse(
            id=b.id,
            name=b.name,
            backend_type=b.backend_type,
            base_url=b.base_url,
            created_at=b.created_at,
        )
        for b in stores.backends.list_all()
    ]


@backends_router.get("/{backend_id}/health", response_model=BackendHealthResponse)
async def get_backend_health(backend_id: str, stores: StoresDep) -> BackendHealthResponse:
    """Probe the connector's own ``GET /status`` for live reachability.

    Thin wrapper over ``BackendClient.check_status()`` — the read half of the
    connector-state probe the webapp polls (slow cadence) to show the backend's
    true up/down on the connector node. ``check_status`` already maps a TCP-level
    failure to ``{"status": "unreachable"}``, so this never raises on a down
    backend; only a genuinely missing ``backend_id`` 404s (via ``get_backend_or_404``).
    """
    backend = get_backend_or_404(backend_id, stores)
    client = build_backend_client(connectors.get(backend.backend_type), backend.base_url)
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
    "backends_router",
]
