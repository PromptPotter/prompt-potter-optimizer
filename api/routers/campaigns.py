"""
Campaign registry endpoints.

Provides REST API for listing and viewing optimization campaigns.
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
