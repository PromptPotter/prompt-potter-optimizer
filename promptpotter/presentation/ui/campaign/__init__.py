"""Display layer for optimization campaigns.

Thin notebook/terminal-facing layer that delegates to ``promptpotter.services``
for core logic and adds tqdm progress bars, print statements, and IPython
display for interactive use.
"""

import types as _types

# -- Setup (init, pipeline, LLM, langfuse, datasets) -------------------------
# -- Re-exports from promptpotter.application.recon (used directly in notebook) ----------
# -- Reporting (supplemental materials) — direct from services ----------------
from promptpotter.application.campaign.utils import generate_export_json, generate_supplemental
from promptpotter.application.recon import (
    build_diagnostic_set,
    build_llm_context,
    build_pipeline_overview,
    build_tunable_params,
)

# -- Campaigns (list, load, diff, overrides) ----------------------------------
from .campaigns import (
    apply_stored_overrides,
    diff_campaign_config,
    list_campaigns,
    load_and_apply_experiment,
    load_stored_campaign_config,
    show_experiment_dashboard,
)

# -- Display ------------------------------------------------------------------
from .notebook_phase import (
    show_axis_profiles,
    show_campaign_summary,
    show_flip_tracking,
    show_lineage_chain,
    show_progress,
    show_recon_leaderboard,
    show_recon_query_difficulty,
)
from .notebook_primitives import (
    BLUE,
    BOLD,
    CYAN,
    GREEN,
    MAGENTA,
    RED,
    RESET,
    YELLOW,
    set_display_tags,
)

# -- Optimization (feedback cycle, stats, scoring) --------------------------------
from .optimize import (
    fmt_ci,
    fmt_pvalue,
    load_baseline_prompt,
    min_detectable_effect,
    proportion_test,
    run_baseline_scoring,
    run_optimization_notebook,
    show_feedback_preflight,
    wilson_ci,
)

# -- Search: Advisor, Baseline, Results, Variants -----------------------------
from .search import (
    convert_advisory_to_recon_variants,
    decompose_recon_baseline,
    load_task_description,
    preview_advisor_prompt,
    recon_advisor,
    resolve_recon_variants,
    resume_or_build_diagnostic,
    run_recon_advisor,
    seed_campaign_from_recon,
    select_recon_winner_notebook,
    show_recon_analytics,
    show_variant_library,
)
from .search_recon import (
    run_adaptive_recon,
    run_recon,
    run_sensitivity_recon,
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
    prepare_scoring_context,
    push_langfuse,
    save_campaign_winner,
    setup_llm,
    show_backend_status,
    show_pipeline_snapshot,
    sync_langfuse,
)

# __all__ derived from the explicit imports above — no manual list to maintain.
# Filter out submodule names (types.ModuleType) to export only functions/constants.
__all__ = [
    name
    for name in dir()
    if not name.startswith("_") and not isinstance(globals()[name], _types.ModuleType)
]
