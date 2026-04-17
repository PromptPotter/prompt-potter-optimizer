"""Canonical factory for a fresh campaign session state.

Lives in the application layer so both CLI (``cmd_init``) and the
orchestrator's auto-mint branch (``run_optimization``) can build identical
initial state without either entry-point importing the other.
"""

from __future__ import annotations

from typing import Any

__all__ = ["new_session_state"]


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
