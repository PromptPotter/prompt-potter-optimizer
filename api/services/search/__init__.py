"""Search package — grid search, smart search, and supporting modules.

Re-exports entry-point functions so callers use one import path::

    from api.services.search import run_grid_search, sensitivity_scan, ...

Submodules are imported lazily to avoid pulling in heavy dependencies
(e.g. pandas) at package-import time.
"""

_SUBMODULE_MAP: dict[str, str] = {
    # grid_core
    "analyze_grid_results": "api.services.search.grid_core",
    "build_combined_state_lookup": "api.services.search.grid_core",
    "build_grid_analysis_prompt": "api.services.search.grid_core",
    "build_grid_points": "api.services.search.grid_core",
    "load_grid_plan_results": "api.services.search.grid_core",
    "merge_grid_results": "api.services.search.grid_core",
    "resolve_point_evals": "api.services.search.grid_core",
    "resume_or_build_grid": "api.services.search.grid_core",
    "run_grid_search": "api.services.search.grid_core",
    "select_grid_winner": "api.services.search.grid_core",
    "validate_grid_config": "api.services.search.grid_core",
    # smart_search
    "ScanEvent": "api.services.search.smart_search",
    "adaptive_search": "api.services.search.smart_search",
    "build_diagnostic_set": "api.services.search.smart_search",
    "filter_variant_library": "api.services.search.smart_search",
    "load_scan_results_from_plan": "api.services.search.smart_search",
    "resume_or_build_diagnostic": "api.services.search.smart_search",
    "select_scan_winner": "api.services.search.smart_search",
    "sensitivity_scan": "api.services.search.smart_search",
    # coverage
    "assess_scan_coverage": "api.services.search.coverage",
    "build_data_inventory": "api.services.search.coverage",
    "build_prompt_result_index": "api.services.search.coverage",
    # eval_dataset
    "load_eval_dataset": "api.services.search.eval_dataset",
    # context
    "restructure_context": "api.services.search.context",
    # scan_advisor
    "advise_scan_config": "api.services.search.scan_advisor",
    # synthesis
    "synthesize_sensitivity_from_grid": "api.services.search.synthesis",
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
