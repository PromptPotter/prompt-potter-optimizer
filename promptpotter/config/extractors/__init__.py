"""Backend extractors — per-backend query parsing and ground truth extraction.

Each extractor module self-registers into the dictionaries below at import time.
Core services dispatch through these registries — they never import an extractor directly.
"""

from __future__ import annotations

from collections.abc import Callable

# -- Registries ---------------------------------------------------------------

EXPERIMENT_EXTRACTORS: dict[str, Callable[[dict], tuple[list[dict], list[str]]]] = {}
"""Backend experiment data → ``(queries, index_terms)``.

Keyed by ``pipeline_schema.name.lower()``.
"""

TRACE_GT_RESOLVERS: dict[str, Callable[[dict, str], str | None]] = {}
"""Resolve ground truth for a single query string from experiment data.

Signature: ``(exp_data, query_str) -> ground_truth | None``.
Keyed by ``pipeline_schema.name.lower()``.
"""

_extractors_loaded = False


def ensure_extractors_loaded() -> None:
    """Lazily import all extractor modules so they self-register."""
    global _extractors_loaded
    if _extractors_loaded:
        return
    _extractors_loaded = True
    import promptpotter.config.extractors.termnorm as _  # noqa: F401
