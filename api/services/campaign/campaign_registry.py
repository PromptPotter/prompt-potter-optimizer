"""
Campaign registry — thin compatibility layer.

High-level campaign operations now live in ``CampaignStore``.  This module
re-exports them for existing callers and keeps ``get_campaign_lineage()``
which has genuine logic beyond what the store provides.
"""
import logging
from typing import Any

from api.services.project_store import ProjectStore

logger = logging.getLogger(__name__)


def create_campaign(
    store: ProjectStore,
    backend_id: str,
    *,
    name: str = "",
    config: dict[str, Any] | None = None,
    campaign_id: str | None = None,
    langfuse_trace_id: str | None = None,
) -> dict[str, Any]:
    """Create a new campaign and persist to disk.  Returns campaign metadata."""
    return store.campaigns.create_campaign(
        backend_id,
        name=name,
        config=config,
        campaign_id=campaign_id,
        langfuse_trace_id=langfuse_trace_id,
    )


def record_trial(
    store: ProjectStore,
    backend_id: str,
    campaign_id: str,
    *,
    round_num: int,
    prompt_state: dict[str, Any],
    accuracy: float,
    hits: int,
    total: int,
    results: list[dict[str, Any]] | None = None,
    label: str = "",
    improved: bool = False,
    candidates_evaluated: int = 0,
    langfuse_trace_id: str | None = None,
    mlflow_run_id: str | None = None,
) -> dict[str, Any]:
    """Record a single optimization trial.  Returns the trial detail dict."""
    return store.campaigns.record_trial(
        backend_id, campaign_id,
        round_num=round_num,
        prompt_state=prompt_state,
        accuracy=accuracy,
        hits=hits,
        total=total,
        results=results,
        label=label,
        improved=improved,
        candidates_evaluated=candidates_evaluated,
        langfuse_trace_id=langfuse_trace_id,
        mlflow_run_id=mlflow_run_id,
    )


def record_campaign_rounds(
    store: ProjectStore,
    backend_id: str,
    campaign_id: str,
    campaign_rounds: list[dict[str, Any]],
) -> list[str]:
    """Bulk-record campaign round dicts as trials.  Returns trial IDs."""
    return store.campaigns.record_campaign_rounds(
        backend_id, campaign_id, campaign_rounds,
    )


def complete_campaign(
    store: ProjectStore,
    backend_id: str,
    campaign_id: str,
) -> None:
    """Mark a campaign as completed."""
    store.campaigns.complete(backend_id, campaign_id)


def get_campaign_lineage(
    store: ProjectStore,
    backend_id: str,
    campaign_id: str,
) -> list[dict[str, Any]]:
    """Reconstruct the full PromptState lineage chain for a campaign.

    Returns a list of ``{trial_id, round, prompt_state_id, parent_id,
    accuracy, label}`` ordered by round.
    """
    campaign = store.campaigns.load(backend_id, campaign_id)
    if not campaign:
        return []

    lineage = []
    for entry in campaign.get("trials", []):
        trial = store.campaigns.load_trial(
            backend_id, campaign_id, entry["round"],
        )
        parent_id = trial.get("parent_prompt_state_id") if trial else None
        lineage.append({
            "trial_id": entry["trial_id"],
            "round": entry["round"],
            "prompt_state_id": entry.get("prompt_state_id", ""),
            "parent_prompt_state_id": parent_id,
            "accuracy": entry.get("accuracy", 0.0),
            "label": entry.get("label", ""),
        })
    return lineage
