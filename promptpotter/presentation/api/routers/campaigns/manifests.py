from __future__ import annotations

from typing import Annotated

from fastapi import Query, Request, Response
from pydantic import Field

from promptpotter.application import campaign_listing
from promptpotter.application.campaign_listing import CampaignListResponse
from promptpotter.application.config_map import ConfigMapResponse, campaign_config_map
from promptpotter.application.datasets.draft_build import (
    DraftCampaignWire,
    DraftListResponse,
    draft_list,
    draft_wire,
)
from promptpotter.application.datasets.draft_campaign import owned_draft
from promptpotter.application.datasets.origin_readiness import (
    OriginLastResolution,
    RaisedCommand,
    load_resolution,
)
from promptpotter.application.hierarchy import hierarchy_version
from promptpotter.application.pipeline_resolve import (
    CampaignPipelineResponse,
    ForkPreviewRequest,
    ForkPreviewResponse,
    fork_preview,
    resolve_pipeline_at,
)
from promptpotter.domain.campaign import LifecycleFilter
from promptpotter.domain.cycle_paths import decode_cycle_path
from promptpotter.domain.strict_model import StrictModel
from promptpotter.presentation.api.deps import StoresDep
from promptpotter.presentation.api.routers.campaigns._conditional import (
    conditional_json,
    hashed_json,
)
from promptpotter.presentation.api.routers.campaigns._router import campaigns_router


@campaigns_router.get("/campaigns", response_model=CampaignListResponse)
def list_campaigns(
    request: Request,
    stores: StoresDep,
    dataset: str | None = Query(default=None, description="Filter to one dataset"),
    lifecycle: Annotated[
        LifecycleFilter,
        Query(
            description="'active' (default), 'archived' or 'deleted' — the visibility intent; "
            "or 'all'",
        ),
    ] = "active",
    descend: str | None = Query(None),
) -> Response:
    """Every campaign in one store owned by the caller, newest first.

    ``descend`` enters an inner store exactly as on ``GET /cycles``.
    """
    inside = decode_cycle_path(descend)
    return conditional_json(
        request,
        (hierarchy_version(stores, inside=inside), dataset, lifecycle),
        lambda: campaign_listing.list_campaigns(
            stores, inside=inside, dataset=dataset, lifecycle=lifecycle
        ),
    )


class DraftReopenResponse(StrictModel):
    """A re-opened check-in: its draft and what the last resolver turn left."""

    draft: DraftCampaignWire
    resolution: OriginLastResolution | None = Field(
        description="The last resolver turn's output; null when none ran."
    )
    raised: list[RaisedCommand] = Field(
        description="Proposals the last turn left unclicked, so a re-opened check-in keeps the "
        "operator's outstanding actions."
    )


@campaigns_router.get("/drafts", response_model=DraftListResponse)
def list_drafts(stores: StoresDep) -> DraftListResponse:
    """The caller's open check-ins. A draft is no campaign: Start mints one and closes it."""
    return draft_list(stores)


@campaigns_router.get("/drafts/{draft_id}", response_model=DraftReopenResponse)
def get_draft(stores: StoresDep, draft_id: str) -> DraftReopenResponse:
    """Re-open a check-in: its draft and its last resolver turn. 404 once started or discarded."""
    draft = owned_draft(stores, draft_id)
    block = load_resolution(stores, draft)
    return DraftReopenResponse(
        draft=draft_wire(stores, draft),
        resolution=block.last_resolution,
        raised=block.raised,
    )


@campaigns_router.get("/campaigns/{campaign_id}/pipeline", response_model=CampaignPipelineResponse)
def get_campaign_pipeline(
    request: Request,
    stores: StoresDep,
    campaign_id: str,
    at: str = Query(
        default="",
        description="Searchpoint subject (`parse_subject` grammar); defaults to the campaign root",
    ),
) -> Response:
    """What this campaign runs, the one server-owned answer; ownership-gated, 404 on cross-tenant reads."""
    return hashed_json(request, resolve_pipeline_at(stores, campaign_id, at))


@campaigns_router.post("/campaigns/{campaign_id}/fork-preview", response_model=ForkPreviewResponse)
def preview_fork_steer(
    stores: StoresDep, campaign_id: str, body: ForkPreviewRequest
) -> ForkPreviewResponse:
    """Whether this steer takes the babysit path: the fork gate's own verdict, asked without forking.

    A read despite the POST: nothing is written, and no capability is needed to ask.
    """
    return fork_preview(stores, campaign_id, body.pipeline_overlay)


@campaigns_router.get("/campaigns/{campaign_id}/config-map", response_model=ConfigMapResponse)
def get_campaign_config_map(stores: StoresDep, campaign_id: str) -> ConfigMapResponse:
    """One campaign's knob coupling and provenance map: what moves which estimand, what overwrites what, which knobs collide.

    Read over the config its root runs under; 404 on cross-user reads.
    """
    return campaign_config_map(stores, campaign_id)
