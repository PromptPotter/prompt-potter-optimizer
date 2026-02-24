"""
Campaign registry endpoints.

Provides REST API for listing, viewing, and exporting optimization campaigns.
"""
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from api.services.project_store import ProjectStore

router = APIRouter()


def _get_store() -> ProjectStore:
    return ProjectStore()


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class CampaignSummary(BaseModel):
    campaign_id: str
    name: str
    status: str
    n_trials: int
    best_accuracy: float
    baseline_accuracy: float
    created_at: str
    updated_at: str


class CampaignListResponse(BaseModel):
    campaigns: list[CampaignSummary]
    total: int


class TrialSummary(BaseModel):
    trial_id: str
    round: int
    label: str
    prompt_state_id: str
    accuracy: float
    hits: int
    total: int
    improved: bool
    created_at: str


class CampaignDetailResponse(BaseModel):
    campaign_id: str
    name: str
    backend_id: str
    status: str
    config: dict[str, Any]
    n_trials: int
    best_accuracy: float
    best_trial_id: str | None
    baseline_accuracy: float
    created_at: str
    updated_at: str
    trials: list[TrialSummary]
    langfuse_trace_id: str | None = None


class LineageEntry(BaseModel):
    trial_id: str
    round: int
    prompt_state_id: str
    parent_prompt_state_id: str | None
    accuracy: float
    label: str


class CampaignLineageResponse(BaseModel):
    campaign_id: str
    lineage: list[LineageEntry]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/campaigns", response_model=CampaignListResponse)
async def list_campaigns(
    backend_id: str = Query(..., description="Backend identifier"),
):
    """List all campaigns for a backend."""
    store = _get_store()
    campaigns = store.campaigns.list_all(backend_id)
    return CampaignListResponse(
        campaigns=[CampaignSummary(**c) for c in campaigns],
        total=len(campaigns),
    )


@router.get("/campaigns/{campaign_id}", response_model=CampaignDetailResponse)
async def get_campaign(
    campaign_id: str,
    backend_id: str = Query(..., description="Backend identifier"),
):
    """Get campaign detail with trial summaries."""
    store = _get_store()
    data = store.campaigns.load(backend_id, campaign_id)
    if data is None:
        raise HTTPException(404, f"Campaign not found: {campaign_id}")
    return CampaignDetailResponse(**data)


@router.get(
    "/campaigns/{campaign_id}/trials/{round_num}",
    response_model=dict[str, Any],
)
async def get_trial(
    campaign_id: str,
    round_num: int,
    backend_id: str = Query(..., description="Backend identifier"),
):
    """Get full trial detail for a specific round."""
    store = _get_store()
    trial = store.campaigns.load_trial(backend_id, campaign_id, round_num)
    if trial is None:
        raise HTTPException(404, f"Trial round {round_num} not found")
    return trial


@router.get(
    "/campaigns/{campaign_id}/export",
    response_model=dict[str, Any],
)
async def export_campaign(
    campaign_id: str,
    backend_id: str = Query(..., description="Backend identifier"),
):
    """Full campaign export: metadata + all trial details."""
    store = _get_store()
    data = store.campaigns.export(backend_id, campaign_id)
    if data is None:
        raise HTTPException(404, f"Campaign not found: {campaign_id}")
    return data


@router.get(
    "/campaigns/{campaign_id}/lineage",
    response_model=CampaignLineageResponse,
)
async def get_campaign_lineage(
    campaign_id: str,
    backend_id: str = Query(..., description="Backend identifier"),
):
    """Reconstruct the PromptState lineage chain for a campaign."""
    from api.services.campaign_registry import get_campaign_lineage

    store = _get_store()
    campaign = store.campaigns.load(backend_id, campaign_id)
    if campaign is None:
        raise HTTPException(404, f"Campaign not found: {campaign_id}")

    lineage = get_campaign_lineage(store, backend_id, campaign_id)
    return CampaignLineageResponse(
        campaign_id=campaign_id,
        lineage=[LineageEntry(**e) for e in lineage],
    )


@router.delete("/campaigns/{campaign_id}")
async def delete_campaign(
    campaign_id: str,
    backend_id: str = Query(..., description="Backend identifier"),
):
    """Delete a campaign and its trials."""
    store = _get_store()
    deleted = store.campaigns.delete(backend_id, campaign_id)
    if not deleted:
        raise HTTPException(404, f"Campaign not found: {campaign_id}")
    return {"deleted": True, "campaign_id": campaign_id}
