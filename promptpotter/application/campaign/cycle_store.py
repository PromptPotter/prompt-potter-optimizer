"""Cycle persistence boundary — resume/create cycles."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from promptpotter.application.campaign.campaign_setup import Session
    from promptpotter.application.campaign.config import CampaignConfig
    from promptpotter.domain.search_point import JobSearchPoint


logger = logging.getLogger(__name__)


# Hot-updateable on resume — these don't change WHAT the cycle solves, only HOW it searches.
HOT_UPDATEABLE_KEYS: frozenset[str] = frozenset(
    {
        "max_rounds",
        "l1_patience",
        "l2_patience",
        "l3_patience",
        "degradation_threshold",
        "model",
        "n_variants",
        "creativity",
        "improvement_threshold",
        "sp_budget_ttest",
        "seed",
    }
)

__all__ = [
    "HOT_UPDATEABLE_KEYS",
    "bootstrap_cycle",
]


def bootstrap_cycle(
    config: CampaignConfig,
    session: Session,
    baseline_jsp: JobSearchPoint,
    baseline_accuracy: float,
    dataset: list,
    cycle_id_override: str | None,
    *,
    parent_session_id: str = "",
    resume_from_round_override: int | None = None,
) -> tuple[str | None, int]:
    """Resume an existing cycle or create a new one via ``session.store.campaigns``.

    Returns ``(cycle_id, resumed_from_round)``. Hot-updateable config keys
    on the existing cycle are refreshed from the current snapshot when
    ``cycle_id_override`` is set.
    """
    from promptpotter.domain.cycle_identity import cycle_config_identity

    if not session.backend_id:
        return None, 0
    try:
        store = session.store.campaigns
        resolved = cycle_id_override or cycle_config_identity(baseline_jsp, dataset)
        if resume_from_round_override is not None:
            store.rewind_to_round(session.backend_id, resolved, resume_from_round_override)
        config_snapshot = config.model_dump(mode="json")
        existing = store.load(session.backend_id, resolved)
        if existing is not None:
            if cycle_id_override:
                stored_cfg = existing.get("config", {}) or {}
                cfg_updated = False
                for k in HOT_UPDATEABLE_KEYS:
                    if stored_cfg.get(k) != config_snapshot.get(k):
                        stored_cfg[k] = config_snapshot.get(k)
                        cfg_updated = True
                if cfg_updated and stored_cfg:
                    store.update(session.backend_id, resolved, {"config": stored_cfg})
                    logger.info("Updated loop-control config for %s", resolved)
            return resolved, len(existing.get("trials", []))
        store.create(
            session.backend_id,
            resolved,
            {
                "type": "optimization_loop",
                "config": config_snapshot,
                "baseline_accuracy": baseline_accuracy,
                "parent_session_id": parent_session_id,
            },
        )
        return resolved, 0
    except (OSError, json.JSONDecodeError, KeyError):
        logger.warning("Cycle resume setup failed — running fresh", exc_info=True)
        return None, 0
