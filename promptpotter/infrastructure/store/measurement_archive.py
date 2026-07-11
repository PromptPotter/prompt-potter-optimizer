"""Measurement archive — DB core. Append-only, content-addressed, cross-cycle/session/tenant.

Two views: by sample (`measurements_for_sample`) and by config (`measurements_for_config`).
Cache reuse → positional prefix-exact; discovery → `_matches_subset`. Sole source of truth —
derived views (AxisIndex, SampleIndex) refresh from `list_all`, not a parallel stream.

The index is `measurements/index.jsonl` (`store/read_model.py`): a save is one appended
line, last-wins by `content_hash`; a read folds the file once. `reindex` rebuilds it from
the detail files (and GCs orphaned ones). No read-whole / O(n)-scan / rewrite-whole per save.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from promptpotter.config.settings import DEFAULT_CONNECTOR_TYPE
from promptpotter.domain.measurement_provenance import entry_grade, meets_grade
from promptpotter.domain.sample import Measurement
from promptpotter.infrastructure.store.io import (
    read_json_optional,
    write_json,
    write_jsonl,
)
from promptpotter.infrastructure.store.read_model import append_row, compact, fold_jsonl


def _summary(data: dict[str, Any]) -> dict[str, Any]:
    """Project a full run-detail dict onto the index summary line (the fields readers
    need without opening the detail file). Shared by :meth:`MeasurementArchive.save`
    and :meth:`MeasurementArchive.reindex` so the two can never drift."""
    return {
        "run_id": data["run_id"],
        "name": data.get("name", data["run_id"]),
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


def _entry_matches_dataset(entry: dict[str, Any], dataset_name: str | None) -> bool:
    """`None` ⇒ everything (forensic/admin). A concrete name ⇒ that dataset's entries.

    An entry carrying no ``dataset_name`` belongs to no dataset, so it matches no concrete name.
    """
    return dataset_name is None or _entry_dataset(entry) == dataset_name


class MeasurementArchive:
    """File I/O for the measurement store — the DB core, NOT the recycle bin.

    Tenant-global, self-contained under `measurements/`: run-detail files
    `measurements/{run_id}.json` and the append-only index `measurements/index.jsonl`
    live together. It sits beside `campaigns/` and `archive/` (the recycle bin),
    never inside `archive/` — it is a cross-campaign cache, not trash. Run files are
    reached by explicit `run_id` (`load_by_id`) or via the index (`list_all`); only
    `reindex` globs the dir, so the index shares it safely.

    **Identity does not include the execution path.** A measurement is keyed by
    `content_hash(prompt, dataset, pipeline_params)` and reused by
    `PipelineSchema.node_configs()`, neither of which carries `backend_type`. So
    the archive is not backend-scoped at all — no read or write takes a
    `backend_id`, and repointing a dataset at a different connector (wire TermNorm
    → in-process `llm_only`, say) does NOT invalidate rows measured under the old
    one — it silently serves them. Change the connector and you must change the
    config the hash sees, or re-mint the campaign. (`dataset_snapshot_path` is the
    one exception: its FILENAME carries the backend, so it takes one.)
    """

    def __init__(self, base_dir: Path):
        self._base_dir = base_dir

    # -- path helpers ---------------------------------------------------------

    def _store_dir(self) -> Path:
        return self._base_dir / "measurements"

    def _index_path(self) -> Path:
        return self._store_dir() / "index.jsonl"

    def dataset_snapshot_path(self, backend_id: str, dataset_name: str) -> Path:
        """Path of the per-(backend, dataset) hard-samples snapshot — the store owns
        its own layout, so the writer never reconstructs `measurements/…` inline."""
        return self._store_dir() / f"hard_samples_{backend_id}_{dataset_name}.json"

    # -- complete runs --------------------------------------------------------

    def save(
        self,
        run_id: str,
        data: dict[str, Any],
    ) -> Path:
        """Write detail + append the index summary. *data* needs `run_id`, `content_hash`,
        `scores`. Detail = atomic write; index = one appended line (last-wins by
        `content_hash`) — no read, no rewrite, no fold.

        A re-measure of the same `content_hash` under a *different* `run_id` orphans the
        old detail file (the index row is superseded, so no read ever reaches it); `reindex`
        GCs those. The common case — same `run_id` (same label) — overwrites the detail file
        in place, so nothing orphans.

        The save path never folds — compaction (dropping superseded lines) is on-demand via
        `reindex`, not paid per write.
        """
        detail_path = self._store_dir() / f"{run_id}.json"
        write_json(detail_path, data)

        append_row(self._index_path(), _summary(data))

        return detail_path

    def load_by_id(self, run_id: str) -> dict[str, Any] | None:
        """Load a run detail file directly by run_id (no index scan)."""
        return read_json_optional(self._store_dir() / f"{run_id}.json")

    def restamp_dataset(self, old_name: str, new_name: str) -> int:
        """Rewrite every archive entry stamped *old_name* → *new_name* (index summary
        + the matching detail file's ``dataset_name``). Returns the count restamped.

        The measurement half of the dataset version-and-repoint migration
        (``application/datasets/dataset_replace.py``): when a dataset's data is
        archived under a ``-vN`` name, its prior campaigns' measurements move with
        it so dataset-scoped reuse + filtering stay truthful. Idempotent — only
        entries still stamped *old_name* are touched, so a re-run after a crash is
        a no-op. Each rename is one appended index row (last-wins by
        ``content_hash``), then a single compaction, mirroring :meth:`save`.
        """
        index_path = self._index_path()
        count = 0
        for entry in list(fold_jsonl(index_path, "content_hash").values()):
            if entry.get("dataset_name") != old_name:
                continue
            renamed = {**entry, "dataset_name": new_name}
            append_row(index_path, renamed)
            count += 1
            run_id = entry.get("run_id", "")
            detail = self.load_by_id(run_id) if run_id else None
            if detail is not None and detail.get("dataset_name") == old_name:
                detail["dataset_name"] = new_name
                write_json(self._store_dir() / f"{run_id}.json", detail)
        if count:
            compact(index_path, "content_hash")
        return count

    def list_all(
        self,
        *,
        dataset_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Index entries (summaries), one fold of ``index.jsonl`` (last-wins by
        ``content_hash``). *dataset_name* scopes to one dataset (None = forensic/admin)."""
        entries = list(fold_jsonl(self._index_path(), "content_hash").values())
        if dataset_name is None:
            return entries
        return [e for e in entries if _entry_matches_dataset(e, dataset_name)]

    def reindex(self) -> dict[str, int]:
        """Rebuild ``index.jsonl`` from the detail files and GC orphans — the append-only
        log's on-demand repair. Reads every ``{run_id}.json``, keeps the latest by
        ``created_at`` per ``content_hash``, rewrites a compacted index, then deletes the
        *superseded* detail files (a re-measure under a new ``run_id`` orphans the old one).
        Returns ``{indexed, orphans_removed, details_scanned}``. Losing the index loses
        nothing — this reproduces it.

        GC is positive-identification-only: a file is deleted only if it parsed as a
        measurement detail (carried a ``content_hash``) and lost to a newer run for that
        hash. A file it cannot read as a detail is left untouched — reindex never removes a
        path it can't explain.
        """
        store = self._store_dir()
        # ``glob("*.json")`` never matches the ``.jsonl`` index; the positive-ID pass below
        # skips any other non-detail file (no ``content_hash``), so no name allowlist is needed.
        candidates = [p for p in store.glob("*.json") if not p.name.startswith("hard_samples_")]
        parsed: list[tuple[Path, dict[str, Any]]] = []
        for path in candidates:
            data = read_json_optional(path)
            if isinstance(data, dict) and "content_hash" in data:
                parsed.append((path, data))

        winners: dict[str, tuple[Path, dict[str, Any]]] = {}
        for path, data in parsed:
            ch = data["content_hash"]
            prev = winners.get(ch)
            if prev is None or str(data.get("created_at", "")) >= str(
                prev[1].get("created_at", "")
            ):
                winners[ch] = (path, data)

        write_jsonl(self._index_path(), [_summary(d) for _, d in winners.values()])
        winner_paths = {path for path, _ in winners.values()}
        orphans = 0
        for path, _ in parsed:
            if path not in winner_paths:
                path.unlink(missing_ok=True)
                orphans += 1
        return {
            "indexed": len(winners),
            "orphans_removed": orphans,
            "details_scanned": len(parsed),
        }

    def load_since(
        self,
        seen_ids: set[str],
        *,
        dataset_name: str | None = None,
    ) -> Iterator[tuple[str, dict[str, Any]]]:
        """`(run_id, detail)` for runs not in *seen_ids*. Index scan + per-run load encapsulated
        so derived views (AxisIndex) don't reinvent it. Dataset-filtered per `list_all`.
        """
        for entry in self.list_all(dataset_name=dataset_name):
            run_id = entry["run_id"]
            if run_id in seen_ids:
                continue
            detail = self.load_by_id(run_id)
            if detail is None:
                continue
            yield run_id, detail

    def find_by_node_configs(
        self,
        node_configs: list[tuple[str, dict[str, Any]]],
        *,
        dataset_name: str | None = None,
    ) -> list[tuple[dict[str, Any], int]]:
        """Position-by-position prefix-equal match. `(entry, match_length)` sorted by match_length
        desc then item_count desc.
        """
        if not node_configs:
            return []

        scored: list[tuple[dict[str, Any], int]] = []
        for entry in self.list_all(dataset_name=dataset_name):
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
        sample_id: int,
        *,
        run_ids: list[str] | None = None,
        dataset_name: str | None = None,
    ) -> list[Measurement]:
        """Every measurement of one sample, across all configs. *run_ids* hint (from `Sample.run_ids`)
        skips the index scan; without it, walks every batch. Caller-supplied ids must already be
        dataset-scoped (true when sourced from `Sample.run_ids`).
        """
        if run_ids is not None:
            sources: Iterator[tuple[str, dict[str, Any]]] = (
                (rid, detail) for rid in run_ids if (detail := self.load_by_id(rid)) is not None
            )
        else:
            sources = (
                (entry["run_id"], detail)
                for entry in self.list_all(dataset_name=dataset_name)
                if (detail := self.load_by_id(entry["run_id"])) is not None
            )

        out: list[Measurement] = []
        for run_id, detail in sources:
            for item in detail.get("measurements", []):
                if item.get("sample_id") == sample_id:
                    out.append(_to_measurement(run_id, detail, item))
        return out

    def measurements_for_config(
        self,
        predicate: dict[str, dict[str, Any]],
        *,
        run_ids: set[str] | list[str] | None = None,
        dataset_name: str | None = None,
    ) -> list[Measurement]:
        """Every measurement under configs matching *predicate*, across samples. Empty predicate → [].
        *run_ids* hint turns O(N) into O(K + matches); must be dataset-scoped at source.
        """
        if not predicate:
            return []

        if run_ids is not None:
            out: list[Measurement] = []
            for rid in run_ids:
                detail = self.load_by_id(rid)
                if detail is None:
                    continue
                for item in detail.get("measurements", []):
                    out.append(_to_measurement(rid, detail, item))
            return out

        out = []
        for entry in self.list_all(dataset_name=dataset_name):
            stored = entry.get("node_configs")
            if not stored:
                continue
            if not _matches_subset(stored, predicate):
                continue
            run_id = entry["run_id"]
            detail = self.load_by_id(run_id)
            if detail is None:
                continue
            for item in detail.get("measurements", []):
                out.append(_to_measurement(run_id, detail, item))
        return out

    def load_reusable_results(
        self,
        node_configs: list[tuple[str, dict[str, Any]]],
        is_fatal: Callable[[dict[str, Any]], bool] | None = None,
        *,
        dataset_name: str | None = None,
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
            node_configs,
            dataset_name=dataset_name,
        ):
            if min_grade is not None and not meets_grade(entry_grade(entry), min_grade):
                continue
            detail = self.load_by_id(entry["run_id"])
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
