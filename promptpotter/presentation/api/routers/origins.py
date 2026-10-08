"""An *origin* is a content identity, distinct from a campaign (a run of one) and a dataset (raw material). Derived,
not stored — an origin drops off the list when the last campaign using it is archived."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import Field

from promptpotter.application.datasets.ingest import draft_from_origin
from promptpotter.application.jobs.launcher.draft_build import draft_wire
from promptpotter.application.origin import OriginEntry, list_origins
from promptpotter.domain.strict_model import StrictModel
from promptpotter.presentation.api.deps import StoresDep

origins_router = APIRouter(prefix="/origins", tags=["Origins"])


class OriginListResponse(StrictModel):
    origins: list[OriginEntry] = Field(description="Runnable origins, newest first")
    total: int = Field(description="Number of origins")


@origins_router.get("", response_model=OriginListResponse)
def get_origins(stores: StoresDep) -> OriginListResponse:
    """Every runnable origin in the caller's tenant — prepared first (ready datasets with no
    campaign yet), then campaign-backed, newest first. Tenant-scoped, not owner-filtered."""
    origins = list_origins(stores)
    return OriginListResponse(origins=origins, total=len(origins))


@origins_router.post("/{origin_id}/draft")
async def post_origin_draft(origin_id: str, stores: StoresDep) -> dict[str, Any]:
    """Open a chosen prior origin as a prefilled check-in campaign — the picker's "Reuse an
    origin" path for a campaign-backed origin. Nothing runs until the operator starts the
    check-in (``/commands/start-checkin``)."""
    draft = await draft_from_origin(stores=stores, origin_id=origin_id)
    return draft_wire(draft, stores.base_dir)
