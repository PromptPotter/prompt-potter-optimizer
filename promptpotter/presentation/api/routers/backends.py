from __future__ import annotations

from fastapi import APIRouter

from promptpotter.application.backend_listing import (
    BackendHealthResponse,
    BackendResponse,
    backend_health,
    list_backends,
)
from promptpotter.domain.command_kinds import ReadKind

backends_router = APIRouter(prefix="/backends", tags=["Backends"])


@backends_router.get("", response_model=list[BackendResponse], name=ReadKind.BACKENDS)
def get_backends() -> list[BackendResponse]:
    """Every registered connector, at the endpoint the operator configured for it."""
    return list_backends()


@backends_router.get(
    "/{backend_id}/health", response_model=BackendHealthResponse, name=ReadKind.BACKEND_HEALTH
)
async def get_backend_health(backend_id: str) -> BackendHealthResponse:
    """Whether a launch against this backend would be admitted, by the reading admission itself takes.

    A down backend is an answer, never an error; only an unregistered ``backend_id`` answers 404.
    """
    return await backend_health(backend_id)
