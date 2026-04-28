"""Measurement archive — the database core, cross-cycle measurement storage.

The archive is the canonical store of every ``(sample × config → outcome)`` row
ever recorded. Append-only, content-addressed, cross-cycle, cross-session,
cross-tenant within a tenant root. Two natural views: by training example
(``measurements_for_sample``) and by searchpoint / config
(``measurements_for_config``). Cache reuse during scoring uses the same
underlying matcher in ``prefix_exact`` mode; discovery retrieval uses
``subset`` mode. See ``docs/concepts/measurement-archive.md``.
"""

import hashlib
import logging
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, Literal

from filelock import FileLock

from promptpotter.domain.measurement import Measurement
from promptpotter.infrastructure.store.base import (
    read_json,
    read_json_optional,
    write_json,
)
from promptpotter.shared.constants import (
    DEFAULT_CONNECTOR_TYPE,
    LOCK_TIMEOUT,
    MEASUREMENTS_SCHEMA_VERSION,
)
from promptpotter.shared.hashing import HASH_TRUNCATE

logger = logging.getLogger(__name__)


def _node_config_matches(
    run_node_configs: list[Any],
    spec: list[tuple[str, dict[str, Any]]] | dict[str, dict[str, Any]],
    *,
    mode: Literal["prefix_exact", "subset"],
) -> int:
    """Match a stored ``node_configs`` chain against a *spec*.

    ``mode="prefix_exact"`` — *spec* is an ordered list of ``(name, cfg)``
    tuples; matches stored entries position-by-position with full dict
    equality, stops at first mismatch. Returns match length (0 = no match).
    Used by the cache reuse path (``find_by_node_configs``).

    ``mode="subset"`` — *spec* is a ``{node_name: required_subdict}`` dict;
    a stored chain matches iff every (name, subdict) in spec finds a
    ``[name, cfg]`` in the chain where ``subdict.items() <= cfg.items()``.
    Empty subdict tests node presence only. Empty spec returns 0 (no-match).
    Returns 1 on full subset match, 0 otherwise. Used by discovery retrieval
    (``measurements_for_config``).
    """
    if mode == "prefix_exact":
        if not isinstance(spec, list) or not spec:
            return 0
        match_len = 0
        for (n_want, c_want), stored_pair in zip(spec, run_node_configs, strict=False):
            # stored_pair may be [name, config] after JSON round-trip
            if not (isinstance(stored_pair, list | tuple) and len(stored_pair) == 2):
                break
            n_have, c_have = stored_pair
            if n_have != n_want or c_have != c_want:
                break
            match_len += 1
        return match_len

    # mode == "subset"
    if not isinstance(spec, dict) or not spec:
        return 0
    by_name: dict[str, dict[str, Any]] = {}
    for stored_pair in run_node_configs:
        if not (isinstance(stored_pair, list | tuple) and len(stored_pair) == 2):
            continue
        n_have, c_have = stored_pair
        if isinstance(c_have, dict):
            by_name[n_have] = c_have
    for node_name, subdict in spec.items():
        cfg = by_name.get(node_name)
        if cfg is None:
            return 0
        if subdict and not (subdict.items() <= cfg.items()):
            return 0
    return 1


class MeasurementArchive:
    """File I/O for the measurement archive — the database core.

    Tenant-global: every measurement batch lives under
    ``library/measurements/`` regardless of ``backend_id``.
    Content-addressed identity comes from ``PipelineSchema.node_configs()`` —
    no cross-backend collision risk since each config blob carries the
    backend's pipeline shape.

    The ``backend_id`` parameter is preserved on public methods for
    call-site stability but is ignored for path construction.
    """

    def __init__(self, base_dir: Path):
        # base_dir is the tenant root
        self._base_dir = base_dir

    # -- path helpers ---------------------------------------------------------

    def _runs_dir(self, _backend_id: str) -> Path:
        return self._base_dir / "library" / "measurements"

    def _index_path(self, _backend_id: str) -> Path:
        return self._base_dir / "library" / "measurements.json"

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
                index = {"measurements": [], "total": 0}

            content_hash_val = data.get("content_hash", "")
            entries = index["measurements"]
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

        return detail_path

    def load_by_id(
        self,
        backend_id: str,
        run_id: str,
    ) -> dict[str, Any] | None:
        """Load a run detail file directly by run_id (no index scan)."""
        return read_json_optional(self._runs_dir(backend_id) / f"{run_id}.json")

    def list_all(self, backend_id: str) -> list[dict[str, Any]]:
        """Return the index entries (summaries without full items)."""
        index = read_json_optional(self._index_path(backend_id)) or {
            "measurements": [],
            "total": 0,
            "schema_version": MEASUREMENTS_SCHEMA_VERSION,
        }
        return index.get("measurements", [])

    def load_since(
        self,
        backend_id: str,
        seen_ids: set[str],
    ) -> Iterator[tuple[str, dict[str, Any]]]:
        """Yield ``(run_id, detail)`` for runs whose ``run_id`` is not in ``seen_ids``.

        Skips runs whose detail file is missing. Encapsulates the index-scan +
        per-run load that derived views (e.g. SearchMemory) used to do by hand.
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
            match_len = _node_config_matches(stored, node_configs, mode="prefix_exact")
            if match_len > 0:
                scored.append((entry, match_len))

        scored.sort(key=lambda t: (t[1], t[0].get("item_count", 0)), reverse=True)
        return scored

    # -- direct retrieval (the database-core view) -----------------------------

    def measurements_for_sample(
        self,
        backend_id: str,
        sample_id: int,
        *,
        run_ids: list[str] | None = None,
    ) -> list[Measurement]:
        """Every measurement of one training example, across all configs.

        When ``run_ids`` is provided (typically from
        ``Sample.run_ids``), skips the index scan and loads only those
        files. When ``None``, walks every batch in the archive.
        """
        if run_ids is not None:
            sources: Iterator[tuple[str, dict[str, Any]]] = (
                (rid, detail)
                for rid in run_ids
                if (detail := self.load_by_id(backend_id, rid)) is not None
            )
        else:
            sources = (
                (entry["run_id"], detail)
                for entry in self.list_all(backend_id)
                if (detail := self.load_by_id(backend_id, entry["run_id"])) is not None
            )

        out: list[Measurement] = []
        for run_id, detail in sources:
            for item in detail.get("measurements", []):
                if item.get("sample_id") == sample_id:
                    out.append(_to_measurement(run_id, detail, item))
        return out

    def measurements_for_config(
        self,
        backend_id: str,
        predicate: dict[str, dict[str, Any]],
    ) -> list[Measurement]:
        """Every measurement under configs matching *predicate*, across all samples.

        ``predicate`` is ``{node_name: required_subdict}`` — see
        :func:`_node_config_matches` for subset semantics. Empty
        predicate returns ``[]``.
        """
        if not predicate:
            return []

        out: list[Measurement] = []
        for entry in self.list_all(backend_id):
            stored = entry.get("node_configs")
            if not stored:
                continue
            if _node_config_matches(stored, predicate, mode="subset") == 0:
                continue
            run_id = entry["run_id"]
            detail = self.load_by_id(backend_id, run_id)
            if detail is None:
                continue
            for item in detail.get("measurements", []):
                out.append(_to_measurement(run_id, detail, item))
        return out

    def load_reusable_results(
        self,
        backend_id: str,
        node_configs: list[tuple[str, dict]],
        is_fatal: Callable[[dict[str, Any]], bool] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Build per-query cache from prior runs sharing *node_configs*.

        Exact matches (``match_length == len(node_configs)``) reuse every
        non-error item.  Partial matches reuse only items whose
        ``pipeline_data.terminated_at`` falls within the shared prefix —
        the query short-circuited before the diverging node.

        Later runs overwrite earlier ones for the same query, **except** when
        ``is_fatal`` is supplied and the incoming row is fatal while the
        existing row is clean — in that case the clean row stands. This keeps
        a saved valid retry from being shadowed by an older deprecated archive
        on a subsequent load. Policy callback stays in the caller's layer so
        the archive remains a pure I/O adapter.
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
            for item in detail.get("measurements", []):
                q = item.get("query", "")
                if not q or item.get("predicted") == "ERROR":
                    continue
                if not is_full_match:
                    terminated_at = (item.get("pipeline_data") or {}).get("terminated_at", "")
                    if not (terminated_at and terminated_at in trusted_nodes):
                        continue
                existing = cache.get(q)
                if (
                    existing is not None
                    and is_fatal is not None
                    and is_fatal(item)
                    and not is_fatal(existing)
                ):
                    continue
                cache[q] = item
        return cache

    # -- prompt alias groups ---------------------------------------------------

    def _alias_path(self, _backend_id: str) -> Path:
        return self._base_dir / "library" / "prompt_aliases.json"

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
        data = read_json_optional(self._alias_path(backend_id)) or {"groups": []}
        for group in data.get("groups", []):
            if rp_hash in group:
                return set(group)
        return {rp_hash}


def _to_measurement(
    run_id: str,
    detail: dict[str, Any],
    item: dict[str, Any],
) -> Measurement:
    """Project a stored item + its enclosing run into a flat Measurement row."""
    raw_configs = detail.get("node_configs") or []
    node_configs: list[tuple[str, dict[str, Any]]] = [
        (pair[0], pair[1])
        for pair in raw_configs
        if isinstance(pair, list | tuple) and len(pair) == 2 and isinstance(pair[1], dict)
    ]
    score = item.get("score")
    return Measurement(
        run_id=run_id,
        content_hash=detail.get("content_hash", ""),
        sample_id=int(item.get("sample_id", -1)),
        query=item.get("query", ""),
        ground_truth=item.get("ground_truth", ""),
        predicted=item.get("predicted", ""),
        hit=bool(item.get("hit", False)),
        score=float(score) if score is not None else None,
        node_configs=node_configs,
        pipeline_data=item.get("pipeline_data") or {},
        created_at=detail.get("created_at", ""),
        run_scores=detail.get("scores") or {},
    )
