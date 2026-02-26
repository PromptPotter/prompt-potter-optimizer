"""
Campaign registry service.

Creates and manages optimization campaigns as structured trial sequences.
Each campaign tracks a series of optimization rounds with full PromptState
lineage, scores, and metadata compatible with Langfuse trace IDs and
MLflow run IDs.

Hierarchy::

    Campaign → Trial(round=0, baseline) → Trial(round=1) → ... → Trial(round=N)
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from api.services.project_store import ProjectStore

logger = logging.getLogger(__name__)


def generate_campaign_id() -> str:
    """Generate a unique campaign identifier."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    short = uuid.uuid4().hex[:8]
    return f"campaign_{ts}_{short}"


def generate_trial_id(round_num: int) -> str:
    """Generate a unique trial identifier for a round."""
    short = uuid.uuid4().hex[:8]
    return f"trial_{round_num:04d}_{short}"


def create_campaign(
    store: ProjectStore,
    backend_id: str,
    *,
    name: str = "",
    config: dict[str, Any] | None = None,
    campaign_id: str | None = None,
    langfuse_trace_id: str | None = None,
) -> dict[str, Any]:
    """Create a new campaign and persist to disk.

    Returns the campaign metadata dict.
    """
    cid = campaign_id or generate_campaign_id()
    metadata = {
        "name": name or cid,
        "backend_id": backend_id,
        "config": config or {},
        "langfuse_trace_id": langfuse_trace_id,
    }
    store.campaigns.create(backend_id, cid, metadata)
    logger.info("Created campaign %s for backend %s", cid, backend_id)
    return store.campaigns.load(backend_id, cid)


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
    """Record a single optimization trial in a campaign.

    Returns the trial detail dict.
    """
    trial_id = generate_trial_id(round_num)
    now = datetime.now(timezone.utc).isoformat()

    trial = {
        "trial_id": trial_id,
        "round": round_num,
        "label": label,
        "prompt_state": prompt_state,
        "prompt_state_id": prompt_state.get("id", ""),
        "parent_prompt_state_id": prompt_state.get("parent_id"),
        "accuracy": accuracy,
        "hits": hits,
        "total": total,
        "improved": improved,
        "candidates_evaluated": candidates_evaluated,
        "results": results or [],
        "langfuse_trace_id": langfuse_trace_id,
        "mlflow_run_id": mlflow_run_id,
        "created_at": now,
    }

    store.campaigns.add_trial(backend_id, campaign_id, trial)
    logger.info(
        "Recorded trial %s (round %d, acc=%.3f) in campaign %s",
        trial_id, round_num, accuracy, campaign_id,
    )
    return trial


def record_campaign_rounds(
    store: ProjectStore,
    backend_id: str,
    campaign_id: str,
    campaign_rounds: list[dict[str, Any]],
) -> list[str]:
    """Bulk-record a list of campaign round dicts as trials.

    This bridges the existing ``campaign_rounds`` list format
    (from ``run_optimization_loop`` / ``run_manual_round``) to the
    campaign registry.

    Returns list of trial IDs.
    """
    trial_ids = []
    for rd in campaign_rounds:
        ps = rd["prompt_state"]
        ps_dict = ps.model_dump() if hasattr(ps, "model_dump") else dict(ps)

        trial = record_trial(
            store, backend_id, campaign_id,
            round_num=rd.get("round", 0),
            prompt_state=ps_dict,
            accuracy=rd.get("accuracy", 0.0),
            hits=rd.get("hits", 0),
            total=rd.get("total", 0),
            results=rd.get("results"),
            label=rd.get("label", ""),
            improved=rd.get("improved", False),
            candidates_evaluated=rd.get("candidates_evaluated", 0),
        )
        trial_ids.append(trial["trial_id"])
    return trial_ids


def complete_campaign(
    store: ProjectStore,
    backend_id: str,
    campaign_id: str,
) -> None:
    """Mark a campaign as completed."""
    store.campaigns.update(backend_id, campaign_id, {"status": "completed"})
    logger.info("Campaign %s completed", campaign_id)


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
        # Load detail for parent_id
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
