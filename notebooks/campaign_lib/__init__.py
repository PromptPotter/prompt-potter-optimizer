"""Helper library for optimization_campaign.ipynb.

Thin notebook-facing layer that delegates to ``api.services`` for core logic
and adds tqdm progress bars, print statements, and IPython display for
interactive notebook use.
"""

from .display import (
    RESET, BOLD, RED, GREEN, YELLOW, BLUE, MAGENTA, CYAN,
    show_progress, show_axis_profiles,
    show_scan_leaderboard, show_scan_query_difficulty,
    show_campaign_summary, show_flip_tracking, show_lineage_chain,
)
from .setup import (
    init_services, setup_llm, load_variant_library, decompose_task_context,
    show_backend_status, build_all_session_terms,
    prepare_datasets, prepare_eval_context,
    configure_pipeline, show_pipeline_snapshot,
    save_campaign_winner,
    configure_langfuse, sync_langfuse, push_langfuse,
    dev_reload,
)
from .search import (
    build_diagnostic_set, sensitivity_scan, adaptive_search,
    resume_or_build_diagnostic, scan_advisor,
    advisory_to_scan_variants, resolve_scan_variants,
    select_scan_winner_notebook, build_historical_index, load_task_description,
    show_scan_coverage, show_data_inventory,
    audit_historical_data, preview_advisor_prompt,
    prepare_scan_baseline, run_scan_advisor, seed_campaign_from_scan,
    build_pipeline_overview, build_tunable_params, build_llm_context,
    show_variant_library, show_scan_analytics, run_sensitivity_scan,
)
from .campaigns import (
    list_campaigns, diff_campaign_config,
    load_experiment_config, show_experiment_dashboard,
    apply_experiment_overrides, load_and_apply_experiment,
)
from .optimize import (
    show_feedback_preflight, run_optimization_notebook,
)

__all__ = [
    # Constants (display)
    "RESET", "BOLD", "RED", "GREEN", "YELLOW", "BLUE", "MAGENTA", "CYAN",
    # Service init (setup)
    "init_services", "setup_llm", "load_variant_library", "decompose_task_context",
    # Backend status & datasets (setup)
    "show_backend_status", "build_all_session_terms",
    "prepare_datasets", "prepare_eval_context",
    # Pipeline config (setup)
    "configure_pipeline", "show_pipeline_snapshot",
    "save_campaign_winner",
    # Langfuse (setup)
    "configure_langfuse", "push_langfuse", "sync_langfuse",
    # Dev (setup)
    "dev_reload",
    # Smart search (search)
    "build_diagnostic_set", "sensitivity_scan", "adaptive_search",
    "show_axis_profiles", "resume_or_build_diagnostic", "scan_advisor",
    "advisory_to_scan_variants", "resolve_scan_variants",
    "select_scan_winner_notebook", "build_historical_index", "load_task_description",
    "show_scan_coverage", "show_data_inventory",
    "audit_historical_data", "preview_advisor_prompt",
    "prepare_scan_baseline", "run_scan_advisor", "seed_campaign_from_scan",
    "build_pipeline_overview", "build_tunable_params", "build_llm_context",
    "show_variant_library", "show_scan_analytics", "run_sensitivity_scan",
    # Display
    "show_progress",
    "show_scan_leaderboard", "show_scan_query_difficulty",
    "show_campaign_summary", "show_flip_tracking", "show_lineage_chain",
    # Campaign management (campaigns)
    "list_campaigns", "diff_campaign_config", "show_experiment_dashboard",
    "load_experiment_config", "apply_experiment_overrides", "load_and_apply_experiment",
    # Feedback cycle (optimize)
    "show_feedback_preflight", "run_optimization_notebook",
]
