from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import Field

from promptpotter.application.jobs.launcher.admission import probe_backend
from promptpotter.domain.strict_model import StrictModel
from promptpotter.presentation.api.deps import StoresDep, get_backend_or_404
from promptpotter.shared.clock import utcnow_iso

backends_router = APIRouter(prefix="/backends", tags=["Backends"])


class BackendResponse(StrictModel):
    id: str = Field(description="Backend identifier")
    name: str = Field(description="Human-readable backend name")
    backend_type: str = Field(description="Backend type (e.g. 'default')")
    base_url: str = Field(description="Backend API base URL")
    created_at: str = Field(description="ISO 8601 creation timestamp")


BackendReachability = Literal["live", "unreachable", "error"]


class BackendHealthResponse(StrictModel):
    backend_id: str = Field(description="Backend identifier")
    base_url: str = Field(description="Backend API base URL probed")
    status: BackendReachability = Field(
        description="Reachability: 'live', 'unreachable', or 'error'"
    )
    checked_at: str = Field(description="ISO 8601 probe timestamp")
    detail: str | None = Field(
        default=None, description="The refusal a launch would get when not 'live'"
    )


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
    """Whether a launch against this backend would be admitted, by the reading admission itself takes.

    A down backend is an answer, never an error; only a missing ``backend_id`` answers 404.
    """
    backend = get_backend_or_404(backend_id, stores)
    down = await probe_backend(backend.backend_type, backend.base_url)
    return BackendHealthResponse(
        backend_id=backend_id,
        base_url=backend.base_url,
        status="live" if down is None else "unreachable",
        checked_at=utcnow_iso(),
        detail=None if down is None else str(down),
    )


__all__ = [
    "BackendHealthResponse",
    "BackendResponse",
    "backends_router",
]
