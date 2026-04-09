"""Prompt variant library loaders.

Loads the variant library from ``prompt_variants.json`` in both
flat (text-only) and rich (with provenance metadata) formats.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path

__all__ = ["load_variant_library", "load_variant_library_rich"]


@functools.lru_cache(maxsize=1)
def _load_variant_library_raw() -> dict:
    path = Path(__file__).parent / "prompt_variants.json"
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
