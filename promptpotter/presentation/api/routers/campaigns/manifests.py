from __future__ import annotations

from fastapi import Query, Request, Response
from pydantic import Field

from promptpotter.application.config_map import ConfigMapResponse, campaign_config_map
from promptpotter.application.datasets.draft_build import (
    DraftCampaignWire,
    read_draft_wire,
)
from promptpotter.application.datasets.draft_campaign import owned_draft
from promptpotter.application.datasets.origin_readiness import (
    OriginResolution,
    load_resolution,
)
from promptpotter.application.hierarchy import draft_starts
from promptpotter.application.pipeline_resolve import fork_preview, resolve_pipeline_at
from promptpotter.application.pipeline_responses import (
    CampaignPipelineResponse,
    ForkPreviewRequest,
    ForkPreviewResponse,
)
from promptpotter.domain.phases import LaunchStage
from promptpotter.domain.strict_model import StrictModel
from promptpotter.presentation.api.deps import StoresDep
from promptpotter.presentation.api.routers.campaigns._conditional import (
    hashed_json,
)
from promptpotter.presentation.api.routers.campaigns._router import campaigns_router


class DraftReopenResponse(StrictModel):
    """A re-opened check-in: its draft and what the last resolver turn left."""

    draft: DraftCampaignWire
    resolution: OriginResolution = Field(
        description="What the last resolver turn left, as `resolve-origin` answered it."
    )
    resolution_pending: bool = Field(
        description="Its task is on file but no resolver turn ever set it up "
        "(`DraftCampaign.resolution_pending`): the one case a reopen sends `resolve-origin`, so a "
        "revisit never re-buys a turn or re-proposes over authored prompts."
    )
    start: LaunchStage | None = Field(
        description="The stage of the Start that holds it: `queued` waits on a machine slot, "
        "`starting` is minting it. Set, a reopen offers no second Start; its listing's lifecycle "
        "tag says the same."
    )


@campaigns_router.get("/drafts/{draft_id}", response_model=DraftReopenResponse)
async def get_draft(stores: StoresDep, draft_id: str) -> DraftReopenResponse:
    """Re-open a check-in: its draft and its last resolver turn. 404 once started or discarded."""
    draft = owned_draft(stores, draft_id)
    return DraftReopenResponse(
        draft=await read_draft_wire(stores, draft),
        resolution=load_resolution(stores, draft),
        resolution_pending=draft.resolution_pending(),
        start=draft_starts(stores).get(draft_id),
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
