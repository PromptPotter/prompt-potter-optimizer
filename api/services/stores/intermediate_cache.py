"""IntermediateCache — per-node output cache for partial pipeline reuse (Wave 4).

Caches upstream node outputs keyed by (upstream_config_hash, query) so that
when only the ranker config changes, upstream evaluation can be skipped.

Gracefully no-ops until TermNorm supports ``node_outputs`` in responses
and ``precomputed`` in requests (Wave 4b).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from api.services.stores.base import read_json_optional, validate_path_component, write_json

logger = logging.getLogger(__name__)


class IntermediateCache:
    """Disk-backed cache for intermediate pipeline node outputs."""

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir

    def _cache_dir(self, backend_id: str) -> Path:
        return self._base_dir / backend_id / "intermediate_cache"

    def _cache_path(self, backend_id: str, upstream_hash: str) -> Path:
        validate_path_component(upstream_hash)
        return self._cache_dir(backend_id) / f"{upstream_hash}.json"

    def get(
        self,
        backend_id: str,
        upstream_hash: str,
        query: str,
    ) -> dict[str, Any] | None:
        """Load cached node outputs for a (upstream_hash, query) pair.

        Returns dict of {node_name: opaque_output} or None on miss.
        """
        if not upstream_hash:
            return None
        data = read_json_optional(self._cache_path(backend_id, upstream_hash))
        if data is None:
            return None
        return data.get(query)

    def put(
        self,
        backend_id: str,
        upstream_hash: str,
        query: str,
        node_outputs: dict[str, Any],
    ) -> None:
        """Store node outputs for a (upstream_hash, query) pair."""
        if not upstream_hash or not node_outputs:
            return
        path = self._cache_path(backend_id, upstream_hash)
        data = read_json_optional(path) or {}
        data[query] = node_outputs
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, data)
