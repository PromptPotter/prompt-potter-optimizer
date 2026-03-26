"""Smart search: re-exports from focused submodules."""

from api.services.search import (  # noqa: F401
    build_diagnostic_set,
    build_prompt_result_index as build_historical_index,
    build_pipeline_overview,
    build_tunable_params,
    build_llm_context,
)
from .display import show_axis_profiles  # noqa: F401

from .search_variants import (  # noqa: F401
    show_variant_library,
    advisory_to_scan_variants,
    resolve_scan_variants,
)
from .search_baseline import (  # noqa: F401
    prepare_scan_baseline,
)
from .search_coverage import (  # noqa: F401
    show_scan_coverage,
    show_data_inventory,
    audit_historical_data,
)
from .search_advisor import (  # noqa: F401
    preview_advisor_prompt,
    load_task_description,
    scan_advisor,
    run_scan_advisor,
)
from .search_scan import (  # noqa: F401
    sensitivity_scan,
    adaptive_search,
    run_sensitivity_scan,
)
from .search_results import (  # noqa: F401
    resume_or_build_diagnostic,
    select_scan_winner_notebook,
    seed_campaign_from_scan,
    show_scan_analytics,
)

__all__ = [
    # Smart search
    "build_diagnostic_set", "sensitivity_scan", "adaptive_search",
    "show_axis_profiles", "resume_or_build_diagnostic", "scan_advisor",
    "advisory_to_scan_variants", "resolve_scan_variants",
    "select_scan_winner_notebook", "build_historical_index", "load_task_description",
    "show_scan_coverage", "show_data_inventory",
    "audit_historical_data", "preview_advisor_prompt",
    # Notebook-facing wrappers
    "prepare_scan_baseline", "run_scan_advisor", "seed_campaign_from_scan",
    "build_pipeline_overview", "build_tunable_params", "build_llm_context",
    "show_variant_library", "show_scan_analytics", "run_sensitivity_scan",
]
