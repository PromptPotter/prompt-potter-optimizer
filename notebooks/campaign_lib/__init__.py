"""Helper library for optimization_campaign.ipynb.

Thin notebook-facing layer that delegates to ``api.services`` for core logic
and adds tqdm progress bars, print statements, and IPython display for
interactive notebook use.
"""

from api.services.search import (
    build_diagnostic_set,
    build_llm_context,
    build_pipeline_overview,
    build_tunable_params,
)
from api.services.search import (
    build_prompt_result_index as build_historical_index,
)

from .campaigns import (
    apply_experiment_overrides,
    diff_campaign_config,
    list_campaigns,
    load_and_apply_experiment,
    load_experiment_config,
    show_experiment_dashboard,
)
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
from .optimize import (
    run_optimization_notebook,
    show_feedback_preflight,
)
from .search_advisor import (
    load_task_description,
    preview_advisor_prompt,
    run_scan_advisor,
    scan_advisor,
)
from .search_baseline import prepare_scan_baseline
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
from .search_scan import (
    adaptive_search,
    run_sensitivity_scan,
    sensitivity_scan,
)
from .search_variants import (
    advisory_to_scan_variants,
    resolve_scan_variants,
    show_variant_library,
)
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

__all__ = [
    "BLUE",
    "BOLD",
    "CYAN",
    "GREEN",
    "MAGENTA",
    "RED",
    # Constants (display)
    "RESET",
    "YELLOW",
    "adaptive_search",
    "advisory_to_scan_variants",
    "apply_experiment_overrides",
    "audit_historical_data",
    "build_all_session_terms",
    # Smart search (search)
    "build_diagnostic_set",
    "build_historical_index",
    "build_llm_context",
    "build_pipeline_overview",
    "build_tunable_params",
    # Langfuse (setup)
    "configure_langfuse",
    # Pipeline config (setup)
    "configure_pipeline",
    "decompose_task_context",
    # Dev (setup)
    "dev_reload",
    "diff_campaign_config",
    # Service init (setup)
    "init_services",
    # Campaign management (campaigns)
    "list_campaigns",
    "load_and_apply_experiment",
    "load_experiment_config",
    "load_task_description",
    "load_variant_library",
    "prepare_datasets",
    "prepare_eval_context",
    "prepare_scan_baseline",
    "preview_advisor_prompt",
    "push_langfuse",
    "resolve_scan_variants",
    "resume_or_build_diagnostic",
    "run_optimization_notebook",
    "run_scan_advisor",
    "run_sensitivity_scan",
    "save_campaign_winner",
    "scan_advisor",
    "seed_campaign_from_scan",
    "select_scan_winner_notebook",
    "sensitivity_scan",
    "setup_llm",
    "show_axis_profiles",
    # Backend status & datasets (setup)
    "show_backend_status",
    "show_campaign_summary",
    "show_data_inventory",
    "show_experiment_dashboard",
    # Feedback cycle (optimize)
    "show_feedback_preflight",
    "show_flip_tracking",
    "show_lineage_chain",
    "show_pipeline_snapshot",
    # Display
    "show_progress",
    "show_scan_analytics",
    "show_scan_coverage",
    "show_scan_leaderboard",
    "show_scan_query_difficulty",
    "show_variant_library",
    "sync_langfuse",
]
