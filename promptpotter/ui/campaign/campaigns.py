"""Campaign management: listing, config diffing, experiment dashboard."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from promptpotter.services.campaign.campaign_setup import (
    apply_stored_overrides,
)
from promptpotter.services.campaign.campaign_setup import (
    diff_campaign_config as _diff_campaign_config,
)
from promptpotter.services.campaign.campaign_setup import (
    resolve_campaign_id as _resolve_campaign_id,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from promptpotter.models.pipeline_schema import PipelineSchema
    from promptpotter.services.campaign.campaign_setup import BackendContext
    from promptpotter.services.campaign.config import CampaignConfig
    from promptpotter.services.project_store import ProjectStore


__all__ = [
    "apply_stored_overrides",
    "diff_campaign_config",
    "list_campaigns",
    "load_and_apply_experiment",
    "load_stored_campaign_config",
    "show_experiment_dashboard",
]


def _print_resume_hint(
    status: str,
    n_trials: int,
    *,
    indent: str = "  ",
    is_matching: bool = True,
) -> None:
    """Print a one-line resume/replay/fresh hint for a campaign."""
    if not is_matching:
        print(f"{indent}→ Config does NOT match — update campaign_config to resume")
        return
    if status == "completed" and n_trials > 0:
        print(f"{indent}→ Re-run will REPLAY from cache (campaign completed)")
    elif n_trials > 0:
        print(f"{indent}→ Re-run will RESUME from round {n_trials}")
    else:
        print(f"{indent}→ Re-run starts fresh")


def _format_campaign_summary(
    store: ProjectStore,
    backend_id: str,
    campaign: dict,
    *,
    active_id: str | None = None,
    show_updated: bool = False,
) -> None:
    """Print one campaign's summary line with optional active marker and config."""
    cid = campaign["campaign_id"]
    status = campaign["status"]
    n = campaign["n_trials"]
    best = f"{campaign['best_accuracy']:.1%}"
    base = f"{campaign['baseline_accuracy']:.1%}"
    is_active = cid == active_id

    if active_id is not None:
        marker = "  ●" if is_active else "   "
        tag = "  <-- active" if is_active else ""
        print(f"{marker} {cid}  {status:<12} {n} rounds  best={best}  base={base}{tag}")
    else:
        print(f"  {cid}  {status}  {n} rounds  best={best}  base={base}")

    full = store.campaigns.load(backend_id, cid)
    cfg = full.get("config", {}) if full else {}
    if cfg:
        model = str(cfg.get("model", "?"))[:30]
        patience = cfg.get("l1_patience", "?")
        max_r = cfg.get("max_rounds", "?")
        sample = cfg.get("sp_budget_ttest", "?")
        indent = "      " if active_id is not None else "    "
        print(f"{indent}patience={patience}  rounds={max_r}  sample={sample}  model={model}")

    if show_updated:
        updated = campaign["updated_at"][:16].replace("T", " ")
        print(f"    updated: {updated}")


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
                "l1_patience",
                "n_variants",
                "creativity",
                "improvement_threshold",
                "model",
                "sp_budget_ttest",
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
        _format_campaign_summary(store, backend_id, c, show_updated=True)
        print()

    print("=" * 72)
    print(f'{len(campaigns)} campaign(s) — pass campaign_id="cycle_..." to see full config\n')
    return campaigns


def diff_campaign_config(
    store: ProjectStore,
    backend_id: str,
    campaign_id: str,
    campaign_config: CampaignConfig,
    pipeline_schema: PipelineSchema | None = None,
) -> dict:
    """Show parameter differences between current config and a stored campaign."""
    campaign = store.campaigns.load(backend_id, campaign_id)
    if campaign is None:
        print(f"Campaign {campaign_id} not found.")
        return {}

    diffs = _diff_campaign_config(
        campaign.get("config", {}),
        campaign_config,
        pipeline_schema,
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


def load_stored_campaign_config(
    store: ProjectStore,
    backend_id: str,
    experiment_id: str,
) -> dict | None:
    """Load stored campaign config for an experiment.

    Returns the stored config dict, or None if not found.
    """
    full_id = _resolve_campaign_id(store, backend_id, experiment_id)
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
    stored_cfg = load_stored_campaign_config(
        session.store,
        session.backend_id,
        experiment_id,
    )
    if stored_cfg:
        pp_override = apply_stored_overrides(campaign_config, stored_cfg)
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
    if experiment_id and session is not None and campaign_config is not None:
        pipeline_params = load_and_apply_experiment(
            session,
            campaign_config,
            experiment_id,
            pipeline_params,
        )

    # --- Resolve short ID ---
    full_id = None
    if experiment_id is not None:
        assert store is not None
        full_id = _resolve_campaign_id(store, backend_id, experiment_id)
        if full_id is None:
            return pipeline_params or {}

    # --- Detect active campaign from current config ---
    active_id = None
    if campaign_config is not None and dataset is not None:
        from promptpotter.services.campaign.lifecycle import resolve_active_campaign_id

        active_id = resolve_active_campaign_id(
            campaign_config,
            session.pipeline_schema if session else None,
            baseline_prompt_fields,
            dataset,
        )

    # --- Detail mode ---
    if full_id is not None:
        assert store is not None
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
                "l1_patience",
                "n_variants",
                "creativity",
                "improvement_threshold",
                "model",
                "sp_budget_ttest",
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
                pipeline_schema=session.pipeline_schema if session else None,
            )

        is_active = full_id == active_id
        _print_resume_hint(status, n, is_matching=is_active)

        print(f"{'=' * 72}\n")
        return pipeline_params or {}

    # --- Overview mode ---
    assert store is not None
    # Dataset runs summary
    from promptpotter.services.campaign.campaign_data import summarize_dataset_runs

    runs = store.dataset_runs.list_all(backend_id)
    run_summary = summarize_dataset_runs(runs)
    source_str = ", ".join(f"{v} {k}" for k, v in sorted(run_summary.by_source.items()))

    # Campaigns
    campaigns = store.campaigns.list_all(backend_id)

    print(f"\n{'=' * 72}")
    print(f"  EXPERIMENT DASHBOARD ({backend_id})")
    print(f"{'=' * 72}")
    if runs:
        print(f"  Dataset runs: {run_summary.total} total ({source_str})")
        if run_summary.best_accuracy > 0:
            print(f"  Best result: {run_summary.best_accuracy:.1%} ({run_summary.best_name})")
    else:
        print("  Dataset runs: none")

    if not campaigns:
        print("\n  No campaigns yet.")
    else:
        print("\n  Campaigns (most recent first):")
        sorted_camps = sorted(campaigns, key=lambda x: x["updated_at"], reverse=True)
        for c in sorted_camps:
            _format_campaign_summary(store, backend_id, c, active_id=active_id)
            if c["campaign_id"] == active_id:
                _print_resume_hint(c["status"], c["n_trials"], indent="      ")
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
