from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request, Response

from promptpotter.application.hierarchy import hierarchy_version, list_hierarchy
from promptpotter.domain.campaign import LifecycleFilter
from promptpotter.domain.cycle_paths import decode_cycle_path
from promptpotter.domain.hierarchy import HierarchyResponse
from promptpotter.presentation.api.deps import StoresDep
from promptpotter.presentation.api.routers.campaigns._conditional import conditional_json

hierarchy_router = APIRouter()


@hierarchy_router.get("/hierarchy", response_model=HierarchyResponse, tags=["Hierarchy"])
def get_hierarchy(
    request: Request,
    stores: StoresDep,
    descend: str | None = Query(None),
    dataset: str | None = Query(default=None, description="Filter to one dataset"),
    lifecycle: Annotated[
        LifecycleFilter,
        Query(
            description="'active' (default), 'archived' or 'deleted' — the visibility intent; "
            "or 'all'",
        ),
    ] = "active",
    campaign: str | None = Query(default=None, description="Filter to one campaign id"),
    attached: bool = Query(False, description="Keep only the courses a producer holds"),
    depth: int = Query(1, ge=0, description="0 serves campaigns alone, 1 their courses"),
) -> Response:
    """Every campaign in one store the caller owns, as hierarchy entries, each with its courses.

    ``descend`` enters an inner store exactly as every per-cycle read does.
    """
    inside = decode_cycle_path(descend)
    return conditional_json(
        request,
        (hierarchy_version(stores, inside=inside), dataset, lifecycle, campaign, attached, depth),
        lambda: list_hierarchy(
            stores,
            inside=inside,
            dataset=dataset,
            lifecycle=lifecycle,
            campaign_id=campaign,
            attached_only=attached,
            depth=depth,
        ),
    )
