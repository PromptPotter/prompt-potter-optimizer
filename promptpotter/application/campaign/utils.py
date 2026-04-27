"""Campaign lifecycle ops — save the winner, diff stored vs current config, apply stored overrides."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.application.campaign.config import CampaignConfig
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.infrastructure.store import Stores

logger = logging.getLogger(__name__)

__all__ = [
    "ConfigDiff",
    "apply_stored_overrides",
    "diff_campaign_config",
    "load_stored_campaign_config",
    "save_campaign_winner",
]


@dataclass(frozen=True)
class ConfigDiff:
    """One field's difference between a stored campaign config and the current config."""

    stored: Any
    current: Any


def apply_stored_overrides(
    campaign_config: CampaignConfig,
    stored_cfg: dict,
) -> tuple[CampaignConfig, dict | None]:
    """Merge stored experiment config into a new ``CampaignConfig`` copy.

    Returns ``(updated_config, stored_pipeline_params)``.  Does NOT mutate
    the input.  Callers apply ``stored_pipeline_params`` (when non-None) by
    writing ``session.pipeline_params = pp``.
    """
    patch = campaign_config.model_dump()
    opt = patch.setdefault("optimization", {})
    for key in (
        "max_rounds",
        "l1_patience",
        "n_variants",
        "creativity",
        "improvement_threshold",
        "seed",
    ):
        if (val := stored_cfg.get(key)) is not None:
            opt[key] = val
    if (model := stored_cfg.get("model")) is not None:
        patch.setdefault("optimizer_llm", {})["model"] = model
    if (sp := stored_cfg.get("sp_budget_ttest")) is not None:
        patch["sp_budget_ttest"] = sp
    updated = CampaignConfig.model_validate(patch)

    stored_pp = stored_cfg.get("pipeline_params")
    return updated, (stored_pp if stored_pp else None)


def save_campaign_winner(
    campaign_rounds: list,
    campaign_config: CampaignConfig,
    store: Stores,
    backend_id: str,
    *,
    campaign_id: str | None = None,
) -> dict:
    """Find best round, save to store + link to campaign. Returns save_data dict."""
    from datetime import UTC, datetime

    from promptpotter.application.campaign.cycle_store import resolve_campaign_id

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
        "config": campaign_config.model_dump(),
        "saved_at": datetime.now(UTC).isoformat(),
    }

    filename = f"optimization/campaign_winner_{winner.lineage.id[:12]}.json"
    store.backends.save_sync(backend_id, filename, save_data)

    if campaign_id:
        full_id = resolve_campaign_id(store, backend_id, campaign_id)
        if full_id:
            with graceful("Campaign metadata update skipped", level=logging.DEBUG):
                store.campaigns.update(
                    backend_id,
                    full_id,
                    {
                        "winner_prompt_fields_id": winner.lineage.id,
                        "winner_accuracy": winner_acc,
                        "winner_filename": filename,
                    },
                )

    logger.info("Winner saved: %s (acc=%.1f%%)", filename, winner_acc * 100)
    return {
        **save_data,
        "winner_id": winner.lineage.id,
        "filename": filename,
        "backend_id": backend_id,
    }


def diff_campaign_config(
    stored_config: dict,
    campaign_config: CampaignConfig,
    pipeline_schema: PipelineSchema | None = None,
) -> dict[str, ConfigDiff]:
    """Compute parameter differences between stored and current campaign config."""
    opt = campaign_config.optimization
    current = {
        "max_rounds": opt.max_rounds,
        "l1_patience": opt.l1_patience,
        "n_variants": opt.n_variants,
        "creativity": opt.creativity,
        "improvement_threshold": opt.improvement_threshold,
        "seed": opt.seed,
        "model": campaign_config.optimizer_llm.model,
        "sp_budget_ttest": campaign_config.sp_budget_ttest,
    }
    diffs: dict[str, ConfigDiff] = {
        key: ConfigDiff(stored=stored_config.get(key), current=cv)
        for key, cv in current.items()
        if stored_config.get(key) != cv
    }

    sp = stored_config.get("pipeline_params")
    cp = pipeline_schema.to_pipeline_params() if pipeline_schema else None
    if sp != cp:
        for pk in sorted(set(sp or {}) | set(cp or {})):
            sv = (sp or {}).get(pk)
            cv = (cp or {}).get(pk)
            if sv != cv:
                diffs[f"pp.{pk}"] = ConfigDiff(stored=sv, current=cv)

    return diffs


def load_stored_campaign_config(
    store: Stores,
    backend_id: str,
    experiment_id: str,
) -> dict | None:
    """Load stored experiment config for a campaign. Returns config dict or None."""
    from promptpotter.application.campaign.cycle_store import resolve_campaign_id

    full_id = resolve_campaign_id(store, backend_id, experiment_id)
    if not full_id:
        return None
    campaign = store.campaigns.load(backend_id, full_id)
    if not campaign:
        return None
    return campaign.get("config")
