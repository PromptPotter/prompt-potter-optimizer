"""Search package — smart search and supporting modules.

Re-exports entry-point functions so callers use one import path::

    from promptpotter.services.search import sensitivity_scan, adaptive_search, ...
"""

# sensitivity_scanner / adaptive_searcher
from promptpotter.services.search.adaptive_searcher import adaptive_search

# context
from promptpotter.services.search.context import (
    decompose_prompt_fields,
    decompose_prompt_fields_cached,
)

# coverage
from promptpotter.services.search.coverage import (
    assess_scan_coverage,
    build_data_inventory,
    build_prompt_result_index,
    diagnose_scan_variants,
)

# eval_dataset
from promptpotter.services.search.eval_dataset import load_eval_dataset

# plan_persistence
from promptpotter.services.search.plan_persistence import (
    deserialize_smart_search_plan,
    serialize_smart_search_plan,
    smart_search_plan_identity,
)

# scan_advisor
from promptpotter.services.search.scan_advisor import (
    advise_scan_config,
    advisory_to_scan_variants,
    build_llm_context,
    build_pipeline_overview,
    build_tunable_params,
    preview_advisor_prompt,
)

# scan_baseline
from promptpotter.services.search.scan_baseline import prepare_scan_baseline

# scan_results
from promptpotter.services.search.scan_results import (
    prepare_scan_context,
    resume_or_build_diagnostic,
    seed_campaign_from_scan,
    select_scan_winner,
)
from promptpotter.services.search.sensitivity_scanner import sensitivity_scan

# smart_search
from promptpotter.services.search.smart_search import (
    ScanEvent,
    build_diagnostic_set,
    filter_variant_library,
    load_filtered_variant_library,
)

__all__ = [
    "ScanEvent",
    "adaptive_search",
    "advise_scan_config",
    "advisory_to_scan_variants",
    "assess_scan_coverage",
    "build_data_inventory",
    "build_diagnostic_set",
    "build_llm_context",
    "build_pipeline_overview",
    "build_prompt_result_index",
    "build_tunable_params",
    "decompose_prompt_fields",
    "decompose_prompt_fields_cached",
    "deserialize_smart_search_plan",
    "diagnose_scan_variants",
    "filter_variant_library",
    "load_eval_dataset",
    "load_filtered_variant_library",
    "prepare_scan_baseline",
    "prepare_scan_context",
    "preview_advisor_prompt",
    "resume_or_build_diagnostic",
    "seed_campaign_from_scan",
    "select_scan_winner",
    "sensitivity_scan",
    "serialize_smart_search_plan",
    "smart_search_plan_identity",
]
