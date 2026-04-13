"""
Dataset run storage — archive layer for eval history.

Stores completed evaluation runs for SearchMemory, observability,
campaign lineage, and full-run cache lookup (``find_by_node_configs``).

Archive identity uses ``PipelineSchema.node_configs`` — the ordered
list of ``(node_name, node_config)`` tuples.  Prefix matching over
that list enables reuse of prior results when only downstream nodes
changed; ``sp_hash = stable_hash(node_configs)`` is the terminal
identity.

Prompt alias groups are used by ``recon_baseline.py`` and
``optimization_loop.py`` for SearchMemory historical linking.
"""

import hashlib
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from filelock import FileLock

from promptpotter.infrastructure.store.base import (
    read_json,
    read_json_optional,
    validate_path_component,
    write_json,
)
from promptpotter.shared.constants import (
    DATASET_RUNS_SCHEMA_VERSION,
    DEFAULT_CONNECTOR_TYPE,
    LOCK_TIMEOUT,
)
from promptpotter.shared.hashing import HASH_TRUNCATE

logger = logging.getLogger(__name__)


class DatasetRunStore:
    """File I/O for dataset evaluation runs and incremental eval writes."""

    def __init__(self, base_dir: Path):
        self._base_dir = base_dir
        # In-memory caches — invalidated on save()/register_alias()
        self._index_cache: dict[str, dict] = {}
        self._alias_cache: dict[str, dict] = {}

    # -- internal cache helpers -----------------------------------------------

    def _load_index(self, backend_id: str) -> dict:
        """Return cached index, loading from disk on first access."""
        if backend_id not in self._index_cache:
            self._index_cache[backend_id] = read_json_optional(self._index_path(backend_id)) or {
                "dataset_runs": [],
                "total": 0,
                "schema_version": DATASET_RUNS_SCHEMA_VERSION,
            }
        return self._index_cache[backend_id]

    def _load_aliases(self, backend_id: str) -> dict:
        """Return cached alias data, loading from disk on first access."""
        if backend_id not in self._alias_cache:
            self._alias_cache[backend_id] = read_json_optional(self._alias_path(backend_id)) or {
                "groups": []
            }
        return self._alias_cache[backend_id]

    def _invalidate_cache(self, backend_id: str) -> None:
        """Drop all in-memory caches for a backend (after write)."""
        self._index_cache.pop(backend_id, None)
        self._alias_cache.pop(backend_id, None)

    # -- path helpers ---------------------------------------------------------

    def _runs_dir(self, backend_id: str) -> Path:
        validate_path_component(backend_id)
        return self._base_dir / backend_id / "dataset_runs"

    def _index_path(self, backend_id: str) -> Path:
        return self._base_dir / backend_id / "dataset_runs.json"

    # -- complete runs --------------------------------------------------------

    def save(
        self,
        backend_id: str,
        run_id: str,
        data: dict[str, Any],
    ) -> Path:
        """Write detail file and upsert the index.

        ``data`` must include at least ``run_id``, ``content_hash``, and
        ``scores``.

        The detail file is written atomically (via ``write_json``).  The
        index update is protected by ``filelock`` to prevent concurrent
        writers from losing entries.
        """
        detail_path = self._runs_dir(backend_id) / f"{run_id}.json"
        write_json(detail_path, data)

        summary = {
            "run_id": data["run_id"],
            "name": data.get("name", run_id),
            "experiment_id": data.get("experiment_id", ""),
            "prompt_fields_id": data["prompt_fields_id"],
            "item_count": data["item_count"],
            "scores": data["scores"],
            "content_hash": data["content_hash"],
            "rendered_prompt_hash": data.get("rendered_prompt_hash", ""),
            "sp_hash": data.get("sp_hash", ""),
            "node_configs": data.get("node_configs"),
            "pipeline_params": data.get("pipeline_params"),
            "source": data.get("source", ""),
            "connector_type": data.get("connector_type", DEFAULT_CONNECTOR_TYPE),
            "created_at": data["created_at"],
        }

        index_path = self._index_path(backend_id)
        lock_path = index_path.with_suffix(".json.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        with FileLock(lock_path, timeout=LOCK_TIMEOUT):
            if index_path.exists():
                index = read_json(index_path)
            else:
                index = {"dataset_runs": [], "total": 0}

            content_hash_val = data.get("content_hash", "")
            entries = index["dataset_runs"]
            replaced = False
            for i, entry in enumerate(entries):
                if entry.get("content_hash") == content_hash_val:
                    entries[i] = summary
                    replaced = True
                    break
            if not replaced:
                entries.append(summary)

            index["total"] = len(entries)
            write_json(index_path, index)

        self._invalidate_cache(backend_id)
        return detail_path

    def load_by_id(
        self,
        backend_id: str,
        run_id: str,
    ) -> dict[str, Any] | None:
        """Load a dataset run detail file directly by run_id (no index scan)."""
        return read_json_optional(self._runs_dir(backend_id) / f"{run_id}.json")

    def list_all(self, backend_id: str) -> list[dict[str, Any]]:
        """Return the index entries (summaries without full items)."""
        return self._load_index(backend_id).get("dataset_runs", [])

    def load_since(
        self,
        backend_id: str,
        seen_ids: set[str],
    ) -> Iterator[tuple[str, dict[str, Any]]]:
        """Yield ``(run_id, detail)`` for runs whose ``run_id`` is not in ``seen_ids``.

        Skips runs whose detail file is missing. Encapsulates the index-scan +
        per-run load that SearchMemory.refresh used to do by hand.
        """
        for entry in self.list_all(backend_id):
            run_id = entry["run_id"]
            if run_id in seen_ids:
                continue
            detail = self.load_by_id(backend_id, run_id)
            if detail is None:
                continue
            yield run_id, detail

    def find_by_node_configs(
        self,
        backend_id: str,
        node_configs: list[tuple[str, dict]],
    ) -> list[tuple[dict[str, Any], int]]:
        """Return index entries sharing a node-config prefix, best match first.

        Compares each stored entry's ``node_configs`` element-by-element
        against *node_configs*.  Returns ``(entry, match_length)`` tuples
        sorted by match_length desc, then item_count desc.
        """
        if not node_configs:
            return []

        scored: list[tuple[dict[str, Any], int]] = []
        for entry in self.list_all(backend_id):
            stored = entry.get("node_configs")
            if not stored:
                continue
            match_len = 0
            for (n_want, c_want), stored_pair in zip(node_configs, stored, strict=False):
                # stored_pair may be [name, config] after JSON round-trip
                if not (isinstance(stored_pair, list | tuple) and len(stored_pair) == 2):
                    break
                n_have, c_have = stored_pair
                if n_have != n_want or c_have != c_want:
                    break
                match_len += 1
            if match_len > 0:
                scored.append((entry, match_len))

        scored.sort(key=lambda t: (t[1], t[0].get("item_count", 0)), reverse=True)
        return scored

    def load_reusable_results(
        self,
        backend_id: str,
        node_configs: list[tuple[str, dict]],
    ) -> dict[str, dict[str, Any]]:
        """Build per-query cache from prior dataset_runs sharing *node_configs*.

        Exact matches (``match_length == len(node_configs)``) reuse every
        non-error item.  Partial matches reuse only items whose
        ``pipeline_data.terminated_at`` falls within the shared prefix —
        the query short-circuited before the diverging node.

        Later runs overwrite earlier ones for the same query.
        """
        if not node_configs:
            return {}
        chain_len = len(node_configs)
        cache: dict[str, dict[str, Any]] = {}

        for entry, match_length in self.find_by_node_configs(backend_id, node_configs):
            detail = self.load_by_id(backend_id, entry["run_id"])
            if not detail:
                continue
            is_full_match = match_length >= chain_len
            trusted_nodes: set[str] = (
                set() if is_full_match else {node_configs[i][0] for i in range(match_length)}
            )
            for item in detail.get("dataset_run_items", []):
                q = item.get("query", "")
                if not q or item.get("predicted") == "ERROR":
                    continue
                if is_full_match:
                    cache[q] = item
                    continue
                terminated_at = (item.get("pipeline_data") or {}).get("terminated_at", "")
                if terminated_at and terminated_at in trusted_nodes:
                    cache[q] = item
        return cache

    # -- prompt alias groups ---------------------------------------------------

    def _alias_path(self, backend_id: str) -> Path:
        validate_path_component(backend_id)
        return self._base_dir / backend_id / "prompt_aliases.json"

    def register_alias(self, backend_id: str, *hashes: str) -> None:
        """Link rendered_prompt_hashes as semantically equivalent.

        Merges into existing groups: if any hash is already grouped,
        the new hashes join that group.
        """
        hashes_set = {h for h in hashes if h}
        if len(hashes_set) < 2:
            return

        path = self._alias_path(backend_id)
        data = read_json_optional(path) or {"groups": []}
        groups: list[list[str]] = data["groups"]

        # Find all existing groups that overlap with the new hashes
        merged: set[str] = set(hashes_set)
        remaining: list[list[str]] = []
        for group in groups:
            if merged & set(group):
                merged |= set(group)
            else:
                remaining.append(group)

        remaining.append(sorted(merged))
        data["groups"] = remaining
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, data)
        self._alias_cache.pop(backend_id, None)

    def register_prompt_alias(
        self,
        backend_id: str,
        raw_text: str,
        canonical_text: str,
    ) -> None:
        """Alias a raw prompt string to its restructured/canonical form.

        Hashes both sides and calls :meth:`register_alias`.  No-ops when
        either side is empty or both hash to the same value.
        """
        if not (raw_text and canonical_text):
            return
        raw_hash = hashlib.sha256(raw_text.encode()).hexdigest()[:HASH_TRUNCATE]
        canonical_hash = hashlib.sha256(canonical_text.encode()).hexdigest()[:HASH_TRUNCATE]
        if raw_hash == canonical_hash:
            return
        self.register_alias(backend_id, raw_hash, canonical_hash)
        logger.info("Registered prompt alias: %s ↔ %s", raw_hash[:8], canonical_hash[:8])

    def resolve_aliases(self, backend_id: str, rp_hash: str) -> set[str]:
        """Return all hashes equivalent to *rp_hash* (including itself)."""
        if not rp_hash:
            return set()
        data = self._load_aliases(backend_id)
        for group in data.get("groups", []):
            if rp_hash in group:
                return set(group)
        return {rp_hash}
