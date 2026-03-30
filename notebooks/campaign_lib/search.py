"""Smart search: re-exports from focused submodules."""

from api.services.search import (
    build_diagnostic_set,
    build_llm_context,
    build_pipeline_overview,
    build_tunable_params,
)
from api.services.search import (
    build_prompt_result_index as build_historical_index,
)

from .display import show_axis_profiles
from .search_advisor import (
    load_task_description,
    preview_advisor_prompt,
    run_scan_advisor,
    scan_advisor,
)
from .search_baseline import (
    prepare_scan_baseline,
)
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

__all__ = [
    "adaptive_search",
    "advisory_to_scan_variants",
    "audit_historical_data",
    # Smart search
    "build_diagnostic_set",
    "build_historical_index",
    "build_llm_context",
    "build_pipeline_overview",
    "build_tunable_params",
    "load_task_description",
    # Notebook-facing wrappers
    "prepare_scan_baseline",
    "preview_advisor_prompt",
    "resolve_scan_variants",
    "resume_or_build_diagnostic",
    "run_scan_advisor",
    "run_sensitivity_scan",
    "scan_advisor",
    "seed_campaign_from_scan",
    "select_scan_winner_notebook",
    "sensitivity_scan",
    "show_axis_profiles",
    "show_data_inventory",
    "show_scan_analytics",
    "show_scan_coverage",
    "show_variant_library",
]
