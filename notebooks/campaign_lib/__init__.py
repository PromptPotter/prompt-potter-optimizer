"""Helper library for optimization_campaign.ipynb.

Thin notebook-facing layer that delegates to ``promptpotter.services`` for core logic
and adds tqdm progress bars, print statements, and IPython display for
interactive notebook use.
"""

# -- Setup (init, pipeline, LLM, langfuse, datasets) -------------------------

# -- Re-exports from promptpotter.services.search (used directly in notebook) ----------
from promptpotter.services.search import (
    build_diagnostic_set,
    build_llm_context,
    build_pipeline_overview,
    build_tunable_params,
)
from promptpotter.services.search import (
    build_prompt_result_index as build_historical_index,
)

# -- Campaigns (list, load, diff, overrides) ----------------------------------
from .campaigns import (
    apply_experiment_overrides,
    diff_campaign_config,
    list_campaigns,
    load_and_apply_experiment,
    load_experiment_config,
    show_experiment_dashboard,
)

# -- Display ------------------------------------------------------------------
from .display import (
    BLUE,
    BOLD,
    CYAN,
    GREEN,
    MAGENTA,
    RED,
    RESET,
    YELLOW,
    set_display_tags,
    show_axis_profiles,
    show_campaign_summary,
    show_flip_tracking,
    show_lineage_chain,
    show_progress,
    show_scan_leaderboard,
    show_scan_query_difficulty,
)

# -- Optimization (feedback cycle, stats, eval) --------------------------------
from .optimize import (
    fmt_ci,
    fmt_pvalue,
    load_baseline_prompt,
    min_detectable_effect,
    proportion_test,
    run_baseline_eval,
    run_optimization_notebook,
    show_feedback_preflight,
    wilson_ci,
)

# -- Reporting (supplemental materials) ---------------------------------------
from .reporting import generate_export_json, generate_supplemental

# -- Search: Advisor, Baseline, Coverage, Results, Variants -------------------
from .search import (
    advisory_to_scan_variants,
    audit_historical_data,
    load_task_description,
    decompose_scan_baseline,
    preview_advisor_prompt,
    resolve_scan_variants,
    resume_or_build_diagnostic,
    run_scan_advisor,
    scan_advisor,
    seed_campaign_from_scan,
    select_scan_winner_notebook,
    show_data_inventory,
    show_scan_analytics,
    show_scan_coverage,
    show_variant_library,
)
from .search_scan import (
    adaptive_search,
    run_sensitivity_scan,
    sensitivity_scan,
)
from .setup import (
    build_all_index_terms,
    configure_langfuse,
    configure_pipeline,
    decompose_task_context,
    dev_reload,
    init_services,
    load_variant_library,
    prepare_datasets,
    prepare_eval_context,
    push_langfuse,
    save_campaign_winner,
    setup_llm,
    show_backend_status,
    show_pipeline_snapshot,
    sync_langfuse,
)

__all__ = [
    # Display
    "BLUE",
    "BOLD",
    "CYAN",
    "GREEN",
    "MAGENTA",
    "RED",
    "RESET",
    "YELLOW",
    # Search: Scan
    "adaptive_search",
    # Search: Variants
    "advisory_to_scan_variants",
    # Campaigns
    "apply_experiment_overrides",
    # Search: Results & Coverage
    "audit_historical_data",
    # Setup
    "build_all_index_terms",
    # Re-exports from promptpotter.services.search
    "build_diagnostic_set",
    "build_historical_index",
    "build_llm_context",
    "build_pipeline_overview",
    "build_tunable_params",
    "configure_langfuse",
    "configure_pipeline",
    "decompose_task_context",
    "dev_reload",
    "diff_campaign_config",
    # Optimization (+ stats + eval)
    "fmt_ci",
    "fmt_pvalue",
    # Reporting
    "generate_export_json",
    "generate_supplemental",
    "init_services",
    "list_campaigns",
    "load_and_apply_experiment",
    "load_baseline_prompt",
    "load_experiment_config",
    # Search: Advisor
    "load_task_description",
    "load_variant_library",
    "min_detectable_effect",
    "prepare_datasets",
    "prepare_eval_context",
    "decompose_scan_baseline",
    "preview_advisor_prompt",
    "proportion_test",
    "push_langfuse",
    "resolve_scan_variants",
    "resume_or_build_diagnostic",
    "run_baseline_eval",
    "run_optimization_notebook",
    "run_scan_advisor",
    "run_sensitivity_scan",
    "save_campaign_winner",
    "scan_advisor",
    "seed_campaign_from_scan",
    "select_scan_winner_notebook",
    "sensitivity_scan",
    "set_display_tags",
    "setup_llm",
    "show_axis_profiles",
    "show_backend_status",
    "show_campaign_summary",
    "show_data_inventory",
    "show_experiment_dashboard",
    "show_feedback_preflight",
    "show_flip_tracking",
    "show_lineage_chain",
    "show_pipeline_snapshot",
    "show_progress",
    "show_scan_analytics",
    "show_scan_coverage",
    "show_scan_leaderboard",
    "show_scan_query_difficulty",
    "show_variant_library",
    "sync_langfuse",
    "wilson_ci",
]
