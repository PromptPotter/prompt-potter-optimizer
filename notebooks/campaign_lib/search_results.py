"""Downstream result wrappers: winner selection, campaign seeding, analytics."""

from __future__ import annotations

import logging

from api.models.opt_search_point import OptSearchPoint
from api.services.search import (
    select_scan_winner as _select_scan_winner,
    resume_or_build_diagnostic as _resume_or_build_diagnostic,
    load_filtered_variant_library as _load_filtered_variants,
)

from .display import (
    show_progress,
    show_scan_leaderboard,
    show_scan_query_difficulty,
)
from .setup import setup_llm

logger = logging.getLogger(__name__)

__all__ = [
    "resume_or_build_diagnostic",
    "select_scan_winner_notebook",
    "seed_campaign_from_scan",
    "show_scan_analytics",
]


async def resume_or_build_diagnostic(
    campaign_config: dict,
    baseline,
    baseline_results: list,
    svc: dict,
    eval_data: list,
    scan_variants: dict | None = None,
) -> tuple[str, object, list, list, dict]:
    """Resume or build smart search diagnostic set.

    Prepares LLM client and variant library internally, then delegates to the
    service-layer ``_resume_or_build_diagnostic()``.

    Returns:
        (plan_id, search_baseline, diagnostic, cached_profiles, variant_library)
    """
    # Prepare variant library: base + scan_variants merge
    pipeline_params = campaign_config.get("pipeline_params")
    variant_library = _load_filtered_variants(pipeline_params, svc.get("pipeline_schema"))
    if scan_variants:
        variant_library["pipeline_params"] = scan_variants

    # Prepare LLM client
    llm_client, llm_model = setup_llm(campaign_config)

    result = await _resume_or_build_diagnostic(
        campaign_config,
        baseline,
        baseline_results,
        llm_client,
        llm_model,
        svc["store"],
        svc["backend_id"],
        eval_data,
        variant_library=variant_library,
    )
    plan_id = result.plan_id
    search_baseline = result.search_baseline
    diagnostic = result.diagnostic
    axis_profiles = result.axis_profiles

    if axis_profiles:
        print(f"[RESUME] Plan {plan_id}: {len(axis_profiles)} axis profiles available")
    else:
        print(
            f"  search_baseline: {search_baseline.id[:12]} "
            f"(render: {len(search_baseline.render())} chars)"
        )

    return plan_id, search_baseline, diagnostic, axis_profiles, variant_library


def select_scan_winner_notebook(
    scan_df,
    axis_profiles: list[dict],
    baseline,
    scan_variants: dict[str, list],
):
    """Pick best from scan and print summary. No backend needed.

    Returns:
        SearchPoint with best values composed in.
    """
    if scan_df is None or (hasattr(scan_df, "empty") and scan_df.empty):
        print("No scan data available. Run sensitivity scan first.")
        return baseline

    best_sp = _select_scan_winner(
        scan_df,
        axis_profiles,
        baseline,
        scan_variants,
    )

    # Print summary
    improving = [
        p for p in axis_profiles if p["best_delta"] > 0 and p["exploration_budget"] != "skip"
    ]
    if improving:
        print(f"Selected best from {len(improving)} improving axes:")
        for p in improving:
            axis_rows = scan_df[scan_df["axis"] == p["axis"]]
            if not axis_rows.empty:
                best_row = axis_rows.loc[axis_rows["accuracy"].idxmax()]
                print(
                    f"  {p['axis']:<25s} best_delta=+{p['best_delta']:.1%}  "
                    f"value_idx={int(best_row['value_idx'])}  "
                    f"acc={best_row['accuracy']:.1%}"
                )
    else:
        print("No axes improved over baseline -- using baseline as-is.")

    if best_sp.sp_hash() != baseline.sp_hash():
        print(f"\nComposed winner: sp_hash={best_sp.sp_hash()[:12]}")

    return best_sp


def seed_campaign_from_scan(
    scan_df,
    axis_profiles: list,
    baseline,
    scan_variants: dict[str, list],
    campaign_rounds: list,
    campaign_config: dict,
):
    """Select scan winner, update pipeline_params, seed campaign_rounds.

    Returns:
        SearchPoint with best values composed in.
    """
    best_sp = select_scan_winner_notebook(
        scan_df,
        axis_profiles,
        baseline,
        scan_variants,
    )

    if best_sp.pipeline_params:
        existing_pp = campaign_config.get("pipeline_params", {})
        # Merge scan winner's params into existing pipeline_params, preserving
        # the steps list set by configure_pipeline()
        merged = {**existing_pp, **best_sp.pipeline_params}
        if "steps" in existing_pp:
            merged["steps"] = existing_pp["steps"]
            # Strip node-level config for excluded nodes so the backend
            # doesn't run them despite being absent from the steps list.
            excluded = set(campaign_config.get("exclude_nodes", []))
            for node_name in excluded:
                merged.pop(node_name, None)
        campaign_config["pipeline_params"] = merged
        # Summarize — avoid dumping full JSON schemas
        display_pp = {}
        for k, v in merged.items():
            if isinstance(v, dict) and len(str(v)) > 100:
                n_keys = len(v.get("properties", v))
                display_pp[k] = f"<{n_keys} fields>"
            else:
                display_pp[k] = v
        print(f"Updated pipeline_params: {display_pp}")

    # Get scan baseline accuracy as fallback when campaign_rounds is empty
    scan_baseline_acc = 0.0
    baseline_rows = scan_df[scan_df["delta"] == 0.0]
    if not baseline_rows.empty:
        scan_baseline_acc = baseline_rows.iloc[0]["accuracy"]

    bl = campaign_rounds[0] if campaign_rounds else {}
    # Create an OptSearchPoint for campaign_rounds lineage (from rendered prompt)
    search_opt = OptSearchPoint(
        instruction=best_sp.render(),
        changes_description=f"scan_winner (sp_hash={best_sp.sp_hash()[:12]})",
    )
    campaign_rounds.append(
        {
            "round": "search",
            "label": f"smart_search ({search_opt.changes_description or search_opt.id[:12]})",
            "prompt_fields": search_opt,
            "accuracy": bl.get("accuracy", scan_baseline_acc),
            "hits": bl.get("hits", 0),
            "total": bl.get("total", 0),
            "results": bl.get("results", []),
        }
    )
    show_progress(campaign_rounds)

    return best_sp


def show_scan_analytics(scan_df, axis_profiles, svc: dict):
    """Display scan leaderboard and query difficulty if results are available.

    Returns difficulty_df (or None if scan_df is empty/None).
    """
    if scan_df is None or scan_df.empty:
        return None
    show_scan_leaderboard(scan_df, axis_profiles)
    return show_scan_query_difficulty(svc["store"], svc["backend_id"])
