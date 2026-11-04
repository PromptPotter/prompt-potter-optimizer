from __future__ import annotations

from promptpotter.application.maintenance.storage_report import (
    LeafBytes,
    WorkspaceStorageResponse,
    campaign_storage,
    workspace_storage,
)
from promptpotter.domain.command_kinds import ReadKind
from promptpotter.presentation.api.deps import StoresDep
from promptpotter.presentation.api.routers.campaigns._router import campaigns_router


@campaigns_router.get(
    "/campaigns/{campaign_id}/storage", name=ReadKind.CAMPAIGN_STORAGE, response_model=LeafBytes
)
def get_campaign_storage(stores: StoresDep, campaign_id: str) -> LeafBytes:
    """On-disk size of the campaign tree, split into its MECE leaves. 404 on cross-user."""
    return campaign_storage(stores, campaign_id)


@campaigns_router.get(
    "/workspace/storage", name=ReadKind.WORKSPACE_STORAGE, response_model=WorkspaceStorageResponse
)
def get_workspace_storage(stores: StoresDep) -> WorkspaceStorageResponse:
    """On-disk size per campaign across the caller's workspace, fattest first, archived campaigns included.

    The shared caches and a residual ``other`` slice ride along, so the total is the tenant's real footprint.
    """
    return workspace_storage(stores)
