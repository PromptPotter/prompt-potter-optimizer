"""IntermediateCache — per-node output cache for partial pipeline reuse.

Each node's output is cached independently via
``node_cache_key(node, config, upstream_hash)``.  Chained dependency
means changing one node's config invalidates only that node + downstream.
``walk_prefix`` finds the longest cached prefix from pipeline start.

Enables partial pipeline execution: cached upstream outputs feed a request
via ``precomputed``, so the backend only runs uncached nodes.

Gracefully no-ops until the backend supports ``node_outputs`` in responses
and ``precomputed`` in requests.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from promptpotter.models.pipeline_schema import PipelineSchema

from promptpotter.services.stores.base import (
    read_json_optional,
    write_json,
)

logger = logging.getLogger(__name__)


def node_cache_key(
    node_name: str,
    node_config: dict[str, Any],
    upstream_hash: str = "",
) -> str:
    """Deterministic 16-char hex key for one node's output.

    Chains upstream: *upstream_hash* is the previous node's cache key.
    Changing any upstream node's config cascades invalidation downstream.
    """
    blob = json.dumps(
        {"node": node_name, "config": node_config, "upstream": upstream_hash},
        sort_keys=True,
        default=str,
    ).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def compute_prefix_keys(
    pipeline_params: dict[str, Any],
    pipeline_schema: PipelineSchema | None = None,
) -> list[tuple[str, str]]:
    """Compute chained cache keys for each node in pipeline order.

    Returns ``[(node_name, cache_key), ...]`` in execution order.
    Uses *pipeline_schema* node ordering when available, otherwise
    falls back to ``pipeline_params["steps"]``.
    """
    steps = pipeline_params.get("steps", [])
    if pipeline_schema:
        step_set = set(steps)
        ordered = [n.name for n in pipeline_schema.nodes if n.name in step_set]
    else:
        ordered = list(steps)

    result: list[tuple[str, str]] = []
    upstream = ""
    for name in ordered:
        config = pipeline_params.get(name, {})
        if not isinstance(config, dict):
            config = {}
        key = node_cache_key(name, config, upstream)
        result.append((name, key))
        upstream = key
    return result


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
