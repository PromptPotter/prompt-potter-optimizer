"""IntermediateCache — per-node output cache for partial pipeline reuse.

Keyed by step sequence: ``put_steps`` stores node outputs under the
ordered step list that produced them; ``get_prefix`` finds the longest
cached prefix of a target step list.

Enables partial pipeline execution: cached ``[web, entity]`` outputs
feed a ``[web, entity, token, llm]`` request via ``precomputed``,
so the backend only runs ``[token, llm]``.

Gracefully no-ops until the backend supports ``node_outputs`` in responses
and ``precomputed`` in requests.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from promptpotter.services.stores.base import (
    read_json_optional,
    validate_path_component,
    write_json,
)

logger = logging.getLogger(__name__)


def _steps_hash(steps: list[str], pipeline_params: dict[str, Any] | None = None) -> str:
    """Deterministic short hash for step list + pipeline params.

    Incorporates pipeline_params so different configurations (model,
    temperature, schema, etc.) produce distinct cache keys.
    """
    key_parts: list[Any] = [steps]
    if pipeline_params:
        # Exclude 'steps' from params hash (already in the step list)
        pp = {k: v for k, v in sorted(pipeline_params.items()) if k != "steps"}
        if pp:
            key_parts.append(pp)
    blob = json.dumps(key_parts, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


class IntermediateCache:
    """Disk-backed cache for intermediate pipeline node outputs."""

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir

    def _cache_dir(self, backend_id: str) -> Path:
        return self._base_dir / backend_id / "intermediate_cache"

    def _cache_path(self, backend_id: str, key: str) -> Path:
        validate_path_component(key)
        return self._cache_dir(backend_id) / f"{key}.json"

    def _index_path(self, backend_id: str) -> Path:
        return self._cache_dir(backend_id) / "_step_index.json"

    # -- Step-sequence mode --------------------------------------------------

    def _load_step_index(self, backend_id: str) -> dict[str, list[str]]:
        """Load {steps_hash: steps_list} index."""
        return read_json_optional(self._index_path(backend_id)) or {}

    def _save_step_index(self, backend_id: str, index: dict[str, list[str]]) -> None:
        path = self._index_path(backend_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, index)

    def put_steps(
        self,
        backend_id: str,
        steps: list[str],
        query: str,
        node_outputs: dict[str, Any],
        pipeline_params: dict[str, Any] | None = None,
    ) -> None:
        """Store node outputs keyed by step sequence + pipeline params + query."""
        if not steps or not node_outputs:
            return
        sh = _steps_hash(steps, pipeline_params)
        path = self._cache_path(backend_id, f"steps_{sh}")
        data = read_json_optional(path) or {}
        data[query] = node_outputs
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, data)

        # Update index
        index = self._load_step_index(backend_id)
        if sh not in index:
            index[sh] = steps
            self._save_step_index(backend_id, index)

    def get_prefix(
        self,
        backend_id: str,
        query: str,
        target_steps: list[str],
        pipeline_params: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], list[str]] | None:
        """Find the longest cached prefix for a query.

        When *pipeline_params* is provided, only returns exact-match entries
        (same steps + same params). Without pipeline_params, falls back to
        step-only matching for backward compatibility.

        Returns ``(node_outputs, cached_steps)`` for the longest match,
        or ``None`` on miss.
        """
        if not target_steps:
            return None

        # Fast path: exact match with pipeline_params
        if pipeline_params is not None:
            sh = _steps_hash(target_steps, pipeline_params)
            data = read_json_optional(self._cache_path(backend_id, f"steps_{sh}"))
            if data and query in data:
                return data[query], target_steps
            return None

        # Legacy path: scan index for step-prefix matches
        index = self._load_step_index(backend_id)
        best: tuple[dict[str, Any], list[str]] | None = None
        best_len = 0

        for sh, cached_steps in index.items():
            n = len(cached_steps)
            if n > len(target_steps) or n <= best_len:
                continue
            if target_steps[:n] != cached_steps:
                continue
            data = read_json_optional(self._cache_path(backend_id, f"steps_{sh}"))
            if data and query in data:
                best = (data[query], cached_steps)
                best_len = n

        return best
