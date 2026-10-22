from __future__ import annotations

from typing import Annotated, Any

from fastapi import Query, Request, Response
from pydantic import Field

from promptpotter.application import campaign_listing
from promptpotter.application.campaign_listing import (
    CampaignDetailResponse,
    CampaignListResponse,
    owned_campaign,
)
from promptpotter.application.config_map import ConfigMapResponse, config_map
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
from promptpotter.application.pipeline_resolve import (
    CampaignPipelineResponse,
    resolve_campaign_config,
    resolve_pipeline_at,
)
from promptpotter.domain.campaign import LifecycleFilter
from promptpotter.domain.pipeline_overlay import (
    permitted_models_from_narrowing,
    steers_disallowed_model,
)
from promptpotter.domain.strict_model import StrictModel
from promptpotter.presentation.api.deps import StoresDep, decode_descend
from promptpotter.presentation.api.routers.campaigns._conditional import conditional_json
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
    return conditional_json(
        request,
        campaign_listing.list_campaigns(
            stores, inside=decode_descend(descend), dataset=dataset, lifecycle=lifecycle
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


@campaigns_router.get("/campaigns/{campaign_id}", response_model=CampaignDetailResponse)
def get_campaign(stores: StoresDep, campaign_id: str) -> CampaignDetailResponse:
    """Campaign manifest detail. 404 on cross-user reads."""
    return campaign_listing.campaign_detail(stores, campaign_id)


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
    return conditional_json(request, resolve_pipeline_at(stores, campaign_id, at))


class ForkPreviewRequest(StrictModel):
    pipeline_overlay: dict[str, Any] = Field(
        description="The `nodes.*.config` overlay the fork would carry, as its `CycleSeed` sends it"
    )


class ForkPreviewResponse(StrictModel):
    """What `POST /commands/fork-cycle` would decide about this steer, asked without forking."""

    steers_disallowed_model: bool = Field(
        description=(
            "The overlay picks a responder the campaign's declared `optimizer_narrowing` never "
            "sanctioned, or touches a cost lever, which no permitted set can sanction. True means "
            "the fork is the ADR-0005 babysit act: it needs `campaign.babysit` (404 without it) "
            "and stamps the branch grade C."
        )
    )
    permitted_models: dict[str, list[str]] = Field(
        description=(
            "What the verdict above was compared AGAINST, per node — this campaign's declared "
            "`optimizer_narrowing[node].param_allowed_values.model`. Served beside the verdict so "
            "a surface naming the permitted models cannot name a different set than the one that "
            "decided. A node absent from it sanctions nothing, which is why any model steer there "
            "counts."
        )
    )


@campaigns_router.post("/campaigns/{campaign_id}/fork-preview", response_model=ForkPreviewResponse)
def preview_fork_steer(
    stores: StoresDep, campaign_id: str, body: ForkPreviewRequest
) -> ForkPreviewResponse:
    """Whether this steer takes the babysit path: the fork gate's own verdict, asked without forking.

    A read despite the POST: nothing is written, and no capability is needed to ask.
    """
    campaign = owned_campaign(stores, campaign_id)
    narrowing = resolve_campaign_config(stores, campaign, None).optimizer_narrowing
    return ForkPreviewResponse(
        steers_disallowed_model=steers_disallowed_model(narrowing, body.pipeline_overlay),
        permitted_models=permitted_models_from_narrowing(narrowing),
    )


@campaigns_router.get("/campaigns/{campaign_id}/config-map", response_model=ConfigMapResponse)
def get_campaign_config_map(stores: StoresDep, campaign_id: str) -> ConfigMapResponse:
    """One campaign's knob coupling and provenance map: what moves which estimand, what overwrites what, which knobs collide.

    Read over the config its root runs under; 404 on cross-user reads.
    """
    campaign = owned_campaign(stores, campaign_id)
    return config_map(resolve_campaign_config(stores, campaign, campaign.root_hop))
