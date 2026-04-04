"""
Dataset run (eval result caching) storage.

Performance: in-memory caches for the index, alias groups, and
rendered-prompt-hash secondary index avoid repeated disk reads during
scan/grid/feedback-cycle hot loops.  All caches are invalidated on write.
"""
import logging
from pathlib import Path
from typing import Any

from filelock import FileLock

from api.services.stores.base import (
    read_json,
    read_json_optional,
    validate_path_component,
    write_json,
)
from api.shared.constants import (
    DATASET_RUNS_SCHEMA_VERSION,
    DEFAULT_CONNECTOR_TYPE,
    LOCK_TIMEOUT,
)

logger = logging.getLogger(__name__)


def _entry_matches(
    entry_pp: dict | None,
    target_pp: dict | None,
    strict_params: dict[str, set[str]] | None = None,
) -> bool:
    """Check whether a historical entry's pipeline_params match a target.

    Step matching uses **ordered prefix**: target steps must be an ordered
    prefix of (or equal to) entry steps.  This allows cached runs with more
    nodes to serve requests for fewer nodes (superset reuse).

    Args:
        entry_pp: pipeline_params from a stored index entry.
        target_pp: pipeline_params we want to match against.
        strict_params: Controls strictness:
            ``None`` — exact full-dict equality (steps still use prefix).
            ``{}`` — steps prefix match, all other params relaxed.
            ``{"node": {"param"}}`` — steps prefix + listed params must match.
    """
    entry_pp = entry_pp or {}
    target_pp = target_pp or {}

    # Steps: target must be an ordered prefix of entry (superset OK)
    entry_steps = entry_pp.get("steps", [])
    target_steps = target_pp.get("steps", [])
    if len(target_steps) > len(entry_steps):
        return False
    if entry_steps[: len(target_steps)] != target_steps:
        return False

    if strict_params is None:
        # Exact mode: params for target nodes must match.
        # Ignore node configs for superset-only nodes (e.g. llm_ranking
        # config when target excludes llm_ranking).
        extra_nodes = set(entry_steps) - set(target_steps)
        entry_filtered = {k: v for k, v in entry_pp.items() if k != "steps" and k not in extra_nodes}
        target_filtered = {k: v for k, v in target_pp.items() if k != "steps"}
        return entry_filtered == target_filtered

    # NN mode: only listed params must match
    for node, params in strict_params.items():
        entry_node = entry_pp.get(node, {})
        target_node = target_pp.get(node, {})
        if not isinstance(entry_node, dict) or not isinstance(target_node, dict):
            if entry_node != target_node:
                return False
            continue
        for p in params:
            if entry_node.get(p) != target_node.get(p):
                return False
    return True


class DatasetRunStore:
    """File I/O for dataset evaluation runs and incremental eval writes."""

    def __init__(self, base_dir: Path):
        self._base_dir = base_dir
        # In-memory caches — invalidated on save()/register_alias()
        self._index_cache: dict[str, dict] = {}
        self._alias_cache: dict[str, dict] = {}
        self._rp_index: dict[str, dict[str, list[dict]]] = {}

    # -- internal cache helpers -----------------------------------------------

    def _load_index(self, backend_id: str) -> dict:
        """Return cached index, loading from disk on first access."""
        if backend_id not in self._index_cache:
            self._index_cache[backend_id] = (
                read_json_optional(self._index_path(backend_id))
                or {"dataset_runs": [], "total": 0, "schema_version": DATASET_RUNS_SCHEMA_VERSION}
            )
        return self._index_cache[backend_id]

    def _load_aliases(self, backend_id: str) -> dict:
        """Return cached alias data, loading from disk on first access."""
        if backend_id not in self._alias_cache:
            self._alias_cache[backend_id] = (
                read_json_optional(self._alias_path(backend_id))
                or {"groups": []}
            )
        return self._alias_cache[backend_id]

    def _get_rp_index(self, backend_id: str) -> dict[str, list[dict]]:
        """Return {rendered_prompt_hash: [index_entries]} secondary index."""
        if backend_id not in self._rp_index:
            idx: dict[str, list[dict]] = {}
            for entry in self._load_index(backend_id).get("dataset_runs", []):
                rp = entry.get("rendered_prompt_hash", "")
                if rp:
                    idx.setdefault(rp, []).append(entry)
            self._rp_index[backend_id] = idx
        return self._rp_index[backend_id]

    def _invalidate_cache(self, backend_id: str) -> None:
        """Drop all in-memory caches for a backend (after write)."""
        self._index_cache.pop(backend_id, None)
        self._alias_cache.pop(backend_id, None)
        self._rp_index.pop(backend_id, None)

    def clear_caches(self) -> None:
        """Drop all in-memory caches across all backends.

        Call between campaign cycles or when memory pressure is a concern.
        """
        self._index_cache.clear()
        self._alias_cache.clear()
        self._rp_index.clear()

    # -- path helpers ---------------------------------------------------------

    def _runs_dir(self, backend_id: str) -> Path:
        validate_path_component(backend_id)
        return self._base_dir / backend_id / "dataset_runs"

    def _index_path(self, backend_id: str) -> Path:
        return self._base_dir / backend_id / "dataset_runs.json"

    # -- complete runs --------------------------------------------------------

    def save(
        self, backend_id: str, run_id: str, data: dict[str, Any],
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

            content_hash = data.get("content_hash", "")
            entries = index["dataset_runs"]
            replaced = False
            for i, entry in enumerate(entries):
                if entry.get("content_hash") == content_hash:
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
        self, backend_id: str, run_id: str,
    ) -> dict[str, Any] | None:
        """Load a dataset run detail file directly by run_id (no index scan)."""
        return read_json_optional(self._runs_dir(backend_id) / f"{run_id}.json")

    def load_by_hash(
        self, backend_id: str, content_hash: str,
    ) -> dict[str, Any] | None:
        """Scan the cached index for a matching content_hash, load detail."""
        for entry in self._load_index(backend_id).get("dataset_runs", []):
            if entry.get("content_hash") == content_hash:
                return read_json_optional(
                    self._runs_dir(backend_id) / f"{entry['run_id']}.json",
                )
        return None

    def list_all(self, backend_id: str) -> list[dict[str, Any]]:
        """Return the index entries (summaries without full items)."""
        return self._load_index(backend_id).get("dataset_runs", [])

    def load_by_alias(
        self,
        backend_id: str,
        rp_hash: str,
        pipeline_params: dict | None,
        item_count: int,
    ) -> dict[str, Any] | None:
        """Find a cached run via prompt alias groups.

        Uses the secondary rp_hash index for O(alias_set × entries_per_hash)
        lookups instead of O(total_entries) linear scan.
        """
        alias_set = self.resolve_aliases(backend_id, rp_hash)
        rp_idx = self._get_rp_index(backend_id)

        for h in alias_set:
            for entry in rp_idx.get(h, []):
                if entry.get("pipeline_params") != pipeline_params:
                    continue
                if entry.get("item_count") != item_count:
                    continue
                return read_json_optional(
                    self._runs_dir(backend_id) / f"{entry['run_id']}.json",
                )
        return None

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

    def find_cached_queries(
        self,
        backend_id: str,
        rendered_prompt_hash: str,
        pipeline_params: dict | None,
        strict_params: dict[str, set[str]] | None = None,
    ) -> dict[str, dict]:
        """Find cached query results for a SearchPoint.

        Returns ``{query_string: result_dict}`` from all prior runs that
        match on rendered prompt, step sequence, and param constraints.
        Different sample sizes are bridged automatically.

        Args:
            rendered_prompt_hash: Hash of the rendered prompt string.
            pipeline_params: Target pipeline_params to match against.
            strict_params: Controls matching strictness:
                ``None`` — exact: full ``pipeline_params`` dict equality.
                ``{}`` — maximally loose: prompt + steps only.
                ``{"node": {"param"}}`` — listed params must match,
                others may differ.

        Later runs overwrite earlier (last-write-wins per query).
        """
        candidates = self._get_rp_index(backend_id).get(rendered_prompt_hash, [])
        matching = [
            e for e in candidates
            if _entry_matches(e.get("pipeline_params"), pipeline_params, strict_params)
        ]

        results: dict[str, dict] = {}
        for entry in matching:
            detail = read_json_optional(
                self._runs_dir(backend_id) / f"{entry['run_id']}.json",
            )
            if not detail:
                continue
            entry_steps = (entry.get("pipeline_params") or {}).get("steps", [])
            for item in detail.get("dataset_run_items", []):
                query = item.get("query", "")
                if query:
                    item["_cache_steps"] = entry_steps
                    results[query] = item
        return results

    def resolve_aliases(self, backend_id: str, rp_hash: str) -> set[str]:
        """Return all hashes equivalent to *rp_hash* (including itself)."""
        if not rp_hash:
            return set()
        data = self._load_aliases(backend_id)
        for group in data.get("groups", []):
            if rp_hash in group:
                return set(group)
        return {rp_hash}
