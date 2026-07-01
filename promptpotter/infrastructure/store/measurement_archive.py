"""Measurement archive — DB core. Append-only, content-addressed, cross-cycle/session/tenant.

Two views: by sample (`measurements_for_sample`) and by config (`measurements_for_config`).
Cache reuse → positional prefix-exact; discovery → `_matches_subset`. Sole source of truth —
derived views (AxisIndex, SampleIndex) refresh from `list_all`, not a parallel stream.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from filelock import FileLock

from promptpotter.config.settings import (
    DEFAULT_CONNECTOR_TYPE,
    LOCK_TIMEOUT,
    MEASUREMENTS_SCHEMA_VERSION,
)
from promptpotter.domain.measurement_provenance import entry_grade, meets_grade
from promptpotter.domain.sample import Measurement
from promptpotter.infrastructure.store.io import (
    read_json,
    read_json_optional,
    write_json,
)
from promptpotter.shared.hashing import HASH_TRUNCATE

logger = logging.getLogger(__name__)


def _matches_subset(
    run_node_configs: list[Any],
    predicate: dict[str, dict[str, Any]],
) -> bool:
    """Every node in *predicate* must appear in the stored chain with at least the required pairs.
    Empty subdict tests presence; empty predicate returns False (no constraints → no rows).
    """
    if not predicate:
        return False
    by_name: dict[str, dict[str, Any]] = {}
    for stored_pair in run_node_configs:
        if not (isinstance(stored_pair, list | tuple) and len(stored_pair) == 2):
            continue
        n_have, c_have = stored_pair
        if isinstance(c_have, dict):
            by_name[n_have] = c_have
    for node_name, subdict in predicate.items():
        cfg = by_name.get(node_name)
        if cfg is None:
            return False
        if subdict and not (subdict.items() <= cfg.items()):
            return False
    return True


def _entry_dataset(entry: dict[str, Any]) -> str | None:
    """Extract the dataset_name from an archive entry; empty / missing → None."""
    val = entry.get("dataset_name")
    return val if isinstance(val, str) and val else None


def _entry_matches_dataset(
    entry: dict[str, Any],
    dataset_name: str | None,
    *,
    include_unknown: bool,
) -> bool:
    """`None` ⇒ everything (forensic/admin). Concrete name ⇒ stamped match; v1-unstamped entries
    pass only with `include_unknown=True`.
    """
    if dataset_name is None:
        return True
    ds = _entry_dataset(entry)
    if ds == dataset_name:
        return True
    return ds is None and include_unknown


class MeasurementArchive:
    """File I/O for the measurement store — the DB core, NOT the recycle bin.

    Tenant-global, self-contained under `measurements/` (regardless of backend_id —
    content-addressed via `PipelineSchema.node_configs()` avoids cross-backend
    collisions): run-detail files `measurements/{run_id}.json`, the index
    `measurements/measurements_index.json`, and the alias groups
    `measurements/prompt_aliases.json` all live together. It sits beside
    `campaigns/` and `archive/` (the recycle bin), never inside `archive/` — it is
    a cross-campaign cache, not trash. Run files are reached by explicit `run_id`
    (`load_by_id`) or via the index (`list_all`); nothing globs the dir, so the
    index + alias files share it safely. `backend_id` is preserved on public
    methods for call-site stability but ignored for paths.
    """

    def __init__(self, base_dir: Path):
        self._base_dir = base_dir

    # -- path helpers ---------------------------------------------------------

    def _store_dir(self) -> Path:
        return self._base_dir / "measurements"

    def _runs_dir(self, _backend_id: str) -> Path:
        return self._store_dir()

    def _index_path(self, _backend_id: str) -> Path:
        return self._store_dir() / "measurements_index.json"

    def dataset_snapshot_path(self, backend_id: str, dataset_name: str) -> Path:
        """Path of the per-(backend, dataset) hard-samples snapshot — the store owns
        its own layout, so the writer never reconstructs `measurements/…` inline."""
        return self._store_dir() / f"hard_samples_{backend_id}_{dataset_name}.json"

    # -- complete runs --------------------------------------------------------

    def save(
        self,
        backend_id: str,
        run_id: str,
        data: dict[str, Any],
    ) -> Path:
        """Write detail + upsert index. *data* needs `run_id`, `content_hash`, `scores`.
        Detail = atomic write; index = `filelock`-protected against concurrent writers.
        """
        detail_path = self._runs_dir(backend_id) / f"{run_id}.json"
        write_json(detail_path, data)

        summary = {
            "run_id": data["run_id"],
            "name": data.get("name", run_id),
            "dataset_name": data.get("dataset_name"),
            "experiment_id": data.get("experiment_id", ""),
            "prompt_fields_id": data["prompt_fields_id"],
            "item_count": data["item_count"],
            "scores": data["scores"],
            "content_hash": data["content_hash"],
            "rendered_prompt_hash": data.get("rendered_prompt_hash", ""),
            "node_configs": data.get("node_configs"),
            "pipeline_params": data.get("pipeline_params"),
            "source": data.get("source", ""),
            "provenance": data.get("provenance"),
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
                    old_run_id = entry.get("run_id", "")
                    entries[i] = summary
                    replaced = True
                    if old_run_id and old_run_id != run_id:
                        (self._runs_dir(backend_id) / f"{old_run_id}.json").unlink(missing_ok=True)
                    break
            if not replaced:
                entries.append(summary)

            index["total"] = len(entries)
            index["schema_version"] = MEASUREMENTS_SCHEMA_VERSION
            write_json(index_path, index)

        return detail_path

    def load_by_id(
        self,
        backend_id: str,
        run_id: str,
    ) -> dict[str, Any] | None:
        """Load a run detail file directly by run_id (no index scan)."""
        return read_json_optional(self._runs_dir(backend_id) / f"{run_id}.json")

    def restamp_dataset(self, old_name: str, new_name: str) -> int:
        """Rewrite every archive entry stamped *old_name* → *new_name* (index summary
        + the matching detail file's ``dataset_name``). Returns the count restamped.

        The measurement half of the dataset version-and-repoint migration
        (``application/datasets/dataset_replace.py``): when a dataset's data is
        archived under a ``-vN`` name, its prior campaigns' measurements move with
        it so dataset-scoped reuse + filtering stay truthful. Idempotent — only
        entries still stamped *old_name* are touched, so a re-run after a crash is
        a no-op. ``backend_id`` is path-irrelevant here (the archive is
        tenant-global), so it's elided. Index write is ``filelock``-protected
        against a concurrent writer, mirroring :meth:`save`.
        """
        index_path = self._index_path("")
        if not index_path.exists():
            return 0
        lock_path = index_path.with_suffix(".json.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with FileLock(lock_path, timeout=LOCK_TIMEOUT):
            index = read_json(index_path)
            for entry in index.get("measurements", []):
                if entry.get("dataset_name") != old_name:
                    continue
                entry["dataset_name"] = new_name
                count += 1
                run_id = entry.get("run_id", "")
                detail = self.load_by_id("", run_id) if run_id else None
                if detail is not None and detail.get("dataset_name") == old_name:
                    detail["dataset_name"] = new_name
                    write_json(self._runs_dir("") / f"{run_id}.json", detail)
            if count:
                write_json(index_path, index)
        return count

    def list_all(
        self,
        backend_id: str,
        *,
        dataset_name: str | None = None,
        include_unknown: bool = False,
    ) -> list[dict[str, Any]]:
        """Index entries (summaries). *dataset_name* scopes to one dataset (None = forensic/admin);
        v1-unstamped entries appear only with `include_unknown=True`.
        """
        index = read_json_optional(self._index_path(backend_id)) or {
            "measurements": [],
            "total": 0,
            "schema_version": MEASUREMENTS_SCHEMA_VERSION,
        }
        entries: list[dict[str, Any]] = index.get("measurements", [])
        if dataset_name is None and include_unknown:
            return entries
        return [
            e
            for e in entries
            if _entry_matches_dataset(e, dataset_name, include_unknown=include_unknown)
        ]

    def load_since(
        self,
        backend_id: str,
        seen_ids: set[str],
        *,
        dataset_name: str | None = None,
        include_unknown: bool = False,
    ) -> Iterator[tuple[str, dict[str, Any]]]:
        """`(run_id, detail)` for runs not in *seen_ids*. Index scan + per-run load encapsulated
        so derived views (AxisIndex) don't reinvent it. Dataset-filtered per `list_all`.
        """
        for entry in self.list_all(
            backend_id, dataset_name=dataset_name, include_unknown=include_unknown
        ):
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
        node_configs: list[tuple[str, dict[str, Any]]],
        *,
        dataset_name: str | None = None,
        include_unknown: bool = False,
    ) -> list[tuple[dict[str, Any], int]]:
        """Position-by-position prefix-equal match. `(entry, match_length)` sorted by match_length
        desc then item_count desc.
        """
        if not node_configs:
            return []

        scored: list[tuple[dict[str, Any], int]] = []
        for entry in self.list_all(
            backend_id, dataset_name=dataset_name, include_unknown=include_unknown
        ):
            stored = entry.get("node_configs")
            if not stored:
                continue
            match_len = 0
            for (n_want, c_want), stored_pair in zip(node_configs, stored, strict=False):
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

    # -- direct retrieval (the database-core view) -----------------------------

    def measurements_for_sample(
        self,
        backend_id: str,
        sample_id: int,
        *,
        run_ids: list[str] | None = None,
        dataset_name: str | None = None,
        include_unknown: bool = False,
    ) -> list[Measurement]:
        """Every measurement of one sample, across all configs. *run_ids* hint (from `Sample.run_ids`)
        skips the index scan; without it, walks every batch. Caller-supplied ids must already be
        dataset-scoped (true when sourced from `Sample.run_ids`).
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
                for entry in self.list_all(
                    backend_id, dataset_name=dataset_name, include_unknown=include_unknown
                )
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
        *,
        run_ids: set[str] | list[str] | None = None,
        dataset_name: str | None = None,
        include_unknown: bool = False,
    ) -> list[Measurement]:
        """Every measurement under configs matching *predicate*, across samples. Empty predicate → [].
        *run_ids* hint turns O(N) into O(K + matches); must be dataset-scoped at source.
        """
        if not predicate:
            return []

        if run_ids is not None:
            out: list[Measurement] = []
            for rid in run_ids:
                detail = self.load_by_id(backend_id, rid)
                if detail is None:
                    continue
                for item in detail.get("measurements", []):
                    out.append(_to_measurement(rid, detail, item))
            return out

        out = []
        for entry in self.list_all(
            backend_id, dataset_name=dataset_name, include_unknown=include_unknown
        ):
            stored = entry.get("node_configs")
            if not stored:
                continue
            if not _matches_subset(stored, predicate):
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
        node_configs: list[tuple[str, dict[str, Any]]],
        is_fatal: Callable[[dict[str, Any]], bool] | None = None,
        *,
        dataset_name: str | None = None,
        include_unknown: bool = False,
        min_grade: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Per-sample cache from prior runs sharing *node_configs*. Exact match → reuse all
        non-error; partial → prefix-trusted nodes only. `is_fatal` prevents a deprecated archive
        row shadowing a saved valid retry. *dataset_name* scopes reuse (same sample_id across
        datasets is a different problem). *min_grade* (`A`/`B`/`C`) drops runs whose provenance
        grade is below the floor — a clean-substrate read (e.g. the loop-improvement experiment)
        passes `A` to reuse only deliberately-explored measurements; the default `None` keeps
        every run, so ordinary scoring caching is unchanged.

        Operating consequence (the answer to "will changing a connector tunable re-score?"):
        `node_configs` carries each node's effective config INCLUDING `model`, so a change at
        node N breaks the prefix match at N — every sample whose pipeline ran PAST N is
        re-measured; only samples that short-circuited upstream (`terminated_at` in a trusted
        prefix node, e.g. cache/fuzzy) replay. So editing a node's model and minting a fresh
        campaign genuinely re-scores the LLM-path samples.
        """
        if not node_configs:
            return {}
        chain_len = len(node_configs)
        cache: dict[str, dict[str, Any]] = {}

        for entry, match_length in self.find_by_node_configs(
            backend_id,
            node_configs,
            dataset_name=dataset_name,
            include_unknown=include_unknown,
        ):
            if min_grade is not None and not meets_grade(entry_grade(entry), min_grade):
                continue
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
        return self._store_dir() / "prompt_aliases.json"

    def register_alias(self, backend_id: str, *hashes: str) -> None:
        """Link rendered_prompt_hashes as semantically equivalent; new hashes merge into any group
        that overlaps theirs.
        """
        hashes_set = {h for h in hashes if h}
        if len(hashes_set) < 2:
            return

        path = self._alias_path(backend_id)
        data = read_json_optional(path) or {"groups": []}
        groups: list[list[str]] = data["groups"]

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
        """Hash both sides → `register_alias`. No-op when either side is empty or hashes match."""
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
    fitness = item.get("fitness")
    return Measurement(
        run_id=run_id,
        content_hash=detail.get("content_hash", ""),
        sample_id=int(item.get("sample_id", -1)),
        query=item.get("query", ""),
        ground_truth=item.get("ground_truth", ""),
        predicted=item.get("predicted", ""),
        hit=bool(item.get("hit", False)),
        fitness=float(fitness) if fitness is not None else None,
        node_configs=node_configs,
        pipeline_data=item.get("pipeline_data") or {},
        created_at=detail.get("created_at", ""),
        run_scores=detail.get("scores") or {},
    )


__all__ = ["MeasurementArchive"]
