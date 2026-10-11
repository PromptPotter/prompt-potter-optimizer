from __future__ import annotations

from fastapi import Request, Response

from promptpotter.application.maintenance.storage_report import (
    CampaignStorageResponse,
    DatasetStorageResponse,
    WorkspaceStorageResponse,
    campaign_storage,
    storage_by_dataset,
    workspace_storage,
)
from promptpotter.presentation.api.deps import StoresDep
from promptpotter.presentation.api.routers.campaigns._conditional import conditional_json
from promptpotter.presentation.api.routers.campaigns._router import campaigns_router


@campaigns_router.get("/campaigns/{campaign_id}/storage", response_model=CampaignStorageResponse)
def get_campaign_storage(request: Request, stores: StoresDep, campaign_id: str) -> Response:
    """On-disk size of the campaign tree, split into the six MECE leaves. 404 on cross-user."""
    return conditional_json(request, campaign_storage(stores, campaign_id))


@campaigns_router.get("/workspace/storage-by-dataset", response_model=DatasetStorageResponse)
def get_storage_by_dataset(request: Request, stores: StoresDep) -> Response:
    """On-disk size per dataset, every campaign of it pooled and split into the six MECE leaves.

    Archived campaigns are included; the shared measurement store is excluded.
    """
    return conditional_json(request, storage_by_dataset(stores))


@campaigns_router.get("/workspace/storage", response_model=WorkspaceStorageResponse)
def get_workspace_storage(request: Request, stores: StoresDep) -> Response:
    """On-disk size per campaign across the caller's workspace, fattest first, archived campaigns included.

    The shared caches and a residual ``other`` slice ride along, so the total is the tenant's real footprint.
    """
    return conditional_json(request, workspace_storage(stores))
