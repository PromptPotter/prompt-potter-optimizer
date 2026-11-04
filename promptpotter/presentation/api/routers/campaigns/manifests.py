from __future__ import annotations

from fastapi import Query, Request, Response

from promptpotter.application.config_map import ConfigMapResponse, campaign_config_map
from promptpotter.application.datasets.draft_build import DraftReopenResponse, reopen_draft
from promptpotter.application.pipeline_resolve import fork_preview, resolve_pipeline_at
from promptpotter.application.pipeline_responses import (
    CampaignPipelineResponse,
    ForkPreviewRequest,
    ForkPreviewResponse,
)
from promptpotter.domain.command_kinds import ReadKind
from promptpotter.presentation.api.deps import StoresDep
from promptpotter.presentation.api.routers.campaigns._conditional import (
    hashed_json,
)
from promptpotter.presentation.api.routers.campaigns._router import campaigns_router


@campaigns_router.get("/drafts/{draft_id}", name=ReadKind.DRAFT, response_model=DraftReopenResponse)
async def get_draft(stores: StoresDep, draft_id: str) -> DraftReopenResponse:
    """Re-open a check-in: its draft and its last resolver turn. 404 once started or discarded."""
    return await reopen_draft(stores, draft_id)


@campaigns_router.get(
    "/campaigns/{campaign_id}/pipeline",
    name=ReadKind.CAMPAIGN_PIPELINE,
    response_model=CampaignPipelineResponse,
)
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


@campaigns_router.post(
    "/campaigns/{campaign_id}/fork-preview",
    name=ReadKind.FORK_PREVIEW,
    response_model=ForkPreviewResponse,
)
def preview_fork_steer(
    stores: StoresDep, campaign_id: str, body: ForkPreviewRequest
) -> ForkPreviewResponse:
    """Whether this steer takes the babysit path: the fork gate's own verdict, asked without forking.

    A read despite the POST: nothing is written, and no capability is needed to ask.
    """
    return fork_preview(stores, campaign_id, body.pipeline_overlay)


@campaigns_router.get(
    "/campaigns/{campaign_id}/config-map",
    name=ReadKind.CONFIG_MAP,
    response_model=ConfigMapResponse,
)
def get_campaign_config_map(stores: StoresDep, campaign_id: str) -> ConfigMapResponse:
    """One campaign's knob coupling and provenance map: what moves which estimand, what overwrites what, which knobs collide.

    Read over the config its root runs under; 404 on cross-user reads.
    """
    return campaign_config_map(stores, campaign_id)
