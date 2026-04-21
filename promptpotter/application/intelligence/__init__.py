"""Shared materialized view over historical search data.

The ``intelligence`` package is the common ground between the optional human
sensitivity scan and the optimization loop. It owns SearchMemory (digest
interaction layer), SampleIndex (per-sample index storage), the variant
library loader, and scoring-set adaptation — all shared primitives that
both loops consume.

Directionality: this package must NOT import from ``scan`` or ``optimization``.
"""

from __future__ import annotations

from promptpotter.application.intelligence.sample_index import SampleIndex
from promptpotter.application.intelligence.scoring_set_adaptation import adapt_scoring_set
from promptpotter.application.intelligence.search_memory import SearchMemory
from promptpotter.application.intelligence.variant_library import (
    load_variant_library,
    load_variant_library_rich,
)

__all__ = [
    "SampleIndex",
    "SearchMemory",
    "adapt_scoring_set",
    "load_variant_library",
    "load_variant_library_rich",
]
