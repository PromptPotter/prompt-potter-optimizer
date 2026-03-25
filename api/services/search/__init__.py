"""Search package — smart search and supporting modules.

Re-exports entry-point functions so callers use one import path::

    from api.services.search import sensitivity_scan, adaptive_search, ...

Submodules are imported lazily to avoid pulling in heavy dependencies
(e.g. pandas) at package-import time.
"""

_SUBMODULE_MAP: dict[str, str] = {
    # smart_search
    "ScanEvent": "api.services.search.smart_search",
    "adaptive_search": "api.services.search.smart_search",
    "build_diagnostic_set": "api.services.search.smart_search",
    "filter_variant_library": "api.services.search.smart_search",
    "load_filtered_variant_library": "api.services.search.smart_search",
    "resume_or_build_diagnostic": "api.services.search.scan_results",
    "select_scan_winner": "api.services.search.scan_results",
    "sensitivity_scan": "api.services.search.smart_search",
    # coverage
    "assess_scan_coverage": "api.services.search.coverage",
    "build_data_inventory": "api.services.search.coverage",
    "build_prompt_result_index": "api.services.search.coverage",
    "diagnose_scan_variants": "api.services.search.coverage",
    # eval_dataset
    "load_eval_dataset": "api.services.search.eval_dataset",
    # context
    "restructure_context": "api.services.search.context",
    "restructure_context_cached": "api.services.search.context",
    # scan_advisor
    "advise_scan_config": "api.services.search.scan_advisor",
    "build_llm_context": "api.services.search.scan_advisor",
    "build_pipeline_overview": "api.services.search.scan_advisor",
    "build_tunable_params": "api.services.search.scan_advisor",
    "preview_advisor_prompt": "api.services.search.scan_advisor",
    # scan_seeding
    "prepare_scan_context": "api.services.search.scan_results",
}

__all__ = list(_SUBMODULE_MAP)


def __getattr__(name: str):
    if name in _SUBMODULE_MAP:
        import importlib
        mod = importlib.import_module(_SUBMODULE_MAP[name])
        attr = getattr(mod, name)
        # Cache on the module so __getattr__ isn't called again
        globals()[name] = attr
        return attr
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
