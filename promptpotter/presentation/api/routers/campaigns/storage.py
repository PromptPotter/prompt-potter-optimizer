from __future__ import annotations

from typing import Annotated

from promptpotter.application.maintenance.storage_report import (
    LeafBytes,
    WorkspaceStorageResponse,
)
from promptpotter.application.read_params import (
    CampaignStorageParams,
    WorkspaceStorageParams,
)
from promptpotter.domain.command_kinds import ReadKind
from promptpotter.presentation.api.deps import CallerDep, read_input
from promptpotter.presentation.api.routers.campaigns._router import campaigns_router


@campaigns_router.get(
    "/campaigns/{campaign_id}/storage", name=ReadKind.CAMPAIGN_STORAGE, response_model=LeafBytes
)
def get_campaign_storage(
    caller: CallerDep, read: Annotated[CampaignStorageParams, read_input(CampaignStorageParams)]
) -> LeafBytes:
    return read.answer(caller)


@campaigns_router.get(
    "/workspace/storage", name=ReadKind.WORKSPACE_STORAGE, response_model=WorkspaceStorageResponse
)
def get_workspace_storage(
    caller: CallerDep, read: Annotated[WorkspaceStorageParams, read_input(WorkspaceStorageParams)]
) -> WorkspaceStorageResponse:
    return read.answer(caller)
