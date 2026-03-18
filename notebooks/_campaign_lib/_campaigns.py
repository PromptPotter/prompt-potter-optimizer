"""Campaign management: listing, config diffing, experiment dashboard."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from api.services.project_store import ProjectStore

from api.models.prompt_state import PromptState


__all__ = [
    "list_campaigns",
    "diff_campaign_config",
    "load_experiment_config",
    "show_experiment_dashboard",
    "apply_experiment_overrides",
]


def apply_experiment_overrides(
    campaign_config: dict,
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


def _resolve_experiment_id(
    store: "ProjectStore", backend_id: str, short_id: str,
) -> str | None:
    """Resolve short prefix/suffix to full campaign_id."""
    campaigns = store.campaigns.list_all(backend_id)
    matches = [c for c in campaigns
               if short_id in c["campaign_id"]]
    if len(matches) == 1:
        return matches[0]["campaign_id"]
    if len(matches) > 1:
        print(f"  Ambiguous ID '{short_id}' — matches:")
        for m in matches:
            print(f"    {m['campaign_id']}  {m['status']}  {m['n_trials']} rounds")
        return None
    print(f"  No campaign matching '{short_id}'")
    return None


def list_campaigns(
    store: "ProjectStore",
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
            for k in ["max_rounds", "patience", "n_variants", "creativity",
                       "improvement_threshold", "model", "temperature",
                       "sample_size", "seed"]:
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
            print(f"    model={model}  patience={patience}  "
                  f"rounds={max_r}  sample={sample}")
        print(f"    updated: {updated}")
        print()

    print("=" * 72)
    print(f'{len(campaigns)} campaign(s) — pass campaign_id="cycle_..." to see full config\n')
    return campaigns


def diff_campaign_config(
    store: "ProjectStore",
    backend_id: str,
    campaign_id: str,
    campaign_config: dict,
    pipeline_params: dict | None = None,
) -> dict:
    """Show parameter differences between current config and a stored campaign."""
    from api.services.campaign.models import CycleConfig

    campaign = store.campaigns.load(backend_id, campaign_id)
    if campaign is None:
        print(f"Campaign {campaign_id} not found.")
        return {}

    stored = campaign.get("config", {})
    current = CycleConfig.from_campaign_config(
        campaign_config, pipeline_params=pipeline_params,
    ).model_dump()

    keys = ["max_rounds", "patience", "n_variants", "creativity",
            "improvement_threshold", "model", "temperature",
            "sample_size", "seed"]

    diffs: dict = {}
    print(f"\nDiff: current config vs {campaign_id}")
    print("=" * 60)
    any_diff = False
    for k in keys:
        sv = stored.get(k)
        cv = current.get(k)
        if sv != cv:
            print(f"  {k}: {sv} (stored) → {cv} (current)")
            diffs[k] = {"stored": sv, "current": cv}
            any_diff = True

    # Pipeline params diff
    sp = stored.get("pipeline_params")
    cp = current.get("pipeline_params")
    if sp != cp:
        sp_keys = set(sp or {})
        cp_keys = set(cp or {})
        for pk in sorted(sp_keys | cp_keys):
            sv = (sp or {}).get(pk)
            cv = (cp or {}).get(pk)
            if sv != cv:
                sv_str = str(sv)[:40] if sv is not None else "(none)"
                cv_str = str(cv)[:40] if cv is not None else "(none)"
                print(f"  pp.{pk}: {sv_str} → {cv_str}")
                diffs[f"pp.{pk}"] = {"stored": sv, "current": cv}
                any_diff = True

    if not any_diff:
        print("  (identical — will resume this campaign)")
    print("=" * 60)
    return diffs


def load_experiment_config(
    store: "ProjectStore",
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


def show_experiment_dashboard(
    store: "ProjectStore | None" = None,
    backend_id: str = "",
    *,
    experiment_id: str | None = None,
    campaign_config: dict | None = None,
    eval_data: list | None = None,
    pipeline_params: dict | None = None,
    baseline_prompt_state: dict | None = None,
    svc: dict | None = None,
) -> dict | list | None:
    """Unified experiment dashboard — overview or detail by experiment ID."""
    # svc shorthand
    if svc is not None:
        store = store or svc.get("store")
        backend_id = backend_id or svc.get("backend_id", "")

    # --- Resolve short ID ---
    full_id = None
    if experiment_id is not None:
        full_id = _resolve_experiment_id(store, backend_id, experiment_id)
        if full_id is None:
            return None

    # --- Detect active campaign from current config ---
    active_id = None
    if campaign_config is not None and eval_data is not None:
        try:
            from api.services.campaign.feedback_cycle import cycle_config_identity
            from api.services.campaign.models import CycleConfig

            config = CycleConfig.from_campaign_config(
                campaign_config, pipeline_params=pipeline_params,
            )
            bl_rendered = ""
            if baseline_prompt_state:
                bl_rendered = PromptState(**baseline_prompt_state).render()
            active_id = cycle_config_identity(config, bl_rendered, eval_data)
        except Exception:
            pass

    # --- Detail mode ---
    if full_id is not None:
        campaign = store.campaigns.load(backend_id, full_id)
        if campaign is None:
            print(f"Campaign {full_id} not found.")
            return None

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
            for k in ["max_rounds", "patience", "n_variants", "creativity",
                       "improvement_threshold", "model", "temperature",
                       "sample_size", "seed"]:
                if k in cfg:
                    print(f"    {k}: {cfg[k]}")
            pp = cfg.get("pipeline_params")
            if pp:
                # Show keys only, not full nested dicts
                pp_keys = sorted(pp.keys())
                pp_summary = ", ".join(
                    f"{k}={str(pp[k])[:25]}" if not isinstance(pp[k], (dict, list))
                    else f"{k}=..."
                    for k in pp_keys[:6]
                )
                if len(pp_keys) > 6:
                    pp_summary += f" +{len(pp_keys) - 6} more"
                print(f"    pipeline_params: {{{pp_summary}}}")

        # Config diff
        if campaign_config is not None:
            print()
            diff_campaign_config(
                store, backend_id, full_id, campaign_config,
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
        return campaign

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
        for i, c in enumerate(sorted_camps):
            cid = c["campaign_id"]
            status = c["status"]
            n = c["n_trials"]
            best = f"{c['best_accuracy']:.1%}"
            base = f"{c['baseline_accuracy']:.1%}"
            is_active = cid == active_id

            marker = "  ●" if is_active else "   "
            tag = "  <-- active" if is_active else ""
            print(f"{marker} {cid}  {status:<12} {n} rounds  "
                  f"best={best}  base={base}{tag}")

            # Inline config
            full = store.campaigns.load(backend_id, cid)
            cfg = full.get("config", {}) if full else {}
            if cfg:
                model = str(cfg.get("model", "?"))[:30]
                patience = cfg.get("patience", "?")
                max_r = cfg.get("max_rounds", "?")
                sample = cfg.get("sample_size", "?")
                print(f"      patience={patience}  rounds={max_r}  "
                      f"sample={sample}  model={model}")

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
    return campaigns
