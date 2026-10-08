"""The storage report's three reads — ``application/maintenance/storage_report.py`` owns the taxonomy and the pooling."""

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
    """Per-dataset on-disk leaf breakdown (the Files-view 'cake') — every campaign of a
    dataset pooled, then split into the six MECE leaves. Includes archived campaigns; the
    shared measurement store is excluded (it's not per-dataset-owned)."""
    return conditional_json(request, storage_by_dataset(stores))


@campaigns_router.get("/workspace/storage", response_model=WorkspaceStorageResponse)
def get_workspace_storage(request: Request, stores: StoresDep) -> Response:
    """Per-campaign on-disk slices across the caller's whole workspace, fattest first,
    plus the shared caches and a residual ``other`` slice so the grand total equals the
    tenant's real footprint — answers "where did the bucket sizes go?", nothing excluded.
    Includes archived campaigns — they stay in ``campaigns/``, flagged, not moved."""
    return conditional_json(request, workspace_storage(stores))
