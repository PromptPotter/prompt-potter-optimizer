"""Search package — smart search and supporting modules.

Re-exports entry-point functions so callers use one import path::

    from api.services.search import sensitivity_scan, adaptive_search, ...
"""

# smart_search
from api.services.search.smart_search import (
    ScanEvent, build_diagnostic_set, filter_variant_library,
    load_filtered_variant_library,
)
# scan_results
from api.services.search.scan_results import (
    prepare_scan_context, resume_or_build_diagnostic, select_scan_winner,
)
# NOTE: sensitivity_scan and adaptive_search are NOT re-exported here
# because their function names collide with submodule names. Import them
# from their submodules directly:
#   from api.services.search.sensitivity_scan import sensitivity_scan
#   from api.services.search.adaptive_search import adaptive_search

# coverage
from api.services.search.coverage import (
    assess_scan_coverage, build_data_inventory, build_prompt_result_index,
    diagnose_scan_variants, preview,
)
# eval_dataset
from api.services.search.eval_dataset import load_eval_dataset
# context
from api.services.search.context import restructure_context, restructure_context_cached
# scan_advisor
from api.services.search.scan_advisor import (
    advise_scan_config, build_llm_context, build_pipeline_overview,
    build_tunable_params, preview_advisor_prompt,
)

__all__ = [
    "ScanEvent", "build_diagnostic_set",
    "filter_variant_library", "load_filtered_variant_library",
    "resume_or_build_diagnostic", "select_scan_winner",
    "assess_scan_coverage", "build_data_inventory", "build_prompt_result_index",
    "diagnose_scan_variants", "preview",
    "load_eval_dataset",
    "restructure_context", "restructure_context_cached",
    "advise_scan_config", "build_llm_context", "build_pipeline_overview",
    "build_tunable_params", "preview_advisor_prompt",
    "prepare_scan_context",
]
