"""Suffix-hash cache for pipeline node outputs.

Single flat KV store keyed by ``suffix_key(input_hash, tail)``.  Each
completed backend run populates every cut point of the executed
prefix, so a query with identical config hits in O(1) and a config
change on any single node hits the longest unchanged prefix in
O(n) hash checks.

See ``docs/architecture/suffix-cache.md`` for the rationale.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from promptpotter.domain.pipeline_schema import stable_hash, suffix_key
from promptpotter.infrastructure.store.base import (
    read_json_optional,
    validate_path_component,
    write_json,
)

__all__ = ["SuffixCache"]


class SuffixCache:
    """Disk-backed suffix-hash cache for pipeline node outputs.

    One file per suffix key: ``{base_dir}/{backend_id}/suffix_cache/{key}.json``
    containing ``{"node_outputs": {name: output}, "covers": [name, ...]}``.
    """

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir

    def _dir(self, backend_id: str) -> Path:
        validate_path_component(backend_id)
        return self._base_dir / backend_id / "suffix_cache"

    def _path(self, backend_id: str, key: str) -> Path:
        return self._dir(backend_id) / f"{key}.json"

    def lookup(self, backend_id: str, key: str) -> dict[str, Any] | None:
        """Return ``{"node_outputs", "covers"}`` for *key* or None."""
        return read_json_optional(self._path(backend_id, key))

    def put(
        self,
        backend_id: str,
        key: str,
        node_outputs: dict[str, Any],
        covers: list[str],
    ) -> None:
        path = self._path(backend_id, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, {"node_outputs": node_outputs, "covers": covers})

    def populate_from_run(
        self,
        backend_id: str,
        query: str,
        node_configs: list[tuple[str, dict[str, Any]]],
        node_outputs: dict[str, Any],
    ) -> None:
        """Emit cut-point entries for one completed run.

        Walks the pipeline in order and only emits cut-points within the
        contiguous executed prefix (stops at the first node missing from
        *node_outputs*, handling short-circuit / ``terminated_at`` runs).
        For n executed nodes, produces n(n+1)/2 entries — all derived
        locally by hashing, no extra I/O beyond the file writes.
        """
        if not node_configs or not node_outputs:
            return

        executed: list[int] = []
        for i, (name, _cfg) in enumerate(node_configs):
            if name not in node_outputs:
                break
            executed.append(i)
        if not executed:
            return

        q_hash = stable_hash(query)
        for i in executed:
            if i == 0:
                input_hash = q_hash
            else:
                prev_name = node_configs[i - 1][0]
                input_hash = stable_hash(node_outputs[prev_name])
            for j in range(i, executed[-1] + 1):
                tail = node_configs[i : j + 1]
                covers = [node_configs[k][0] for k in range(i, j + 1)]
                key = suffix_key(input_hash, tail)
                self.put(
                    backend_id,
                    key,
                    {name: node_outputs[name] for name in covers},
                    covers,
                )
