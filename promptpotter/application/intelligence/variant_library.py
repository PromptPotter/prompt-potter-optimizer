"""L1 crossover / recombination pool loader.

Reads the curated, task-agnostic axis pool from
``promptpotter/config/prompt_variants.json``.  Two shapes: flat
(``{field: [str]}``) for consumers that just need text, rich (with
provenance metadata) for UI surfaces.

**This is the crossover pool, not the starting-point store.**  Baseline
prompts for a campaign live in ``datasets/{name}/prompts/*.json`` and
load via ``promptpotter.application.datasets.prompt_store.load_dataset_prompt``
— that is the principal flow at init time.  This file is the material
L1 recombines from during optimization (and the seed corpus for recon
axis variants).  Never read at init as a source of the starting
``JobSearchPoint``.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path

__all__ = ["load_variant_library", "load_variant_library_rich"]


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
