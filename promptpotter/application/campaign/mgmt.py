"""Campaign management — post-init operations on campaigns.

Functions for saving winners, diffing configs, applying stored
overrides, and resolving campaign IDs for display.  Separated from
``campaign_setup.py`` (bootstrap) because these operate on existing
campaigns rather than creating them.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.campaign.config import CampaignConfig
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.infrastructure.store.project_store import ProjectStore

logger = logging.getLogger(__name__)

__all__ = [
    "apply_stored_overrides",
    "diff_campaign_config",
    "load_stored_campaign_config",
    "resolve_active_campaign_id",
    "save_campaign_winner",
]


def apply_stored_overrides(
    campaign_config: CampaignConfig,
    stored_cfg: dict,
) -> dict | None:
    """Merge stored experiment config into campaign_config (in-place).

    Returns updated pipeline_params if stored, else None.
    """
    _OVERRIDE_KEYS: dict[str, tuple[str, ...]] = {
        "l1_patience": ("optimization",),
        "max_rounds": ("optimization",),
        "n_variants": ("optimization",),
        "creativity": ("optimization",),
        "model": ("optimizer_llm",),
        "sp_budget_ttest": (),
    }
    for key, path in _OVERRIDE_KEYS.items():
        val = stored_cfg.get(key)
        if val is not None:
            target: dict[str, Any] = cast(dict[str, Any], campaign_config)
            for p in path:
                target = target.setdefault(p, {})
            target[key] = val

    stored_pp = stored_cfg.get("pipeline_params")
    if stored_pp:
        campaign_config["pipeline_params"] = stored_pp
        return stored_pp
    return None


def save_campaign_winner(
    campaign_rounds: list,
    campaign_config: CampaignConfig,
    store: ProjectStore,
    backend_id: str,
    *,
    campaign_id: str | None = None,
) -> dict:
    """Find best round, save to store + link to campaign. Returns save_data dict."""
    from datetime import UTC, datetime

    from promptpotter.application.campaign.campaign_setup import resolve_campaign_id

    winner = campaign_rounds[-1]["prompt_fields"]
    winner_acc = campaign_rounds[-1]["accuracy"]

    for rd in campaign_rounds:
        if rd["accuracy"] > winner_acc:
            winner = rd["prompt_fields"]
            winner_acc = rd["accuracy"]

    baseline_acc = campaign_rounds[0]["accuracy"] if campaign_rounds else None
    save_data = {
        "winner": winner.model_dump(),
        "accuracy": winner_acc,
        "campaign_rounds": len(campaign_rounds),
        "baseline_accuracy": baseline_acc,
        "improvement": (winner_acc - baseline_acc) if baseline_acc is not None else None,
        "config": campaign_config,
        "saved_at": datetime.now(UTC).isoformat(),
    }

    filename = f"optimization/campaign_winner_{winner.id[:12]}.json"
    store.backends.save_sync(backend_id, filename, save_data)

    if campaign_id:
        full_id = resolve_campaign_id(store, backend_id, campaign_id)
        if full_id:
            with graceful("Campaign metadata update skipped", level=logging.DEBUG):
                store.campaigns.update(
                    backend_id,
                    full_id,
                    {
                        "winner_prompt_fields_id": winner.id,
                        "winner_accuracy": winner_acc,
                        "winner_filename": filename,
                    },
                )

    logger.info("Winner saved: %s (acc=%.1f%%)", filename, winner_acc * 100)
    return {
        **save_data,
        "winner_id": winner.id,
        "filename": filename,
        "backend_id": backend_id,
    }


def diff_campaign_config(
    stored_config: dict,
    campaign_config: CampaignConfig,
    pipeline_schema: PipelineSchema | None = None,
) -> dict[str, dict]:
    """Compute parameter differences between stored and current campaign config."""
    from promptpotter.application.campaign.config import LoopConfig

    current = LoopConfig.from_campaign_config(
        campaign_config,
        pipeline_schema=pipeline_schema,
    ).model_dump()

    keys = [
        "max_rounds",
        "l1_patience",
        "n_variants",
        "creativity",
        "improvement_threshold",
        "model",
        "sp_budget_ttest",
        "seed",
    ]

    diffs: dict[str, dict] = {}
    for k in keys:
        sv = stored_config.get(k)
        cv = current.get(k)
        if sv != cv:
            diffs[k] = {"stored": sv, "current": cv}

    # Compare pipeline params (derived from schema)
    sp = stored_config.get("pipeline_params")
    cp = pipeline_schema.to_pipeline_params() if pipeline_schema else None
    if sp != cp:
        for pk in sorted(set(sp or {}) | set(cp or {})):
            sv = (sp or {}).get(pk)
            cv = (cp or {}).get(pk)
            if sv != cv:
                diffs[f"pp.{pk}"] = {"stored": sv, "current": cv}

    return diffs


def load_stored_campaign_config(
    store: ProjectStore,
    backend_id: str,
    experiment_id: str,
) -> dict | None:
    """Load stored experiment config for a campaign. Returns config dict or None."""
    from promptpotter.application.campaign.campaign_setup import resolve_campaign_id

    full_id = resolve_campaign_id(store, backend_id, experiment_id)
    if not full_id:
        return None
    campaign = store.campaigns.load(backend_id, full_id)
    if not campaign:
        return None
    return campaign.get("config")


def resolve_active_campaign_id(
    campaign_config: CampaignConfig,
    pipeline_schema: PipelineSchema | None,
    baseline_prompt_fields: dict | None,
    dataset: list[dict],
) -> str | None:
    """Compute the cycle ID matching the current config, or None on failure.

    Used by display layer to detect which stored campaign matches the active
    notebook/CLI configuration.
    """
    from promptpotter.application.campaign.config import LoopConfig
    from promptpotter.domain.cycle_identity import cycle_config_identity
    from promptpotter.domain.opt_search_point import OptSearchPoint

    try:
        config = LoopConfig.from_campaign_config(
            campaign_config,
            pipeline_schema=pipeline_schema,
        )
        bl_rendered = ""
        if baseline_prompt_fields:
            bl_rendered = OptSearchPoint.from_prompt_fields(baseline_prompt_fields).render()
        return cycle_config_identity(
            config, bl_rendered, dataset, strict=config.strict_cycle_identity
        )
    except Exception:
        logger.debug("Could not compute active campaign ID", exc_info=True)
        return None
