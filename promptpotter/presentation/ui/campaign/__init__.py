"""Display layer for optimization campaigns.

Thin notebook/terminal-facing layer that delegates to ``promptpotter.services``
for core logic and adds tqdm progress bars, print statements, and IPython
display for interactive use.
"""

import types as _types

# -- Reporting (supplemental materials) — direct from services ----------------
from promptpotter.application.campaign.reporting import generate_export_json
from promptpotter.presentation.views.display_primitives import (
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
from promptpotter.presentation.views.formatting import generate_supplemental

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
from .notebook_analytics import (
    show_campaign_summary,
    show_flip_tracking,
    show_lineage_chain,
    show_progress,
)

# -- Optimization (feedback cycle, stats, scoring) --------------------------------
from .optimize import (
    fmt_ci,
    fmt_pvalue,
    load_baseline_prompt,
    min_detectable_effect,
    proportion_test,
    run_optimization_notebook,
    show_feedback_preflight,
    wilson_ci,
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
