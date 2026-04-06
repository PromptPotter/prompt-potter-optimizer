"""Campaign management: listing, config diffing, experiment dashboard."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from promptpotter.models.opt_search_point import OptSearchPoint
from promptpotter.services.campaign.bootstrap import (
    apply_experiment_overrides,
)
from promptpotter.services.campaign.bootstrap import (
    diff_campaign_config as _diff_campaign_config,
)
from promptpotter.services.campaign.bootstrap import (
    resolve_experiment_id as _resolve_experiment_id,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from promptpotter.services.campaign.config import CampaignConfig
    from promptpotter.services.campaign.bootstrap import BackendContext
    from promptpotter.services.project_store import ProjectStore


__all__ = [
    "apply_experiment_overrides",
    "diff_campaign_config",
    "list_campaigns",
    "load_and_apply_experiment",
    "load_experiment_config",
    "show_experiment_dashboard",
]


def list_campaigns(
    store: ProjectStore,
    backend_id: str,
    *,
    campaign_id: str | None = None,
) -> list[dict] | dict | None:
    """Interactive campaign explorer — two modes in one cell.

    **Overview** (no campaign_id): table of all campaigns with inline config.
    **Detail** (campaign_id provided): full config for one campaign, copy-pasteable.
    """
    import json as _json

    if campaign_id is not None:
        # --- Detail mode ---
        campaign = store.campaigns.load(backend_id, campaign_id)
        if campaign is None:
            print(f"Campaign {campaign_id} not found.")
            return None

        status = campaign["status"]
        n = campaign["n_trials"]
        best = f"{campaign['best_accuracy']:.1%}"
        base = f"{campaign['baseline_accuracy']:.1%}"
        updated = campaign["updated_at"][:16].replace("T", " ")

        print(f"\nCAMPAIGN: {campaign_id}")
        print("=" * 72)
        print(f"  Status: {status}  |  Rounds: {n}  |  Best: {best}  |  Baseline: {base}")
        print(f"  Updated: {updated}")

        cfg = campaign.get("config", {})
        if cfg:
            print("\n  Config (copy to campaign_config):")
            for k in [
                "max_rounds",
                "patience",
                "n_variants",
                "creativity",
                "improvement_threshold",
                "model",
                "sample_size",
                "seed",
            ]:
                if k in cfg:
                    print(f"    {k}: {cfg[k]}")
            pp = cfg.get("pipeline_params")
            if pp:
                pp_str = _json.dumps(pp, indent=6)
                print(f"    pipeline_params: {pp_str}")

        print("=" * 72)
        return campaign

    # --- Overview mode ---
    campaigns = store.campaigns.list_all(backend_id)

    if not campaigns:
        print("No campaigns found.")
        return []

    print(f"\nCAMPAIGNS ({backend_id})")
    print("=" * 72)
    for c in sorted(campaigns, key=lambda x: x["updated_at"], reverse=True):
        cid = c["campaign_id"]
        status = c["status"]
        n = c["n_trials"]
        best = f"{c['best_accuracy']:.1%}"
        base = f"{c['baseline_accuracy']:.1%}"
        updated = c["updated_at"][:16].replace("T", " ")

        print(f"  {cid}  {status}  {n} rounds  best={best}  base={base}")

        # Inline config from stored campaign data
        full = store.campaigns.load(backend_id, cid)
        cfg = full.get("config", {}) if full else {}
        if cfg:
            model = cfg.get("model", "?")
            patience = cfg.get("patience", "?")
            max_r = cfg.get("max_rounds", "?")
            sample = cfg.get("sample_size", "?")
            print(f"    model={model}  patience={patience}  rounds={max_r}  sample={sample}")
        print(f"    updated: {updated}")
        print()

    print("=" * 72)
    print(f'{len(campaigns)} campaign(s) — pass campaign_id="cycle_..." to see full config\n')
    return campaigns


def diff_campaign_config(
    store: ProjectStore,
    backend_id: str,
    campaign_id: str,
    campaign_config: CampaignConfig,
    pipeline_params: dict | None = None,
) -> dict:
    """Show parameter differences between current config and a stored campaign."""
    campaign = store.campaigns.load(backend_id, campaign_id)
    if campaign is None:
        print(f"Campaign {campaign_id} not found.")
        return {}

    diffs = _diff_campaign_config(
        campaign.get("config", {}),
        campaign_config,
        pipeline_params,
    )

    print(f"\nDiff: current config vs {campaign_id}")
    print("=" * 60)
    for k, v in diffs.items():
        sv_str = str(v["stored"])[:40] if v["stored"] is not None else "(none)"
        cv_str = str(v["current"])[:40] if v["current"] is not None else "(none)"
        print(f"  {k}: {sv_str} → {cv_str}")
    if not diffs:
        print("  (identical — will resume this campaign)")
    print("=" * 60)
    return diffs


def load_experiment_config(
    store: ProjectStore,
    backend_id: str,
    experiment_id: str,
) -> dict | None:
    """Load stored campaign config for an experiment.

    Returns the stored config dict, or None if not found.
    """
    full_id = _resolve_experiment_id(store, backend_id, experiment_id)
    if not full_id:
        return None
    campaign = store.campaigns.load(backend_id, full_id)
    if not campaign:
        return None
    return campaign.get("config", {})


def load_and_apply_experiment(
    session: BackendContext,
    campaign_config: CampaignConfig,
    experiment_id: str,
    pipeline_params: dict | None = None,
) -> dict | None:
    """Load stored experiment config, apply overrides, return pipeline_params.

    Convenience wrapper used by the notebook's experiment dashboard cell.
    Returns updated *pipeline_params* (or the original if nothing changed).
    """
    stored_cfg = load_experiment_config(
        session.store,
        session.backend_id,
        experiment_id,
    )
    if stored_cfg:
        pp_override = apply_experiment_overrides(campaign_config, stored_cfg)
        if pp_override:
            pipeline_params = pp_override
        print(f"  Loaded config from experiment {experiment_id}")
    return pipeline_params


def show_experiment_dashboard(
    store: ProjectStore | None = None,
    backend_id: str = "",
    *,
    experiment_id: str | None = None,
    campaign_config: CampaignConfig | None = None,
    dataset: list | None = None,
    pipeline_params: dict | None = None,
    baseline_prompt_fields: dict | None = None,
    session: BackendContext | None = None,
) -> dict:
    """Unified experiment dashboard — overview or detail by experiment ID.

    When *experiment_id* is truthy, loads and applies experiment overrides
    before displaying. Always returns ``pipeline_params`` (possibly updated).
    """
    # session shorthand
    if session is not None:
        store = store or session.store
        backend_id = backend_id or session.backend_id

    # Apply experiment overrides when resuming
    if experiment_id and session is not None:
        pipeline_params = load_and_apply_experiment(
            session,
            campaign_config,
            experiment_id,
            pipeline_params,
        )

    # --- Resolve short ID ---
    full_id = None
    if experiment_id is not None:
        full_id = _resolve_experiment_id(store, backend_id, experiment_id)
        if full_id is None:
            return pipeline_params or {}

    # --- Detect active campaign from current config ---
    active_id = None
    if campaign_config is not None and dataset is not None:
        try:
            from promptpotter.services.campaign.config import RunConfig
            from promptpotter.services.campaign.lifecycle import cycle_config_identity

            config = RunConfig.from_campaign_config(
                campaign_config,
                pipeline_params=pipeline_params,
            )
            bl_rendered = ""
            if baseline_prompt_fields:
                bl_rendered = OptSearchPoint.from_prompt_fields(baseline_prompt_fields).render()
            active_id = cycle_config_identity(config, bl_rendered, dataset)
        except Exception:
            logger.debug("Could not compute active campaign ID", exc_info=True)

    # --- Detail mode ---
    if full_id is not None:
        campaign = store.campaigns.load(backend_id, full_id)
        if campaign is None:
            print(f"Campaign {full_id} not found.")
            return pipeline_params or {}

        status = campaign["status"]
        n = campaign["n_trials"]
        best = f"{campaign['best_accuracy']:.1%}"
        base = f"{campaign['baseline_accuracy']:.1%}"
        updated = campaign["updated_at"][:16].replace("T", " ")

        print(f"\n{'=' * 72}")
        print(f"  EXPERIMENT: {full_id}")
        print(f"  Status: {status}  |  Rounds: {n}  |  Best: {best}  |  Base: {base}")
        print(f"  Updated: {updated}")

        cfg = campaign.get("config", {})
        if cfg:
            print("\n  Config (copy to campaign_config to resume):")
            for k in [
                "max_rounds",
                "patience",
                "n_variants",
                "creativity",
                "improvement_threshold",
                "model",
                "sample_size",
                "seed",
            ]:
                if k in cfg:
                    print(f"    {k}: {cfg[k]}")
            pp = cfg.get("pipeline_params")
            if pp:
                # Show keys only, not full nested dicts
                pp_keys = sorted(pp.keys())
                pp_summary = ", ".join(
                    f"{k}={str(pp[k])[:25]}" if not isinstance(pp[k], (dict, list)) else f"{k}=..."
                    for k in pp_keys[:6]
                )
                if len(pp_keys) > 6:
                    pp_summary += f" +{len(pp_keys) - 6} more"
                print(f"    pipeline_params: {{{pp_summary}}}")

        # Config diff
        if campaign_config is not None:
            print()
            diff_campaign_config(
                store,
                backend_id,
                full_id,
                campaign_config,
                pipeline_params=pipeline_params,
            )

        is_active = full_id == active_id
        if is_active:
            if status == "completed" and n > 0:
                print("  → Re-run will REPLAY from cache (campaign completed)")
            elif n > 0:
                print(f"  → Re-run will RESUME from round {n}")
            else:
                print("  → Re-run starts fresh")
        else:
            print("  → Config does NOT match — update campaign_config to resume")

        print(f"{'=' * 72}\n")
        return pipeline_params or {}

    # --- Overview mode ---
    # Dataset runs summary
    runs = store.dataset_runs.list_all(backend_id)
    by_source: dict[str, int] = {}
    best_acc = 0.0
    best_name = ""
    for r in runs:
        name = r.get("name", "")
        source = name.split("_")[0] if "_" in name else "other"
        by_source[source] = by_source.get(source, 0) + 1
        acc = r.get("scores", {}).get("accuracy", 0.0) or 0.0
        if acc > best_acc:
            best_acc = acc
            best_name = name

    source_str = ", ".join(f"{v} {k}" for k, v in sorted(by_source.items()))

    # Campaigns
    campaigns = store.campaigns.list_all(backend_id)

    print(f"\n{'=' * 72}")
    print(f"  EXPERIMENT DASHBOARD ({backend_id})")
    print(f"{'=' * 72}")
    if runs:
        print(f"  Dataset runs: {len(runs)} total ({source_str})")
        if best_acc > 0:
            print(f"  Best result: {best_acc:.1%} ({best_name})")
    else:
        print("  Dataset runs: none")

    if not campaigns:
        print("\n  No campaigns yet.")
    else:
        print("\n  Campaigns (most recent first):")
        sorted_camps = sorted(campaigns, key=lambda x: x["updated_at"], reverse=True)
        for _i, c in enumerate(sorted_camps):
            cid = c["campaign_id"]
            status = c["status"]
            n = c["n_trials"]
            best = f"{c['best_accuracy']:.1%}"
            base = f"{c['baseline_accuracy']:.1%}"
            is_active = cid == active_id

            marker = "  ●" if is_active else "   "
            tag = "  <-- active" if is_active else ""
            print(f"{marker} {cid}  {status:<12} {n} rounds  best={best}  base={base}{tag}")

            # Inline config
            full = store.campaigns.load(backend_id, cid)
            cfg = full.get("config", {}) if full else {}
            if cfg:
                model = str(cfg.get("model", "?"))[:30]
                patience = cfg.get("patience", "?")
                max_r = cfg.get("max_rounds", "?")
                sample = cfg.get("sample_size", "?")
                print(f"      patience={patience}  rounds={max_r}  sample={sample}  model={model}")

            # Resume hint for active
            if is_active:
                ac = store.campaigns.load(backend_id, cid)
                ac_status = ac["status"] if ac else "?"
                ac_n = ac["n_trials"] if ac else 0
                if ac_status == "completed" and ac_n > 0:
                    print("      → Re-run will REPLAY from cache")
                elif ac_n > 0:
                    print(f"      → Re-run will RESUME from round {ac_n}")
                else:
                    print("      → Re-run starts fresh")
            print()

    print(f"{'=' * 72}")
    hint = 'Set experiment_id="<short_id>" to see full config and diff'
    print(f"  {hint}")
    if active_id:
        print(f"  Active: {active_id}")
    elif campaign_config is not None:
        print("  No matching campaign — feedback cycle will create new")
    print()
    return pipeline_params or {}
