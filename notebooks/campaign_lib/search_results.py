"""Downstream result wrappers: winner selection, campaign seeding, analytics."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from promptpotter.services.search import (
    load_filtered_variant_library as _load_filtered_variants,
)
from promptpotter.services.search import (
    resume_or_build_diagnostic as _resume_or_build_diagnostic,
)
from promptpotter.services.search import (
    select_scan_winner as _select_scan_winner,
)
from promptpotter.services.search.scan_results import (
    seed_campaign_from_scan as _seed_campaign_from_scan,
)

from .display import (
    format_pipeline_overrides,
    show_progress,
    show_scan_leaderboard,
    show_scan_query_difficulty,
)
from .setup import setup_llm

if TYPE_CHECKING:
    from promptpotter.services.campaign.config import CampaignConfig
    from promptpotter.services.campaign.init import BackendSession

logger = logging.getLogger(__name__)

__all__ = [
    "resume_or_build_diagnostic",
    "seed_campaign_from_scan",
    "select_scan_winner_notebook",
    "show_scan_analytics",
]


async def resume_or_build_diagnostic(
    campaign_config: CampaignConfig,
    baseline,
    baseline_results: list,
    session: BackendSession,
    dataset: list,
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
    variant_library = _load_filtered_variants(pipeline_params, session.pipeline_schema)
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
        session.store,
        session.backend_id,
        dataset,
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
    campaign_config: CampaignConfig,
    pipeline_schema=None,
):
    """Select scan winner, update pipeline_params, seed campaign_rounds.

    Delegates to ``promptpotter.services.search.scan_results.seed_campaign_from_scan()``
    and prints summary + progress.
    """
    if scan_df is None or (hasattr(scan_df, "empty") and scan_df.empty):
        print("No scan data available. Run sensitivity scan first.")
        return baseline

    result = _seed_campaign_from_scan(
        scan_df, axis_profiles, baseline, scan_variants,
        campaign_rounds, campaign_config,
    )

    # Print improving axes summary
    if result.improving_axes:
        print(f"Selected best from {len(result.improving_axes)} improving axes:")
        for p in result.improving_axes:
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

    # Print pipeline_params update
    if result.merged_pipeline_params:
        display_pp = {}
        for k, v in result.merged_pipeline_params.items():
            if isinstance(v, dict) and len(str(v)) > 100:
                n_keys = len(v.get("properties", v))
                display_pp[k] = f"<{n_keys} fields>"
            else:
                display_pp[k] = v
        print(f"Updated pipeline_params: {display_pp}")
        format_pipeline_overrides(result.merged_pipeline_params, pipeline_schema)

    if result.best_sp.sp_hash() != baseline.sp_hash():
        print(f"\nComposed winner: sp_hash={result.best_sp.sp_hash()[:12]}")

    show_progress(campaign_rounds)

    return result.best_sp


def show_scan_analytics(scan_df, axis_profiles, session: BackendSession):
    """Display scan leaderboard and query difficulty if results are available.

    Returns difficulty_df (or None if scan_df is empty/None).
    """
    if scan_df is None or scan_df.empty:
        return None
    show_scan_leaderboard(scan_df, axis_profiles)
    return show_scan_query_difficulty(session.store, session.backend_id)
