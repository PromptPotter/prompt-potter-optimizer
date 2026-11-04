from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter

from promptpotter.application.origin_listing import OriginListResponse
from promptpotter.application.read_params import OriginsParams
from promptpotter.domain.command_kinds import ReadKind
from promptpotter.presentation.api.deps import CallerDep, read_input

origins_router = APIRouter(prefix="/origins", tags=["Origins"])


@origins_router.get("", response_model=OriginListResponse, name=ReadKind.ORIGINS)
def get_origins(
    caller: CallerDep, read: Annotated[OriginsParams, read_input(OriginsParams)]
) -> OriginListResponse:
    return read.answer(caller)
