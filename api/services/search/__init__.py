"""Search package — grid search, smart search, and supporting modules.

Re-exports every public name so callers use one import path::

    from api.services.search import run_grid_search, sensitivity_scan, ...
"""

# grid_core
from api.services.search.grid_core import (
    DEFAULT_GRID_AXES,
    GRID_PREFIX,
    GRID_SEARCHABLE_FIELDS,
    PointEvalInfo,
    REQUIRED_TEMPLATE_VARS,
    SAMPLING_ALPHA,
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
    DEFAULT_DIAGNOSTIC_QUERIES,
    DIAGNOSTIC_HIT_RATIO,
    MIN_DIAGNOSTIC_QUERIES,
    adaptive_search,
    build_diagnostic_set,
    classify_axis,
    resume_or_build_diagnostic,
    sensitivity_scan,
)

# plan_persistence
from api.services.search.plan_persistence import (
    GRIDPLAN_PREFIX,
    SSPLAN_PREFIX,
    deserialize_grid_plan,
    deserialize_smart_search_plan,
    grid_plan_identity,
    serialize_grid_plan,
    serialize_smart_search_plan,
    smart_search_plan_identity,
)

# coverage
from api.services.search.coverage import (
    assess_scan_coverage,
    build_data_inventory,
    build_prompt_result_index,
)

# eval_dataset
from api.services.search.eval_dataset import (
    load_eval_dataset,
)

# context
from api.services.search.context import (
    restructure_context,
)

# synthesis
from api.services.search.synthesis import (
    synthesize_sensitivity_from_grid,
)

__all__ = [
    # grid_core
    "DEFAULT_GRID_AXES",
    "GRID_PREFIX",
    "GRID_SEARCHABLE_FIELDS",
    "PointEvalInfo",
    "REQUIRED_TEMPLATE_VARS",
    "SAMPLING_ALPHA",
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
    "DEFAULT_DIAGNOSTIC_QUERIES",
    "DIAGNOSTIC_HIT_RATIO",
    "MIN_DIAGNOSTIC_QUERIES",
    "adaptive_search",
    "build_diagnostic_set",
    "classify_axis",
    "resume_or_build_diagnostic",
    "sensitivity_scan",
    # plan_persistence
    "GRIDPLAN_PREFIX",
    "SSPLAN_PREFIX",
    "deserialize_grid_plan",
    "deserialize_smart_search_plan",
    "grid_plan_identity",
    "serialize_grid_plan",
    "serialize_smart_search_plan",
    "smart_search_plan_identity",
    # coverage
    "assess_scan_coverage",
    "build_data_inventory",
    "build_prompt_result_index",
    # eval_dataset
    "load_eval_dataset",
    # context
    "restructure_context",
    # synthesis
    "synthesize_sensitivity_from_grid",
]
