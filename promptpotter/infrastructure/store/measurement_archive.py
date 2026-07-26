"""Measurement archive — DB core. Append-only, content-addressed, cross-cycle/session/tenant.

Two views: by sample (`measurements_for_sample`) and by config (`measurements_for_config`).
Cache reuse → positional prefix-exact; discovery → `_matches_subset`. Sole source of truth —
derived views (AxisIndex, SampleIndex) refresh from `list_all`, not a parallel stream.

**Both files are append-only logs over `store/read_model.py`, folded last-wins.** The index
is `measurements/index.jsonl`, keyed by `content_hash`; each run's detail is
`measurements/runs/{run_id}.jsonl`, keyed by `k` — one `"run"` header row (rewritten whole
per save, so the fold always serves the latest) and one `"m:{sample_id}"` row per
measurement. A save appends only what is new: the scoring walk re-saves after every sample,
and rewriting the whole detail each time cost O(samples²) bytes (measured: 90 MB of archive
took 839 MB of writes; 67× worse at 200 samples).

No read-whole / O(n)-scan / rewrite-whole per save — and, since the loop reads the index
many times per round, none per READ either: `_live_rows` keeps the fold in memory and tails
only the bytes appended since the last read, re-folding whole only when the file shrinks or
is rewritten under it. Correctness rides on stat-ing the file every read, never on trusting
our own writes — see `_live_rows`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

from promptpotter.config.settings import DEFAULT_CONNECTOR_TYPE
from promptpotter.domain.measurement_provenance import entry_grade, meets_grade
from promptpotter.domain.sample import Measurement
from promptpotter.infrastructure.store.io import write_jsonl
from promptpotter.infrastructure.store.read_model import (
    append_row,
    compact,
    fold_jsonl,
    fold_jsonl_from,
)

logger = logging.getLogger(__name__)

# The detail log's fold key, and the one row that is not a measurement.
_FOLD_KEY = "k"
_HEADER_KEY = "run"
_DETAIL_SUFFIX = ".jsonl"


def _measurement_key(item: dict[str, Any]) -> str:
    """Fold key of one measurement row — its ``sample_id``, the cell's one identity.

    A row without one is a writer bug (every writer stamps it), but the archive is paid LLM
    spend: keying it by its own content keeps it in the log rather than dropping it, and it
    cannot collide with a real sample's key.
    """
    sid = item.get("sample_id")
    if isinstance(sid, int):
        return f"m:{sid}"
    digest = hashlib.blake2b(
        json.dumps(item, sort_keys=True, default=str).encode(), digest_size=8
    ).hexdigest()
    logger.warning("Measurement row without an int sample_id — keying by content (%s).", digest)
    return f"m:!{digest}"


def _summary(data: dict[str, Any]) -> dict[str, Any]:
    """Project a full run-detail dict onto the index summary line (the fields readers
    need without opening the detail file). Shared by :meth:`MeasurementArchive.save`
    and :meth:`MeasurementArchive.reindex` so the two can never drift."""
    return {
        "run_id": data["run_id"],
        "name": data.get("name", data["run_id"]),
        "dataset_name": data.get("dataset_name"),
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

    Tenant-global, self-contained under `measurements/`: the append-only index
    `measurements/index.jsonl` beside a `measurements/runs/` dir holding one
    `{run_id}.jsonl` detail log each. It sits beside `campaigns/` and `archive/` (the
    recycle bin), never inside `archive/` — it is a cross-campaign cache, not trash. Run
    logs are reached by explicit `run_id` (`load_by_id`) or via the index (`list_all`); only
    `reindex` globs the dir, so the index shares it safely.

    **`runs/` is load-bearing, not cosmetic.** The details used to sit directly in
    `measurements/`, so every scan of them carried a name filter (`.json` suffix AND a
    `hard_samples_` prefix blocklist) — and the only reason it did not also swallow
    `index.jsonl` was the accident that `".jsonl".endswith(".json")` is False. A dir holding
    nothing but detail logs retires the whole membership test for one suffix check.

    **Identity does not include the execution path.** A measurement is keyed by
    `content_hash(prompt, dataset, pipeline_params)` and reused by
    `PipelineSchema.node_configs()`, neither of which carries `backend_type`. So
    the archive is not backend-scoped at all — no read or write takes a
    `backend_id`, and repointing a dataset at a different connector (wire TermNorm
    → an in-process one, say) does NOT invalidate rows measured under the old
    one — it silently serves them. Change the connector and you must change the
    config the hash sees, or re-mint the campaign. (`dataset_snapshot_path` is the
    one exception: its FILENAME carries the backend, so it takes one.)
    """

    def __init__(self, base_dir: Path):
        self._base_dir = base_dir
        self._rows: dict[str, dict[str, Any]] | None = None
        self._stat: tuple[int, int] | None = None
        self._offset = 0

    # -- path helpers ---------------------------------------------------------

    @property
    def base_dir(self) -> Path:
        """The archive's root — its identity. A memo over archive-derived data keys on
        this: run_ids are content-addressed, but an L4 inner cycle runs in-process over a
        SANDBOXED archive, so run_id alone is not a unique key across live instances."""
        return self._base_dir

    def _store_dir(self) -> Path:
        return self._base_dir / "measurements"

    def _index_path(self) -> Path:
        return self._store_dir() / "index.jsonl"

    def _runs_dir(self) -> Path:
        return self._store_dir() / "runs"

    def _detail_path(self, run_id: str) -> Path:
        return self._runs_dir() / f"{run_id}{_DETAIL_SUFFIX}"

    # -- index read model -----------------------------------------------------

    def _invalidate(self) -> None:
        """Drop the memo — for the two ops that rewrite the index wholesale."""
        self._rows = None
        self._stat = None
        self._offset = 0

    def _live_rows(self) -> dict[str, dict[str, Any]]:
        """The folded index (last-wins by ``content_hash``), tailed rather than re-folded.

        Every read stats the file first. Unchanged ``(mtime_ns, size)`` serves the memo;
        a file that GREW is tailed from the last offset and merged (last-wins, so an
        appended row supersedes in place); anything else — shrunk, or same size with a
        new mtime, i.e. a `compact`/`reindex`/`write_jsonl` rewrote it — re-folds whole.

        The stat is what makes this safe across instances: an L4 inner cycle runs
        in-process with its own ``Stores`` over the same ``measurements/`` dir, so a memo
        trusting only its own writes would go blind to the sibling's appends.
        """
        path = self._index_path()
        try:
            st = path.stat()
        except FileNotFoundError:
            self._invalidate()
            return {}
        sig = (st.st_mtime_ns, st.st_size)
        if self._rows is not None and sig == self._stat:
            return self._rows
        if self._rows is not None and st.st_size > self._offset:
            fresh, self._offset = fold_jsonl_from(path, "content_hash", self._offset)
            self._rows.update(fresh)
        else:
            # Fold from 0 through the same primitive: it returns the newline-aligned
            # offset, so a crash-truncated trailing line stays pending instead of being
            # skipped forever once the writer completes it.
            self._rows, self._offset = fold_jsonl_from(path, "content_hash", 0)
        self._stat = sig
        return self._rows

    def dataset_snapshot_path(self, backend_id: str, dataset_name: str) -> Path:
        """Path of the per-(backend, dataset) hard-samples snapshot — the store owns
        its own layout, so the writer never reconstructs `measurements/…` inline."""
        return self._store_dir() / f"hard_samples_{backend_id}_{dataset_name}.json"

    # -- complete runs --------------------------------------------------------

    def append_run(
        self,
        run_id: str,
        data: dict[str, Any],
        new_measurements: Iterable[dict[str, Any]],
    ) -> Path:
        """Append *new_measurements* + a fresh header row to the run's detail log, and one
        index summary. *data* is the full run dict (`build_dataset_run_data`); its
        ``measurements`` key is the merged view, used for the header's derived fields
        (scores, provenance, item_count) and dropped from the row itself.

        The caller passes ONLY the rows it has not appended yet. The measurement rows
        already on disk are not rewritten — that is the whole point: the scoring walk calls
        this once per sample, and rewriting the accumulated detail each time is what cost
        O(samples²) bytes.

        Measurements land before the header, so the header is the commit marker: a crash
        mid-save leaves rows the old header does not yet count, never a header promising
        rows that are not there.

        A re-measure of the same `content_hash` under a *different* `run_id` orphans the old
        log (the index row is superseded, so no read ever reaches it); `reindex` GCs those.
        The common case — same `run_id` (same label) — appends into the same log, where
        last-wins by ``sample_id`` supersedes in place.
        """
        detail_path = self._detail_path(run_id)
        for item in new_measurements:
            append_row(detail_path, {_FOLD_KEY: _measurement_key(item), **item})
        header = {_FOLD_KEY: _HEADER_KEY, **{k: v for k, v in data.items() if k != "measurements"}}
        append_row(detail_path, header)

        append_row(self._index_path(), _summary(data))

        return detail_path

    def compact_run(self, run_id: str) -> bool:
        """Drop the run's superseded rows (dead headers, re-measured samples).

        ``factor=1`` is required, not a tuning choice: a walk of S samples leaves S header
        rows against ONE live one, so the default ``factor=2`` guard (rewrite only past 2× the
        live set) would never fire on a log whose live set is ``1 + S``.
        """
        return compact(self._detail_path(run_id), _FOLD_KEY, factor=1)

    def reset_run(self, run_id: str) -> None:
        """Discard the run's log entirely — the ``force_fresh`` truncation.

        Append-only does not overwrite, so a re-measure that means to REPLACE the stale rows
        (a connector fix the content hash cannot see) has to say so. Without this, an
        interrupted force-fresh pass leaves a franken-run: post-fix rows for the samples it
        reached, pre-fix rows for the rest, under one header, and nothing errors.
        """
        self._detail_path(run_id).unlink(missing_ok=True)

    def maintain_index(self) -> bool:
        """Drop superseded rows from the index when they've grown past the live set.

        `save` appends one summary row per call, and the scoring walk saves after every
        sample — so a run of S samples leaves S rows for one live entry. On the live
        archive that is 7909 rows for 688 runs: 91% dead weight, re-read by every cold
        fold. Compaction is the append-only log's own answer to that (`read_model`), it
        just had no caller on the loop path. Self-limiting: `compact` no-ops on a tight
        log, so calling this every run costs one fold and usually rewrites nothing.
        """
        if compact(self._index_path(), "content_hash"):
            self._invalidate()
            return True
        return False

    def load_by_id(self, run_id: str) -> dict[str, Any] | None:
        """Fold a run's detail log into the full run dict (no index scan); ``None`` if the log
        is absent or carries no header row yet."""
        return _fold_detail(self._detail_path(run_id))

    def detail_signatures(self) -> dict[str, tuple[int, int]]:
        """``{run_id: (mtime_ns, size)}`` for every run detail, in ONE directory scan.

        Lets a caller memoize a DERIVATION of the details (cheap to hold) instead of the
        details themselves (90 MB across 676 runs — caching those is what starves memory).
        A run_id is NOT immutable content: the scoring walk appends to the same log after
        every sample, so it grows under a reader, and anything cached off it must be
        revalidated. `scandir` carries the stat data from the directory read, so asking
        once for all of them beats a `stat` per run by an order of magnitude.
        """
        out: dict[str, tuple[int, int]] = {}
        try:
            with os.scandir(self._runs_dir()) as it:
                for e in it:
                    if not e.name.endswith(_DETAIL_SUFFIX):
                        continue
                    st = e.stat()
                    out[e.name[: -len(_DETAIL_SUFFIX)]] = (st.st_mtime_ns, st.st_size)
        except OSError:
            return out
        return out

    def restamp_dataset(self, old_name: str, new_name: str) -> int:
        """Rewrite every archive entry stamped *old_name* → *new_name* (index summary
        + the matching detail log's ``dataset_name``). Returns the count restamped.

        The measurement half of the dataset version-and-repoint migration
        (``application/datasets/dataset_replace.py``): when a dataset's data is
        archived under a ``-vN`` name, its prior campaigns' measurements move with
        it so dataset-scoped reuse + filtering stay truthful. Idempotent — only
        entries still stamped *old_name* are touched, so a re-run after a crash is
        a no-op. Each rename is one appended index row + one appended header row
        (last-wins on both logs) — no measurement is rewritten.
        """
        index_path = self._index_path()
        count = 0
        for entry in list(fold_jsonl(index_path, "content_hash").values()):
            if entry.get("dataset_name") != old_name:
                continue
            count += 1
            run_id = entry.get("run_id", "")
            detail = self.load_by_id(run_id) if run_id else None
            if detail is not None:
                # Appends the restamped header AND its index summary — one write path.
                self.append_run(run_id, {**detail, "dataset_name": new_name}, [])
            else:
                append_row(index_path, {**entry, "dataset_name": new_name})
        if count:
            compact(index_path, "content_hash")
            self._invalidate()
        return count

    def list_all(
        self,
        *,
        dataset_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Index entries (summaries), one fold of ``index.jsonl`` (last-wins by
        ``content_hash``). *dataset_name* scopes to one dataset (None = forensic/admin)."""
        entries = list(self._live_rows().values())
        if dataset_name is None:
            return entries
        return [e for e in entries if _entry_matches_dataset(e, dataset_name)]

    def reindex(self) -> dict[str, int]:
        """Rebuild ``index.jsonl`` from the detail logs and GC orphans — the append-only
        log's on-demand repair. Folds every ``runs/{run_id}.jsonl``, keeps the latest by
        ``created_at`` per ``content_hash``, rewrites a compacted index, then deletes the
        *superseded* logs (a re-measure under a new ``run_id`` orphans the old one).
        Returns ``{indexed, orphans_removed, details_scanned}``. Losing the index loses
        nothing — this reproduces it.

        GC is positive-identification-only: a log is deleted only if it folded to a
        measurement detail (carried a ``content_hash``) and lost to a newer run for that
        hash. A path it cannot read as a detail is left untouched — reindex never removes a
        path it can't explain.
        """
        parsed: list[tuple[Path, dict[str, Any]]] = []
        for path in self._runs_dir().glob(f"*{_DETAIL_SUFFIX}"):
            data = _fold_detail(path)
            if data is not None and "content_hash" in data:
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
        self._invalidate()
        winner_paths = {path for path, _ in winners.values()}
        orphans = 0
        for path, _ in parsed:
            if path not in winner_paths:
                path.unlink(missing_ok=True)
                path.with_suffix(path.suffix + ".lock").unlink(missing_ok=True)
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
        dataset_name: str,
        min_grade: str | None = None,
    ) -> dict[int, dict[str, Any]]:
        """Per-sample cache from prior runs sharing *node_configs*, keyed by ``sample_id``.
        Exact match → reuse all non-error; partial → prefix-trusted nodes only. `is_fatal`
        prevents a deprecated archive row shadowing a saved valid retry. *min_grade*
        (`A`/`B`/`C`) drops runs whose provenance grade is below the floor — a clean-substrate
        read (e.g. the loop-improvement experiment) passes `A` to reuse only deliberately-
        explored measurements; the default `None` keeps every run, so ordinary scoring caching
        is unchanged.

        *dataset_name* is REQUIRED. ``sample_id`` identifies a sample WITHIN a dataset, so a
        pooled slice (the ``None`` the index treats as forensic/admin) would serve one dataset's
        measurement under another's sample — the bleed ``test_integrity`` guards. Keying on the
        raw ``query`` text used to hide that, and bought a different silent collision instead:
        two samples that phrase the same question shared one cell, and a sample with an empty
        query was dropped from reuse entirely. The writer stamps ``sample_id`` on every
        measurement (``sample_measurement.py``), so it is the cell's one identity.

        Operating consequence (the answer to "will changing a connector tunable re-score?"):
        `node_configs` carries each node's effective config INCLUDING `model`, so a change at
        node N breaks the prefix match at N — every sample whose pipeline ran PAST N is
        re-measured; only samples that short-circuited upstream (`terminated_at` in a trusted
        prefix node, e.g. cache/fuzzy) replay. So editing a node's model and minting a fresh
        campaign genuinely re-scores the LLM-path samples.
        """
        if not dataset_name:
            raise ValueError("load_reusable_results requires a dataset_name — see the docstring")
        if not node_configs:
            return {}
        chain_len = len(node_configs)
        cache: dict[int, dict[str, Any]] = {}

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
                sid = item.get("sample_id")
                if not isinstance(sid, int) or item.get("predicted") == "ERROR":
                    continue
                if not is_full_match:
                    terminated_at = (item.get("pipeline_data") or {}).get("terminated_at", "")
                    if not (terminated_at and terminated_at in trusted_nodes):
                        continue
                existing = cache.get(sid)
                if (
                    existing is not None
                    and is_fatal is not None
                    and is_fatal(item)
                    and not is_fatal(existing)
                ):
                    continue
                cache[sid] = item
        return cache


def _fold_detail(path: Path) -> dict[str, Any] | None:
    """Fold one detail log back into the run dict its writer built.

    Last-wins per ``k``: the newest header row, and the newest row per ``sample_id`` in
    first-seen order. ``None`` when the log is absent or has no header yet — a headerless
    log is a walk that appended measurements and died before its first commit, and it must
    not read as a run (it has no scores).
    """
    rows = fold_jsonl(path, _FOLD_KEY)
    header = rows.pop(_HEADER_KEY, None)
    if header is None:
        return None
    data = {k: v for k, v in header.items() if k != _FOLD_KEY}
    data["measurements"] = [
        {k: v for k, v in row.items() if k != _FOLD_KEY} for row in rows.values()
    ]
    return data


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
