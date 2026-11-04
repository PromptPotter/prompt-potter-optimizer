from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter

from promptpotter.application.backend_listing import BackendHealthResponse, BackendResponse
from promptpotter.application.read_params import BackendHealthParams, BackendsParams
from promptpotter.domain.command_kinds import ReadKind
from promptpotter.presentation.api.deps import CallerDep, read_input

backends_router = APIRouter(prefix="/backends", tags=["Backends"])


@backends_router.get("", response_model=list[BackendResponse], name=ReadKind.BACKENDS)
def get_backends(
    caller: CallerDep, read: Annotated[BackendsParams, read_input(BackendsParams)]
) -> list[BackendResponse]:
    return read.answer(caller)


@backends_router.get(
    "/{backend_id}/health", response_model=BackendHealthResponse, name=ReadKind.BACKEND_HEALTH
)
async def get_backend_health(
    caller: CallerDep, read: Annotated[BackendHealthParams, read_input(BackendHealthParams)]
) -> BackendHealthResponse:
    return await read.answer(caller)
