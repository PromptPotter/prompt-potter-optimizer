"""IntermediateCache — per-node output cache for partial pipeline reuse.

Chained dependency keys (via ``node_cache_key`` in ``pipeline_schema``)
mean changing one node's config invalidates only that node + downstream.
``walk_prefix`` finds the longest cached prefix from pipeline start.

Enables partial pipeline execution: cached upstream outputs feed a request
via ``precomputed``, so the backend only runs uncached nodes.

Gracefully no-ops until the backend supports ``node_outputs`` in responses
and ``precomputed`` in requests.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from promptpotter.services.store.base import (
    read_json_optional,
    write_json,
)

logger = logging.getLogger(__name__)


class IntermediateCache:
    """Disk-backed cache for intermediate pipeline node outputs.

    Per-node keying: each node's output is stored in a separate file
    ``{node_name}_{cache_key}.json`` containing ``{query: output}``.
    """

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir

    def _cache_dir(self, backend_id: str) -> Path:
        return self._base_dir / backend_id / "intermediate_cache"

    def get_node(
        self,
        backend_id: str,
        node_name: str,
        cache_key: str,
        query: str,
    ) -> dict[str, Any] | None:
        """Load a single node's cached output for *query*."""
        path = self._cache_dir(backend_id) / f"{node_name}_{cache_key}.json"
        data = read_json_optional(path)
        if data and query in data:
            return data[query]
        return None

    def put_node(
        self,
        backend_id: str,
        node_name: str,
        cache_key: str,
        query: str,
        node_output: Any,
    ) -> None:
        """Store a single node's output for *query*."""
        path = self._cache_dir(backend_id) / f"{node_name}_{cache_key}.json"
        data = read_json_optional(path) or {}
        data[query] = node_output
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, data)

    def walk_prefix(
        self,
        backend_id: str,
        query: str,
        prefix_keys: list[tuple[str, str]],
    ) -> tuple[dict[str, Any], list[str]]:
        """Walk node chain, returning cached outputs up to first miss.

        Returns ``(node_outputs, cached_node_names)`` where *node_outputs*
        maps node name to output for all consecutively cached nodes from
        the pipeline start.
        """
        node_outputs: dict[str, Any] = {}
        cached_names: list[str] = []
        for node_name, cache_key in prefix_keys:
            output = self.get_node(backend_id, node_name, cache_key, query)
            if output is None:
                break
            node_outputs[node_name] = output
            cached_names.append(node_name)
        return node_outputs, cached_names
