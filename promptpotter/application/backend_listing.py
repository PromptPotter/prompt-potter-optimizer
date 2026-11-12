from __future__ import annotations

from pydantic import Field

from promptpotter import connectors
from promptpotter.application.jobs.launcher.admission import probe_backend
from promptpotter.domain.connector import (
    BackendReachability,
    ConnectorExecution,
    backend_reachability,
)
from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import NotFoundError


class BackendResponse(StrictModel):
    id: str = Field(
        description="The connector's name, as `dataset.yaml::pipeline.backend_type` names it"
    )
    execution: ConnectorExecution = Field(
        description="Its wire kind: only a `remote_http` one has a connection to secure"
    )
    configured: bool = Field(
        description="False where a `remote_http` one names no address, so every call refuses"
    )
    base_url: str = Field(description="Where the operator configured it; empty where none is")
    auth_header: str | None = Field(
        description="The header the operator's credential rides in on every request; null where "
        "operator config holds none"
    )


class BackendHealthResponse(StrictModel):
    base_url: str = Field(description="Backend API base URL probed")
    status: BackendReachability = Field(
        description="Reachability, worded by `BACKEND_REACHABILITY_INFO`"
    )
    checked_at: str = Field(description="ISO 8601 probe timestamp")
    detail: str | None = Field(
        default=None, description="The refusal a launch would get when not 'live'"
    )


def list_backends() -> list[BackendResponse]:
    return [
        BackendResponse(
            id=name,
            execution=connector.execution,
            configured=connector.unconfigured() is None,
            base_url=connector.base_url(),
            auth_header=next(iter(connector.credential_header()), None),
        )
        for name, connector in sorted(connectors.CONNECTORS.table.items())
    ]


async def backend_health(backend_id: str) -> BackendHealthResponse:
    """A down backend is an answer, never an error; only an unregistered *backend_id* is a 404."""
    connector = connectors.CONNECTORS.table.get(backend_id)
    if connector is None:
        raise NotFoundError(f"Backend '{backend_id}' not found")
    down = await probe_backend(connector.name)
    return BackendHealthResponse(
        base_url=connector.base_url(),
        status=backend_reachability(down),
        checked_at=utcnow_iso(),
        detail=None if down is None else str(down),
    )
