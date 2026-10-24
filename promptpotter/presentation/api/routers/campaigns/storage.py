from __future__ import annotations

from promptpotter.application.maintenance.storage_report import (
    CampaignStorageResponse,
    DatasetStorageResponse,
    WorkspaceStorageResponse,
    campaign_storage,
    storage_by_dataset,
    workspace_storage,
)
from promptpotter.presentation.api.deps import StoresDep
from promptpotter.presentation.api.routers.campaigns._router import campaigns_router


@campaigns_router.get("/campaigns/{campaign_id}/storage", response_model=CampaignStorageResponse)
def get_campaign_storage(stores: StoresDep, campaign_id: str) -> CampaignStorageResponse:
    """On-disk size of the campaign tree, split into the six MECE leaves. 404 on cross-user."""
    return campaign_storage(stores, campaign_id)


@campaigns_router.get("/workspace/storage-by-dataset", response_model=DatasetStorageResponse)
def get_storage_by_dataset(stores: StoresDep) -> DatasetStorageResponse:
    """On-disk size per dataset, every campaign of it pooled and split into the six MECE leaves.

    Archived campaigns are included; the shared measurement store is excluded.
    """
    return storage_by_dataset(stores)


@campaigns_router.get("/workspace/storage", response_model=WorkspaceStorageResponse)
def get_workspace_storage(stores: StoresDep) -> WorkspaceStorageResponse:
    """On-disk size per campaign across the caller's workspace, fattest first, archived campaigns included.

    The shared caches and a residual ``other`` slice ride along, so the total is the tenant's real footprint.
    """
    return workspace_storage(stores)
