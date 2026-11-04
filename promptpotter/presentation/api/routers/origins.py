from __future__ import annotations

from fastapi import APIRouter

from promptpotter.application.origin_listing import OriginListResponse, origin_index
from promptpotter.domain.command_kinds import ReadKind
from promptpotter.presentation.api.deps import StoresDep

origins_router = APIRouter(prefix="/origins", tags=["Origins"])


@origins_router.get("", response_model=OriginListResponse, name=ReadKind.ORIGINS)
def get_origins(stores: StoresDep) -> OriginListResponse:
    """Every minted origin in the caller's tenant, newest first; a dataset no campaign ran is `GET /datasets`'.

    Tenant-scoped, not owner-filtered.
    """
    return origin_index(stores)
