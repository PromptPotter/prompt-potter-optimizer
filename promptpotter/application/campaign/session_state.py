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
    cycle_hash: str,
    baseline_acc: float = 0.0,
    baseline_prompt_fields: dict | None = None,
    dataset_size: int = 0,
    experiment_id: str | None = None,
) -> str:
    """Mint a session when the caller has no session_id yet.

    Used by the optimize path (``run_optimization`` when called
    outside CLI ``init``). Claims the active-session pointer so follow-up
    commands find the session without ``--session <id>``.

    ``cycle_hash`` is the 12-hex content-addressed suffix (no ``cycle_``
    prefix) — callers compute it via ``cycle_hash_suffix`` so the minted
    session dir ends in the same hash as the ``cycle_<hash>`` dir that
    ``bootstrap_cycle`` produces for the same problem.
    """
    state = new_session_state(
        init_params={
            "backend_url": session.backend_client.base_url,
            "backend_id": session.backend_id,
            "experiment_id": experiment_id,
            "dataset_name": session.dataset_name,
        },
        campaign_config=campaign_config.model_dump(),
        pipeline_params={},
        active_steps=[],
    )
    state["baseline_accuracy"] = baseline_acc
    state["dataset_count"] = dataset_size
    state["baseline_prompt_fields"] = baseline_prompt_fields or {}

    cycle_id = session.store.campaigns.create_session(session.backend_id, state, cycle_hash)
    session.store.campaigns.save_active_pointer(session.store.tenant_id, cycle_id)
    logger.info("Auto-minted session: %s", cycle_id)
    return cycle_id
