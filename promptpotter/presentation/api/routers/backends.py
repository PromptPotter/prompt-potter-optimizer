from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import Field

from promptpotter import connectors
from promptpotter.application.jobs.launcher.admission import probe_backend
from promptpotter.domain.strict_model import StrictModel
from promptpotter.presentation.api.deps import IdentityDep
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import NotFoundError

backends_router = APIRouter(prefix="/backends", tags=["Backends"])


class BackendResponse(StrictModel):
    id: str = Field(
        description="The connector's name, as `dataset.yaml::pipeline.backend_type` names it"
    )
    base_url: str = Field(
        description="Where the operator configured it; empty for an in-process one"
    )


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
def list_backends(_identity: IdentityDep) -> list[BackendResponse]:
    """Every registered connector, at the endpoint the operator configured for it."""
    return [
        BackendResponse(id=name, base_url=connector.base_url())
        for name, connector in sorted(connectors.registered().items())
    ]


@backends_router.get("/{backend_id}/health", response_model=BackendHealthResponse)
async def get_backend_health(backend_id: str, _identity: IdentityDep) -> BackendHealthResponse:
    """Whether a launch against this backend would be admitted, by the reading admission itself takes.

    A down backend is an answer, never an error; only an unregistered ``backend_id`` answers 404.
    """
    connector = connectors.registered().get(backend_id)
    if connector is None:
        raise NotFoundError(f"Backend '{backend_id}' not found")
    down = await probe_backend(connector.name)
    return BackendHealthResponse(
        backend_id=connector.name,
        base_url=connector.base_url(),
        status="live" if down is None else "unreachable",
        checked_at=utcnow_iso(),
        detail=None if down is None else str(down),
    )


__all__ = [
    "BackendHealthResponse",
    "BackendResponse",
    "backends_router",
]
