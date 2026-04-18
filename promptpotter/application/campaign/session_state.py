"""Canonical factory for a fresh campaign session state.

Lives in the application layer so both CLI (``cmd_init``) and the
orchestrator's auto-mint branch (``run_optimization``) can build identical
initial state without either entry-point importing the other.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from promptpotter.application.campaign.campaign_setup import SessionEnv
    from promptpotter.application.campaign.config import CampaignConfig

logger = logging.getLogger(__name__)

__all__ = ["auto_mint_session", "new_session_state"]


def new_session_state(
    *,
    init_params: dict,
    campaign_config: dict,
    pipeline_params: dict,
    active_steps: list[str],
) -> dict[str, Any]:
    """Fresh session state — phase='init', empty baseline/results slots."""
    return {
        "phase": "init",
        "init_params": init_params,
        "campaign_config": campaign_config,
        "pipeline_params": pipeline_params,
        "active_steps": active_steps,
        "baseline_prompt_fields": {},
        "dataset_count": 0,
        "baseline_accuracy": 0.0,
        "task_context": None,
        "recon_variants": None,
        "cycle_id": None,
        "experiment_id": None,
    }


def auto_mint_session(
    session: SessionEnv,
    campaign_config: CampaignConfig,
    *,
    baseline_acc: float = 0.0,
    baseline_prompt_fields: dict | None = None,
    dataset_size: int = 0,
    experiment_id: str | None = None,
) -> str:
    """Mint a session when the caller has no session_id yet.

    Shared across the optimize path (``run_optimization`` when called
    outside CLI ``init``) and the scan path (``run_recon_and_persist``
    / notebook recon). Claims the active-session pointer so follow-up
    commands find the session without ``--session <id>``.

    Prefer passing scalars over a domain object — this function is a
    thin wrapper over ``new_session_state`` + ``SessionStore.create``,
    not a scored-baseline adapter.
    """
    state = new_session_state(
        init_params={
            "backend_url": session.backend_client.base_url,
            "backend_id": session.backend_id,
            "experiment_id": experiment_id,
            "dataset_name": session.dataset_name,
        },
        campaign_config=dict(campaign_config),
        pipeline_params={},
        active_steps=[],
    )
    state["baseline_accuracy"] = baseline_acc
    state["dataset_count"] = dataset_size
    state["baseline_prompt_fields"] = baseline_prompt_fields or {}

    session_id = session.store.sessions.create(session.backend_id, state)
    session.store.sessions.save_active_pointer(session.backend_id, session_id)
    logger.info("Auto-minted session: %s", session_id)
    return session_id
