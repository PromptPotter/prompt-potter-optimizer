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
        description="The connector's name, as `dataset.yaml::pipeline.connector` names it"
    )
    execution: ConnectorExecution = Field(
        description="Its wire kind: only a `remote_http` one has a connection to secure"
    )
    configured: bool = Field(
        description="False where a `remote_http` one names no address, so every call refuses"
    )
    endpoints: dict[str, str] = Field(
        description="Each address the operator configured, by the name a dataset's "
        "`pipeline.endpoint` gives; empty where none is"
    )
    auth_header: str | None = Field(
        description="The header the operator's credential rides in on every request; null where "
        "operator config holds none"
    )


class BackendHealthResponse(StrictModel):
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
            configured=connector.endpoint is None or bool(connector.endpoints()),
            endpoints=connector.endpoints(),
            auth_header=next(iter(connector.credential_header()), None),
        )
        for name, connector in sorted(connectors.CONNECTORS.table.items())
    ]


async def backend_health(backend_id: str) -> BackendHealthResponse:
    """A down backend is an answer, never an error; only an unregistered *backend_id* is a 404.
    Each endpoint is probed, and the first refusal is the answer."""
    connector = connectors.CONNECTORS.table.get(backend_id)
    if connector is None:
        raise NotFoundError(f"Backend '{backend_id}' not found")
    downs = [await probe_backend(connector.name, e) for e in connector.probed_endpoints()]
    down = next((d for d in downs if d is not None), None)
    return BackendHealthResponse(
        status=backend_reachability(down),
        checked_at=utcnow_iso(),
        detail=None if down is None else str(down),
    )
