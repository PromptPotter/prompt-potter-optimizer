from __future__ import annotations

from fastapi import APIRouter
from pydantic import Field

from promptpotter.application.datasets.draft_build import DraftCampaignWire, draft_wire
from promptpotter.application.datasets.ingest import draft_from_origin
from promptpotter.application.origin_listing import OriginEntry, list_origins
from promptpotter.domain.strict_model import StrictModel
from promptpotter.presentation.api.deps import StoresDep

origins_router = APIRouter(prefix="/origins", tags=["Origins"])


class OriginListResponse(StrictModel):
    origins: list[OriginEntry] = Field(description="Runnable origins, newest first")
    total: int = Field(description="Number of origins")


@origins_router.get("", response_model=OriginListResponse)
def get_origins(stores: StoresDep) -> OriginListResponse:
    """Every runnable origin in the caller's tenant: prepared datasets with no campaign yet, then campaign-backed ones, newest first.

    Tenant-scoped, not owner-filtered.
    """
    origins = list_origins(stores)
    return OriginListResponse(origins=origins, total=len(origins))


@origins_router.post("/{origin_id}/draft", response_model=DraftCampaignWire)
async def post_origin_draft(origin_id: str, stores: StoresDep) -> DraftCampaignWire:
    """Open a prior origin as a prefilled check-in campaign; nothing runs until ``/commands/start-checkin``."""
    draft = await draft_from_origin(stores=stores, origin_id=origin_id)
    return draft_wire(draft, stores.base_dir)
