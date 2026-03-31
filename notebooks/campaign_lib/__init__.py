"""Helper library for optimization_campaign.ipynb.

Thin notebook-facing layer that delegates to ``api.services`` for core logic
and adds tqdm progress bars, print statements, and IPython display for
interactive notebook use.
"""

# -- Setup (init, pipeline, LLM, langfuse, datasets) -------------------------

from .setup import (
    build_all_session_terms,
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

# -- Campaigns (list, load, diff, overrides) ----------------------------------

from .campaigns import (
    apply_experiment_overrides,
    diff_campaign_config,
    list_campaigns,
    load_and_apply_experiment,
    load_experiment_config,
    show_experiment_dashboard,
)

# -- Search: Scan (sensitivity, adaptive, baseline) ---------------------------

from .search_baseline import prepare_scan_baseline
from .search_scan import (
    adaptive_search,
    run_sensitivity_scan,
    sensitivity_scan,
)

# -- Search: Advisor ----------------------------------------------------------

from .search_advisor import (
    load_task_description,
    preview_advisor_prompt,
    run_scan_advisor,
    scan_advisor,
)

# -- Search: Results & Coverage -----------------------------------------------

from .search_coverage import (
    audit_historical_data,
    show_data_inventory,
    show_scan_coverage,
)
from .search_results import (
    resume_or_build_diagnostic,
    seed_campaign_from_scan,
    select_scan_winner_notebook,
    show_scan_analytics,
)

# -- Search: Variants ---------------------------------------------------------

from .search_variants import (
    advisory_to_scan_variants,
    resolve_scan_variants,
    show_variant_library,
)

# -- Optimization (feedback cycle) --------------------------------------------

from .optimize import (
    run_optimization_notebook,
    show_feedback_preflight,
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
    show_axis_profiles,
    show_campaign_summary,
    show_flip_tracking,
    show_lineage_chain,
    show_progress,
    show_scan_leaderboard,
    show_scan_query_difficulty,
)

# -- Re-exports from api.services.search (used directly in notebook) ----------

from api.services.search import (
    build_diagnostic_set,
    build_llm_context,
    build_pipeline_overview,
    build_tunable_params,
)
from api.services.search import (
    build_prompt_result_index as build_historical_index,
)

__all__ = [
    # Setup
    "build_all_session_terms",
    "configure_langfuse",
    "configure_pipeline",
    "decompose_task_context",
    "dev_reload",
    "init_services",
    "load_variant_library",
    "prepare_datasets",
    "prepare_eval_context",
    "push_langfuse",
    "save_campaign_winner",
    "setup_llm",
    "show_backend_status",
    "show_pipeline_snapshot",
    "sync_langfuse",
    # Campaigns
    "apply_experiment_overrides",
    "diff_campaign_config",
    "list_campaigns",
    "load_and_apply_experiment",
    "load_experiment_config",
    "show_experiment_dashboard",
    # Search: Scan
    "adaptive_search",
    "prepare_scan_baseline",
    "run_sensitivity_scan",
    "sensitivity_scan",
    # Search: Advisor
    "load_task_description",
    "preview_advisor_prompt",
    "run_scan_advisor",
    "scan_advisor",
    # Search: Results & Coverage
    "audit_historical_data",
    "resume_or_build_diagnostic",
    "seed_campaign_from_scan",
    "select_scan_winner_notebook",
    "show_data_inventory",
    "show_scan_analytics",
    "show_scan_coverage",
    # Search: Variants
    "advisory_to_scan_variants",
    "resolve_scan_variants",
    "show_variant_library",
    # Optimization
    "run_optimization_notebook",
    "show_feedback_preflight",
    # Display
    "BLUE",
    "BOLD",
    "CYAN",
    "GREEN",
    "MAGENTA",
    "RED",
    "RESET",
    "YELLOW",
    "show_axis_profiles",
    "show_campaign_summary",
    "show_flip_tracking",
    "show_lineage_chain",
    "show_progress",
    "show_scan_leaderboard",
    "show_scan_query_difficulty",
    # Re-exports from api.services.search
    "build_diagnostic_set",
    "build_historical_index",
    "build_llm_context",
    "build_pipeline_overview",
    "build_tunable_params",
]
