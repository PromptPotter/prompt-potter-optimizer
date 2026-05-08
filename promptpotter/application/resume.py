"""Campaign resume helpers — diff stored vs current config, resolve IDs, apply stored overrides."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.application.config import CampaignConfig

if TYPE_CHECKING:
    from promptpotter.application.bootstrap.session import Session
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.infrastructure.store import Stores

logger = logging.getLogger(__name__)

__all__ = [
    "ConfigDiff",
    "apply_stored_overrides",
    "diff_campaign_config",
    "load_and_apply_experiment",
    "load_stored_campaign_config",
    "resolve_campaign_id",
]


@dataclass(frozen=True)
class ConfigDiff:
    """One field's difference between a stored campaign config and the current config."""

    stored: Any
    current: Any


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
    full_id = resolve_campaign_id(store, backend_id, experiment_id)
    if not full_id:
        return None
    campaign = store.campaigns.load(backend_id, full_id)
    if not campaign:
        return None
    return campaign.get("config")


def load_and_apply_experiment(
    session: Session,
    campaign_config: CampaignConfig,
    experiment_id: str,
    pipeline_params: dict | None = None,
) -> tuple[CampaignConfig, dict | None]:
    """Load stored experiment config + merge it into the current ``CampaignConfig``."""
    stored_cfg = load_stored_campaign_config(session.store, session.backend_id, experiment_id)
    if not stored_cfg:
        return campaign_config, pipeline_params
    campaign_config, pp_override = apply_stored_overrides(campaign_config, stored_cfg)
    if pp_override:
        pipeline_params = pp_override
    return campaign_config, pipeline_params
