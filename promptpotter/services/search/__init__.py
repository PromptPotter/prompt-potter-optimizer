"""Search package — smart search and supporting modules.

Re-exports entry-point functions so callers use one import path::

    from promptpotter.services.search import sensitivity_scan, adaptive_search, ...
"""

from __future__ import annotations

import functools
import json
from pathlib import Path

# coverage
from promptpotter.services.search.coverage import (
    assess_scan_coverage,
    build_data_inventory,
    build_prompt_result_index,
)

# scan_advisor
from promptpotter.services.search.scan_advisor import (
    advise_scan_config,
    build_llm_context,
    build_pipeline_overview,
    build_tunable_params,
    convert_advisory_to_scan_variants,
    preview_advisor_prompt,
)

# scan_results (also absorbs scan_baseline)
from promptpotter.services.search.scan_results import (
    decompose_scan_baseline,
    prepare_scan_brief,
    resume_or_build_diagnostic,
    seed_campaign_from_scan,
    select_scan_winner,
)
from promptpotter.services.search.sensitivity_scanner import sensitivity_scan

# smart_search
from promptpotter.services.search.smart_search import (
    adaptive_search,
    build_diagnostic_set,
    load_filtered_variant_library,
)

# ---------------------------------------------------------------------------
# Variant library loaders (inlined from variant_library.py)
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def _load_variant_library_raw() -> dict:
    path = Path(__file__).parents[2] / "config" / "prompt_variants.json"
    with open(path) as f:
        return json.load(f)


def load_variant_library() -> dict:
    """Load the prompt variant library, returning flat ``{field: [str]}`` shape."""
    raw = _load_variant_library_raw()
    return {
        section: {
            field: [v["text"] if isinstance(v, dict) else v for v in vals]
            for field, vals in axes.items()
        }
        for section, axes in raw.items()
    }


def load_variant_library_rich() -> dict:
    """Load the prompt variant library with provenance metadata intact."""
    return _load_variant_library_raw()


__all__ = [
    "adaptive_search",
    "advise_scan_config",
    "assess_scan_coverage",
    "build_data_inventory",
    "build_diagnostic_set",
    "build_llm_context",
    "build_pipeline_overview",
    "build_prompt_result_index",
    "build_tunable_params",
    "convert_advisory_to_scan_variants",
    "decompose_scan_baseline",
    "load_filtered_variant_library",
    "load_variant_library",
    "load_variant_library_rich",
    "prepare_scan_brief",
    "preview_advisor_prompt",
    "resume_or_build_diagnostic",
    "seed_campaign_from_scan",
    "select_scan_winner",
    "sensitivity_scan",
]
