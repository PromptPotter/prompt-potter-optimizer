"""Connector configs — per-backend/dataset query parsing, ground truth extraction, and defaults.

Each connector module self-registers into the dictionaries below at import time.
Core services dispatch through these registries — they never import a connector directly.
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

_connectors_loaded = False


def ensure_connectors_loaded() -> None:
    """Lazily import all connector modules so they self-register."""
    global _connectors_loaded
    if _connectors_loaded:
        return
    _connectors_loaded = True
    import promptpotter.config.connectors.termnorm as _  # noqa: F401
