"""
Campaign registry endpoints.

Provides REST API for listing and viewing optimization campaigns,
nested under ``/backends/{backend_id}/campaigns``.
"""
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from promptpotter.dependencies import StoreDep

router = APIRouter()


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class CampaignSummary(BaseModel):
    campaign_id: str = Field(description="Unique campaign identifier")
    name: str = Field(description="Human-readable campaign name")
    status: str = Field(description="Campaign status: active, completed, or stopped")
    n_trials: int = Field(description="Total number of completed trial rounds")
    best_accuracy: float = Field(description="Highest accuracy achieved across all trials")
    baseline_accuracy: float = Field(description="Initial accuracy before optimization")
    created_at: str = Field(description="ISO 8601 creation timestamp")
    updated_at: str = Field(description="ISO 8601 last-update timestamp")


class CampaignListResponse(BaseModel):
    campaigns: list[CampaignSummary] = Field(description="List of campaign summaries")
    total: int = Field(description="Total number of campaigns")


class TrialSummary(BaseModel):
    trial_id: str = Field(description="Unique trial identifier")
    round: int = Field(description="Round number within the campaign")
    label: str = Field(description="Human-readable label (e.g. 'round_3')")
    prompt_fields_id: str = Field(description="OptSearchPoint ID for this trial's prompt")
    accuracy: float = Field(description="Accuracy achieved in this trial")
    hits: int = Field(description="Number of correct matches")
    total: int = Field(description="Total number of evaluated queries")
    improved: bool = Field(description="Whether this trial improved over the previous best")
    created_at: str = Field(description="ISO 8601 creation timestamp")


class CampaignDetailResponse(BaseModel):
    campaign_id: str = Field(description="Unique campaign identifier")
    name: str = Field(description="Human-readable campaign name")
    backend_id: str = Field(description="Backend this campaign optimizes against")
    status: str = Field(description="Campaign status: active, completed, or stopped")
    config: dict[str, Any] = Field(description="Full campaign configuration used for this run")
    n_trials: int = Field(description="Total number of completed trial rounds")
    best_accuracy: float = Field(description="Highest accuracy achieved across all trials")
    best_trial_id: str | None = Field(description="Trial ID of the best-performing round")
    baseline_accuracy: float = Field(description="Initial accuracy before optimization")
    created_at: str = Field(description="ISO 8601 creation timestamp")
    updated_at: str = Field(description="ISO 8601 last-update timestamp")
    trials: list[TrialSummary] = Field(description="Ordered list of trial summaries")
    langfuse_trace_id: str | None = Field(
        default=None, description="Langfuse trace ID if observability is enabled",
    )


# ---------------------------------------------------------------------------
# Endpoints — nested under /backends/{backend_id}/campaigns
# ---------------------------------------------------------------------------


@router.get(
    "/backends/{backend_id}/campaigns",
    response_model=CampaignListResponse,
)
async def list_campaigns(
    store: StoreDep,
    backend_id: str,
):
    """List all campaigns for a backend."""
    campaigns = store.campaigns.list_all(backend_id)
    return CampaignListResponse(
        campaigns=[CampaignSummary(**c) for c in campaigns],
        total=len(campaigns),
    )


@router.get(
    "/backends/{backend_id}/campaigns/{campaign_id}",
    response_model=CampaignDetailResponse,
)
async def get_campaign(
    store: StoreDep,
    backend_id: str,
    campaign_id: str,
):
    """Get campaign detail with trial summaries."""
    data = store.campaigns.load(backend_id, campaign_id)
    if data is None:
        raise HTTPException(404, f"Campaign not found: {campaign_id}")
    return CampaignDetailResponse(**data)


@router.get(
    "/backends/{backend_id}/campaigns/{campaign_id}/trials/{round_num}",
    response_model=dict[str, Any],
)
async def get_trial(
    store: StoreDep,
    backend_id: str,
    campaign_id: str,
    round_num: int,
):
    """Get full trial detail for a specific round."""
    trial = store.campaigns.load_trial(backend_id, campaign_id, round_num)
    if trial is None:
        raise HTTPException(404, f"Trial round {round_num} not found")
    return trial
