"""Search package — grid search, smart search, and supporting modules.

Re-exports entry-point functions so callers use one import path::

    from api.services.search import run_grid_search, sensitivity_scan, ...
"""

# grid_core
from api.services.search.grid_core import (
    DEFAULT_GRID_AXES,
    analyze_grid_results,
    build_combined_state_lookup,
    build_grid_analysis_prompt,
    build_grid_points,
    load_grid_plan_results,
    merge_grid_results,
    resolve_point_evals,
    resume_or_build_grid,
    run_grid_search,
    select_grid_winner,
    validate_grid_config,
)

# smart_search
from api.services.search.smart_search import (
    ScanEvent,
    adaptive_search,
    build_diagnostic_set,
    filter_variant_library,
    resume_or_build_diagnostic,
    select_scan_winner,
    sensitivity_scan,
)

# coverage
from api.services.search.coverage import (
    assess_scan_coverage,
    build_data_inventory,
    build_prompt_result_index,
)

# eval_dataset
from api.services.search.eval_dataset import load_eval_dataset

# context
from api.services.search.context import restructure_context

# scan_advisor
from api.services.search.scan_advisor import advise_scan_config

# synthesis
from api.services.search.synthesis import synthesize_sensitivity_from_grid

__all__ = [
    # grid_core
    "DEFAULT_GRID_AXES",
    "analyze_grid_results",
    "build_combined_state_lookup",
    "build_grid_analysis_prompt",
    "build_grid_points",
    "load_grid_plan_results",
    "merge_grid_results",
    "resolve_point_evals",
    "resume_or_build_grid",
    "run_grid_search",
    "select_grid_winner",
    "validate_grid_config",
    # smart_search
    "ScanEvent",
    "adaptive_search",
    "build_diagnostic_set",
    "filter_variant_library",
    "resume_or_build_diagnostic",
    "select_scan_winner",
    "sensitivity_scan",
    # coverage
    "assess_scan_coverage",
    "build_data_inventory",
    "build_prompt_result_index",
    # eval_dataset
    "load_eval_dataset",
    # context
    "restructure_context",
    # scan_advisor
    "advise_scan_config",
    # synthesis
    "synthesize_sensitivity_from_grid",
]
