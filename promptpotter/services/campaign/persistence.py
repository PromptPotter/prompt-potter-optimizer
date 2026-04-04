"""Campaign persistence — resolve, override, save, and diff campaign config."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from promptpotter.services.campaign.config import CampaignConfig
    from promptpotter.services.project_store import ProjectStore

logger = logging.getLogger(__name__)

__all__ = [
    "apply_experiment_overrides",
    "diff_campaign_config",
    "resolve_experiment_id",
    "save_campaign_winner",
]


def resolve_experiment_id(
    store: ProjectStore,
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


def apply_experiment_overrides(
    campaign_config: CampaignConfig,
    stored_cfg: dict,
) -> dict | None:
    """Merge stored experiment config into campaign_config (in-place).

    Returns updated pipeline_params if stored, else None.
    """
    _OVERRIDE_KEYS: dict[str, tuple[str, ...]] = {
        "patience": ("optimization",),
        "max_rounds": ("optimization",),
        "n_variants": ("optimization",),
        "creativity": ("optimization",),
        "model": ("eval_llm",),
        "sample_size": (),
    }
    for key, path in _OVERRIDE_KEYS.items():
        val = stored_cfg.get(key)
        if val is not None:
            target = campaign_config
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
    experiment_id: str | None = None,
) -> dict:
    """Find best round, save to store + link to campaign. Returns save_data dict."""
    from datetime import datetime

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

    # Link winner to campaign store if experiment_id provided
    if experiment_id:
        full_id = resolve_experiment_id(store, backend_id, experiment_id)
        if full_id:
            try:
                store.campaigns.update(
                    backend_id,
                    full_id,
                    {
                        "winner_prompt_fields_id": winner.id,
                        "winner_accuracy": winner_acc,
                        "winner_filename": filename,
                    },
                )
            except (KeyboardInterrupt, asyncio.CancelledError):
                raise
            except Exception:
                pass  # campaign may not exist yet

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
    pipeline_params: dict | None = None,
) -> dict[str, dict]:
    """Compute parameter differences between stored and current campaign config.

    Returns dict of ``{param_name: {"stored": value, "current": value}}``.
    Includes nested pipeline_params diffs as ``pp.<key>`` entries.
    """
    from promptpotter.services.campaign.config import RunConfig

    current = RunConfig.from_campaign_config(
        campaign_config, pipeline_params=pipeline_params,
    ).model_dump()

    keys = [
        "max_rounds", "patience", "n_variants", "creativity",
        "improvement_threshold", "model",
        "sample_size", "seed",
    ]

    diffs: dict[str, dict] = {}
    for k in keys:
        sv = stored_config.get(k)
        cv = current.get(k)
        if sv != cv:
            diffs[k] = {"stored": sv, "current": cv}

    # Pipeline params diff
    sp = stored_config.get("pipeline_params")
    cp = current.get("pipeline_params")
    if sp != cp:
        sp_keys = set(sp or {})
        cp_keys = set(cp or {})
        for pk in sorted(sp_keys | cp_keys):
            sv = (sp or {}).get(pk)
            cv = (cp or {}).get(pk)
            if sv != cv:
                diffs[f"pp.{pk}"] = {"stored": sv, "current": cv}

    return diffs
