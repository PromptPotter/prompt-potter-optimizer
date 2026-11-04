from __future__ import annotations

from typing import Annotated

from fastapi import Request, Response

from promptpotter.application.config_map import ConfigMapResponse
from promptpotter.application.datasets.draft_build import DraftReopenResponse
from promptpotter.application.pipeline_responses import (
    CampaignPipelineResponse,
    ForkPreviewResponse,
)
from promptpotter.application.read_params import (
    CampaignPipelineParams,
    ConfigMapParams,
    DraftParams,
    ForkPreviewParams,
)
from promptpotter.domain.command_kinds import ReadKind
from promptpotter.presentation.api.deps import CallerDep, read_input
from promptpotter.presentation.api.routers.campaigns._conditional import (
    hashed_json,
)
from promptpotter.presentation.api.routers.campaigns._router import campaigns_router


@campaigns_router.get("/drafts/{draft_id}", name=ReadKind.DRAFT, response_model=DraftReopenResponse)
async def get_draft(
    caller: CallerDep, read: Annotated[DraftParams, read_input(DraftParams)]
) -> DraftReopenResponse:
    return await read.answer(caller)


@campaigns_router.get(
    "/campaigns/{campaign_id}/pipeline",
    name=ReadKind.CAMPAIGN_PIPELINE,
    response_model=CampaignPipelineResponse,
)
def get_campaign_pipeline(
    request: Request,
    caller: CallerDep,
    read: Annotated[CampaignPipelineParams, read_input(CampaignPipelineParams)],
) -> Response:
    return hashed_json(request, read.answer(caller))


@campaigns_router.post(
    "/campaigns/{campaign_id}/fork-preview",
    name=ReadKind.FORK_PREVIEW,
    response_model=ForkPreviewResponse,
)
def preview_fork_steer(
    caller: CallerDep, read: Annotated[ForkPreviewParams, read_input(ForkPreviewParams)]
) -> ForkPreviewResponse:
    return read.answer(caller)


@campaigns_router.get(
    "/campaigns/{campaign_id}/config-map",
    name=ReadKind.CONFIG_MAP,
    response_model=ConfigMapResponse,
)
def get_campaign_config_map(
    caller: CallerDep, read: Annotated[ConfigMapParams, read_input(ConfigMapParams)]
) -> ConfigMapResponse:
    return read.answer(caller)
