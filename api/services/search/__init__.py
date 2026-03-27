"""Search package — smart search and supporting modules.

Re-exports entry-point functions so callers use one import path::

    from api.services.search import sensitivity_scan, adaptive_search, ...
"""

# smart_search
# context
from api.services.search.context import restructure_context, restructure_context_cached

# coverage
from api.services.search.coverage import (
    assess_scan_coverage,
    build_data_inventory,
    build_prompt_result_index,
    diagnose_scan_variants,
)

# eval_dataset
from api.services.search.eval_dataset import load_eval_dataset

# plan_persistence
from api.services.search.plan_persistence import (
    analyze_candidate_coverage,
    deserialize_smart_search_plan,
    serialize_smart_search_plan,
    smart_search_plan_identity,
)

# NOTE: sensitivity_scan and adaptive_search are NOT re-exported here
# because their function names collide with submodule names. Import them
# from their submodules directly:
#   from api.services.search.sensitivity_scan import sensitivity_scan
#   from api.services.search.adaptive_search import adaptive_search
# preview
from api.services.search.preview import preview

# scan_advisor
from api.services.search.scan_advisor import (
    advise_scan_config,
    build_llm_context,
    build_pipeline_overview,
    build_tunable_params,
    preview_advisor_prompt,
)

# scan_results
from api.services.search.scan_results import (
    prepare_scan_context,
    resume_or_build_diagnostic,
    select_scan_winner,
)
from api.services.search.smart_search import (
    ScanEvent,
    build_diagnostic_set,
    filter_variant_library,
    load_filtered_variant_library,
)

__all__ = [
    "ScanEvent",
    "advise_scan_config",
    "analyze_candidate_coverage",
    "assess_scan_coverage",
    "build_data_inventory",
    "build_diagnostic_set",
    "build_llm_context",
    "build_pipeline_overview",
    "build_prompt_result_index",
    "build_tunable_params",
    "deserialize_smart_search_plan",
    "diagnose_scan_variants",
    "filter_variant_library",
    "load_eval_dataset",
    "load_filtered_variant_library",
    "prepare_scan_context",
    "preview",
    "preview_advisor_prompt",
    "restructure_context",
    "restructure_context_cached",
    "resume_or_build_diagnostic",
    "select_scan_winner",
    "serialize_smart_search_plan",
    "smart_search_plan_identity",
]
