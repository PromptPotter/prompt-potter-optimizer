"""
Dataset run storage — archive layer for eval history.

Stores completed evaluation runs for SearchMemory, observability,
campaign lineage, and full-run cache lookup (``find_by_sp_hash``).
Per-node reuse is handled separately by ``IntermediateCache``.

``config_hash`` is still used by ``coverage.py`` for scan variant
matching.  Prompt alias groups are used by ``scan_baseline.py`` and
``optimization_loop.py`` for SearchMemory historical linking.
"""

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from filelock import FileLock

from promptpotter.services.stores.base import (
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


# Keys in node config dicts that are tracked by rp_hash, not by
# pipeline config.  Stripped before hashing to avoid asymmetry
# (stored entries may include them, lookup may not).
# output_schema is NOT stripped — schema mutations are independent
# of rendered prompt text and must produce distinct cache keys.
_PROMPT_KEYS = frozenset({"prompt"})


def _normalize_pp(pipeline_params: dict | None) -> dict:
    """Normalize pipeline_params for hashing: strip prompt/output_schema."""
    pp = pipeline_params or {}
    out: dict = {}
    for k, v in pp.items():
        if isinstance(v, dict):
            out[k] = {pk: pv for pk, pv in v.items() if pk not in _PROMPT_KEYS}
        else:
            out[k] = v
    return out


def config_hash(pipeline_params: dict | None, rp_hash: str = "") -> str:
    """Canonical hash of pipeline config identity.

    Combines ``rp_hash`` (prompt identity) with normalized
    ``pipeline_params`` (steps + all node configs, excluding prompt
    and output_schema which are already captured by rp_hash).

    Two configs with the same hash ran the same steps, same prompt,
    and same node params — guaranteed identical results.
    """
    normalized = _normalize_pp(pipeline_params)
    blob = json.dumps({"pp": normalized, "rp": rp_hash}, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:HASH_TRUNCATE]


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

    def find_by_sp_hash(self, backend_id: str, sp_hash: str) -> list[dict[str, Any]]:
        """Return index entries matching *sp_hash*, most items first."""
        matches = [e for e in self.list_all(backend_id) if e.get("sp_hash") == sp_hash]
        matches.sort(key=lambda e: e.get("item_count", 0), reverse=True)
        return matches

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

    def resolve_aliases(self, backend_id: str, rp_hash: str) -> set[str]:
        """Return all hashes equivalent to *rp_hash* (including itself)."""
        if not rp_hash:
            return set()
        data = self._load_aliases(backend_id)
        for group in data.get("groups", []):
            if rp_hash in group:
                return set(group)
        return {rp_hash}
