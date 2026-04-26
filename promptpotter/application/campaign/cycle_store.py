"""Cycle persistence boundary — resume/create cycles, resolve campaign IDs."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from promptpotter.application.campaign.campaign_setup import Session
    from promptpotter.application.campaign.config import CampaignConfig
    from promptpotter.infrastructure.store import Stores
    from promptpotter.infrastructure.store.campaign_store import CampaignStore


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
    "resolve_campaign_id",
    "resume_or_create",
]


def resolve_campaign_id(
    store: Stores,
    backend_id: str,
    short_id: str,
) -> str | None:
    """Resolve short prefix/suffix to full campaign_id."""
    campaigns = store.campaigns.list_all(backend_id)
    matches = [c for c in campaigns if short_id in c["campaign_id"]]
    if len(matches) == 1:
        return matches[0]["campaign_id"]
    if len(matches) > 1:
        logger.warning(
            "Ambiguous ID '%s' — %d matches: %s",
            short_id,
            len(matches),
            [m["campaign_id"] for m in matches],
        )
        return None
    logger.warning("No campaign matching '%s'", short_id)
    return None


def resume_or_create(
    store: CampaignStore,
    backend_id: str,
    cycle_id: str,
    *,
    config_snapshot: dict[str, Any],
    baseline_accuracy: float,
    hot_update_keys: frozenset[str] = frozenset(),
    parent_session_id: str = "",
) -> int:
    """Resume an existing cycle or create a new one. Returns ``resumed_from_round``."""
    existing = store.load(backend_id, cycle_id)
    if existing is not None:
        if hot_update_keys:
            stored_cfg = existing.get("config", {})
            if stored_cfg:
                cfg_updated = False
                for k in hot_update_keys:
                    if stored_cfg.get(k) != config_snapshot.get(k):
                        stored_cfg[k] = config_snapshot.get(k)
                        cfg_updated = True
                if cfg_updated:
                    store.update(backend_id, cycle_id, {"config": stored_cfg})
                    logger.info("Updated loop-control config for %s", cycle_id)
        resumed_from_round = len(existing.get("trials", []))
        if resumed_from_round:
            logger.debug(
                "Resuming cycle %s — %d prior round(s) on disk",
                cycle_id,
                resumed_from_round,
            )
        return resumed_from_round

    store.create(
        backend_id,
        cycle_id,
        {
            "type": "optimization_loop",
            "config": config_snapshot,
            "baseline_accuracy": baseline_accuracy,
            "parent_session_id": parent_session_id,
        },
    )
    return 0


def bootstrap_cycle(
    config: CampaignConfig,
    session: Session,
    baseline_render: str,
    baseline_accuracy: float,
    dataset: list,
    active_steps: list[str],
    cycle_id_override: str | None,
    *,
    parent_session_id: str = "",
    resume_from_round_override: int | None = None,
) -> tuple[str | None, int]:
    """Resume or create the cycle via ``session.store.campaigns``. Returns ``(cycle_id, resumed_from_round)``."""
    from promptpotter.domain.cycle_identity import cycle_config_identity

    if not session.backend_id:
        return None, 0
    try:
        store = session.store.campaigns
        resolved = cycle_id_override or cycle_config_identity(
            config,
            baseline_render,
            dataset,
            active_steps,
        )
        if resume_from_round_override is not None:
            store.rewind_to_round(
                session.backend_id,
                resolved,
                resume_from_round_override,
            )
        resumed_from = resume_or_create(
            store,
            session.backend_id,
            resolved,
            config_snapshot=config.model_dump(mode="json"),
            baseline_accuracy=baseline_accuracy,
            hot_update_keys=HOT_UPDATEABLE_KEYS if cycle_id_override else frozenset(),
            parent_session_id=parent_session_id,
        )
        return resolved, resumed_from
    except (OSError, json.JSONDecodeError, KeyError):
        logger.warning("Cycle resume setup failed — running fresh", exc_info=True)
        return None, 0
