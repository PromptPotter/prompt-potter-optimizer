"""Helper library for optimization_campaign.ipynb and evaluation.ipynb.

Thin notebook-facing layer that delegates to ``api.services`` for core logic
and adds tqdm progress bars, print statements, and IPython display for
interactive notebook use.
"""

from ._display import *    # noqa: F403
from ._setup import *      # noqa: F403
from ._eval import *       # noqa: F403
from ._search import *     # noqa: F403
from ._campaigns import *  # noqa: F403
from ._optimize import *   # noqa: F403

# Public API -- every name the notebook imports.
__all__ = [  # noqa: F405
    # Constants
    "RESET", "BOLD", "RED", "GREEN", "YELLOW", "BLUE", "MAGENTA", "CYAN",
    # Service init
    "init_services", "setup_llm", "load_variant_library", "decompose_task_context",
    # Backend status & datasets
    "show_backend_status", "show_dataset_summary", "build_all_session_terms",
    "load_or_create_datasets", "load_stored_dataset", "prepare_datasets",
    "prepare_eval_context",
    "SHEET_COLUMN_MAP",
    # Baseline & eval
    "load_baseline_prompt", "run_baseline_eval",
    "analyze_candidate_coverage", "load_eval_dataset",
    "run_coverage_diagnostic",
    # Candidates & suggestions
    "l1_generate",
    # Pipeline config
    "configure_pipeline",
    # Smart search
    "build_diagnostic_set", "sensitivity_scan", "adaptive_search",
    "show_axis_profiles", "resume_or_build_diagnostic", "scan_advisor",
    "advisory_to_scan_variants", "resolve_scan_variants",
    "select_scan_winner_notebook", "build_historical_index", "load_task_description",
    "show_scan_coverage", "show_data_inventory",
    "audit_historical_data", "run_scan_advisor", "seed_campaign_from_scan",
    "prepare_scan_baseline", "preview_advisor_prompt", "show_variant_library",
    "show_scan_analytics", "run_sensitivity_scan",
    # Campaign
    "show_feedback_preflight", "run_feedback_cycle_notebook", "save_campaign_winner",
    "show_progress",
    "list_campaigns", "diff_campaign_config", "show_experiment_dashboard",
    "load_experiment_config", "apply_experiment_overrides", "load_and_apply_experiment",
    # Pipeline snapshot
    "show_pipeline_snapshot",
    # Scan analytics
    "show_scan_leaderboard", "show_scan_query_difficulty",
    # Campaign results display
    "show_campaign_summary", "show_flip_tracking", "show_lineage_chain",
    # Langfuse
    "configure_langfuse", "push_langfuse", "sync_langfuse",
    # Entity profiles
    "show_entity_profiles",
    # Dev
    "dev_reload",
]
