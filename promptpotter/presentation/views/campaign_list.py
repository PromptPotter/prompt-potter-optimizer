"""Render campaign list, detail, config diff, experiment dashboard."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from promptpotter.application.campaign.cycle_store import resolve_campaign_id
from promptpotter.application.campaign.data import summarize_dataset_runs
from promptpotter.application.campaign.utils import (
    diff_campaign_config,
    load_and_apply_experiment,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from promptpotter.application.campaign.campaign_setup import Session
    from promptpotter.application.campaign.config import CampaignConfig
    from promptpotter.infrastructure.store import Stores

__all__ = [
    "render_config_diff",
    "render_experiment_dashboard",
    "render_resume_hint",
]


def render_resume_hint(
    status: str,
    n_trials: int,
    *,
    indent: str = "  ",
    is_matching: bool = True,
) -> str:
    """One-line resume/replay/fresh hint for a campaign."""
    if not is_matching:
        return f"{indent}→ Config does NOT match — update campaign_config to resume"
    if status == "completed" and n_trials > 0:
        return f"{indent}→ Re-run will REPLAY from cache (campaign completed)"
    if n_trials > 0:
        return f"{indent}→ Re-run will RESUME from round {n_trials}"
    return f"{indent}→ Re-run starts fresh"


def _format_campaign_summary(
    store: Stores,
    backend_id: str,
    campaign: dict,
    *,
    active_id: str | None = None,
    show_updated: bool = False,
) -> list[str]:
    """One campaign's summary block (header line + inline config + optional updated)."""
    cid = campaign["campaign_id"]
    status = campaign["status"]
    n = campaign["n_trials"]
    best = f"{campaign['best_accuracy']:.1%}"
    base = f"{campaign['baseline_accuracy']:.1%}"
    is_active = cid == active_id

    out: list[str] = []
    if active_id is not None:
        marker = "  ●" if is_active else "   "
        tag = "  <-- active" if is_active else ""
        out.append(f"{marker} {cid}  {status:<12} {n} rounds  best={best}  base={base}{tag}")
    else:
        out.append(f"  {cid}  {status}  {n} rounds  best={best}  base={base}")

    full = store.campaigns.load(backend_id, cid)
    cfg = full.get("config", {}) if full else {}
    if cfg:
        model = str(cfg.get("model", "?"))[:30]
        patience = cfg.get("l1_patience", "?")
        max_r = cfg.get("max_rounds", "?")
        sample = cfg.get("sp_budget_ttest", "?")
        indent = "      " if active_id is not None else "    "
        out.append(f"{indent}patience={patience}  rounds={max_r}  sample={sample}  model={model}")

    if show_updated:
        updated = campaign["updated_at"][:16].replace("T", " ")
        out.append(f"    updated: {updated}")
    return out


def render_config_diff(
    diffs: dict,
    campaign_id: str,
) -> str:
    """Pretty-print a diff dict (output of ``diff_campaign_config``)."""
    lines = ["", f"Diff: current config vs {campaign_id}", "=" * 60]
    if not diffs:
        lines.append("  (identical — will resume this campaign)")
    else:
        for k, v in diffs.items():
            sv_str = str(v.stored)[:40] if v.stored is not None else "(none)"
            cv_str = str(v.current)[:40] if v.current is not None else "(none)"
            lines.append(f"  {k}: {sv_str} → {cv_str}")
    lines.append("=" * 60)
    return "\n".join(lines)


def _detect_active_campaign(
    session: Session | None,
    campaign_config: CampaignConfig | None,
    dataset: list | None,
    baseline_prompt_fields: dict | None,
) -> str | None:
    """Compute the cycle hash of the current config + dataset, or None."""
    if not (campaign_config and dataset):
        return None
    try:
        from promptpotter.domain.cycle_identity import cycle_config_identity
        from promptpotter.domain.opt_search_point import OptSearchPoint

        ps = session.pipeline_schema if session else None
        base_pp = ps.to_pipeline_params() if ps else {}
        baseline_osp = OptSearchPoint.from_prompt_fields(baseline_prompt_fields or {})
        baseline_jsp = baseline_osp.to_job_search_point(base_pipeline_params=base_pp, schema=ps)
        return cycle_config_identity(baseline_jsp, dataset)
    except Exception:
        logger.debug("Could not compute active campaign ID", exc_info=True)
        return None


def render_experiment_dashboard(
    *,
    store: Stores | None = None,
    backend_id: str = "",
    experiment_id: str | None = None,
    campaign_config: CampaignConfig | None = None,
    dataset: list | None = None,
    pipeline_params: dict | None = None,
    baseline_prompt_fields: dict | None = None,
    session: Session | None = None,
) -> tuple[str, dict, CampaignConfig | None]:
    """Unified experiment dashboard — overview or detail by experiment ID.

    Returns ``(rendered_text, pipeline_params, campaign_config)``. When
    ``experiment_id`` is provided, applies stored overrides via
    ``load_and_apply_experiment``; the returned ``campaign_config`` reflects
    those overrides so the notebook can write them back.
    """
    if session is not None:
        store = store or session.store
        backend_id = backend_id or session.backend_id

    pre_lines: list[str] = []
    if experiment_id and session is not None and campaign_config is not None:
        campaign_config, pipeline_params = load_and_apply_experiment(
            session, campaign_config, experiment_id, pipeline_params
        )
        if pipeline_params:
            session.pipeline_params = pipeline_params
        pre_lines.append(f"  Loaded config from experiment {experiment_id}")

    full_id: str | None = None
    if experiment_id is not None:
        assert store is not None
        full_id = resolve_campaign_id(store, backend_id, experiment_id)
        if full_id is None:
            return "\n".join(pre_lines), pipeline_params or {}, campaign_config

    active_id = _detect_active_campaign(session, campaign_config, dataset, baseline_prompt_fields)

    # Detail mode
    if full_id is not None:
        assert store is not None
        campaign = store.campaigns.load(backend_id, full_id)
        if campaign is None:
            pre_lines.append(f"Campaign {full_id} not found.")
            return "\n".join(pre_lines), pipeline_params or {}, campaign_config

        status = campaign["status"]
        n = campaign["n_trials"]
        best = f"{campaign['best_accuracy']:.1%}"
        base = f"{campaign['baseline_accuracy']:.1%}"
        updated = campaign["updated_at"][:16].replace("T", " ")

        lines = [
            *pre_lines,
            "",
            "=" * 72,
            f"  EXPERIMENT: {full_id}",
            f"  Status: {status}  |  Rounds: {n}  |  Best: {best}  |  Base: {base}",
            f"  Updated: {updated}",
        ]

        cfg = campaign.get("config", {})
        if cfg:
            lines.append("")
            lines.append("  Config (copy to campaign_config to resume):")
            for k in (
                "max_rounds",
                "l1_patience",
                "n_variants",
                "creativity",
                "improvement_threshold",
                "model",
                "sp_budget_ttest",
                "seed",
            ):
                if k in cfg:
                    lines.append(f"    {k}: {cfg[k]}")
            pp = cfg.get("pipeline_params")
            if pp:
                pp_keys = sorted(pp.keys())
                pp_summary = ", ".join(
                    f"{k}={str(pp[k])[:25]}" if not isinstance(pp[k], (dict, list)) else f"{k}=..."
                    for k in pp_keys[:6]
                )
                if len(pp_keys) > 6:
                    pp_summary += f" +{len(pp_keys) - 6} more"
                lines.append(f"    pipeline_params: {{{pp_summary}}}")

        if campaign_config is not None:
            schema = session.pipeline_schema if session else None
            diffs = diff_campaign_config(campaign.get("config", {}), campaign_config, schema)
            lines.append(render_config_diff(diffs, full_id))

        is_active = full_id == active_id
        lines.append(render_resume_hint(status, n, is_matching=is_active))
        lines.append("=" * 72)
        lines.append("")
        return "\n".join(lines), pipeline_params or {}, campaign_config

    # Overview mode
    assert store is not None
    runs = store.dataset_runs.list_all(backend_id)
    run_summary = summarize_dataset_runs(runs)
    source_str = ", ".join(f"{v} {k}" for k, v in sorted(run_summary.by_source.items()))
    campaigns = store.campaigns.list_all(backend_id)

    lines = [
        *pre_lines,
        "",
        "=" * 72,
        f"  EXPERIMENT DASHBOARD ({backend_id})",
        "=" * 72,
    ]
    if runs:
        lines.append(f"  Dataset runs: {run_summary.total} total ({source_str})")
        if run_summary.best_accuracy > 0:
            lines.append(
                f"  Best result: {run_summary.best_accuracy:.1%} ({run_summary.best_name})"
            )
    else:
        lines.append("  Dataset runs: none")

    if not campaigns:
        lines.append("")
        lines.append("  No campaigns yet.")
    else:
        lines.append("")
        lines.append("  Campaigns (most recent first):")
        for c in sorted(campaigns, key=lambda x: x["updated_at"], reverse=True):
            lines.extend(_format_campaign_summary(store, backend_id, c, active_id=active_id))
            if c["campaign_id"] == active_id:
                lines.append(render_resume_hint(c["status"], c["n_trials"], indent="      "))
            lines.append("")

    lines.append("=" * 72)
    lines.append('  Set experiment_id="<short_id>" to see full config and diff')
    if active_id:
        lines.append(f"  Active: {active_id}")
    elif campaign_config is not None:
        lines.append("  No matching campaign — feedback cycle will create new")
    lines.append("")

    return "\n".join(lines), pipeline_params or {}, campaign_config
