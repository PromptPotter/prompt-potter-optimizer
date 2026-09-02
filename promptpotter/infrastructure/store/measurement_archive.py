"""Measurement archive — DB core. **Nothing whole, in either direction**: a save appends only what
is new (the scoring walk re-saves per sample), and a read tails only the bytes since the last one."""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

from promptpotter.domain.measurement_provenance import entry_grade, meets_grade
from promptpotter.domain.sample import Measurement
from promptpotter.infrastructure.store.io import (
    read_bytes_optional,
    write_bytes,
    write_jsonl,
    write_text,
)
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
_COLD_SUFFIX = ".jsonl.gz"


def _measurement_key(item: dict[str, Any]) -> str:
    """A row without a ``sample_id`` is a writer bug, but the archive is paid LLM spend — keying it by
    its own content keeps it in the log instead of dropping it, and cannot collide with a real key."""
    sid = item.get("sample_id")
    if isinstance(sid, int):
        return f"m:{sid}"
    digest = hashlib.blake2b(
        json.dumps(item, sort_keys=True, default=str).encode(), digest_size=8
    ).hexdigest()
    logger.warning("Measurement row without an int sample_id — keying by content (%s).", digest)
    return f"m:!{digest}"


def _summary(data: dict[str, Any]) -> dict[str, Any]:
    """Shared by :meth:`MeasurementArchive.save` and :meth:`MeasurementArchive.reindex`, so the
    summary the two write can never drift.

    The defaulted ``.get(…)`` reads LOOK like tolerance for a drifted writer and are not: test
    fixtures call ``save`` with partial dicts, so tightening them to subscripts breaks the callers
    rather than catching one. The subscripted keys above are the ones every writer supplies."""
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
    val = entry.get("dataset_name")
    return val if isinstance(val, str) and val else None


def _entry_matches_dataset(entry: dict[str, Any], dataset_name: str | None) -> bool:
    """``None`` ⇒ everything (forensic/admin). An entry carrying no ``dataset_name`` belongs to no
    dataset, so it matches no concrete name."""
    return dataset_name is None or _entry_dataset(entry) == dataset_name


class MeasurementArchive:
    """**Identity does not include the execution path.** A measurement is keyed by content, and no
    read or write takes a ``backend_id`` — repointing a dataset at another connector serves the old rows."""

    def __init__(self, base_dir: Path):
        self._base_dir = base_dir
        self._rows: dict[str, dict[str, Any]] | None = None
        self._stat: tuple[int, int] | None = None
        self._offset = 0

    # -- path helpers ---------------------------------------------------------

    @property
    def base_dir(self) -> Path:
        """The archive's root — its identity. run_ids are content-addressed, but an L4 inner cycle runs
        in-process over a SANDBOXED archive, so run_id alone is not unique across live instances."""
        return self._base_dir

    def _store_dir(self) -> Path:
        return self._base_dir / "measurements"

    def _index_path(self) -> Path:
        return self._store_dir() / "index.jsonl"

    def _runs_dir(self) -> Path:
        return self._store_dir() / "runs"

    def derived_dir(self) -> Path:
        """Read models folded FROM these measurements live under them — the derivation is the
        archive's own, so the path is not something a view module gets to spell for itself."""
        return self._store_dir() / "derived"

    def _detail_path(self, run_id: str) -> Path:
        return self._runs_dir() / f"{run_id}{_DETAIL_SUFFIX}"

    # -- index read model -----------------------------------------------------

    def _invalidate(self) -> None:
        self._rows = None
        self._stat = None
        self._offset = 0

    def _live_rows(self) -> dict[str, dict[str, Any]]:
        """Tailed rather than re-folded, and every read STATS the file first — an L4 inner cycle runs
        in-process over the same dir, so a memo trusting only its own writes goes blind to its appends."""
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

    # -- complete runs --------------------------------------------------------

    def append_run(
        self,
        run_id: str,
        data: dict[str, Any],
        new_measurements: Iterable[dict[str, Any]],
    ) -> Path:
        """The caller passes ONLY the rows it has not appended yet; rewriting the accumulated detail per
        sample is O(samples²). Measurements land before the header, so the header is the commit marker."""
        detail_path = self._detail_path(run_id)
        for item in new_measurements:
            append_row(detail_path, {_FOLD_KEY: _measurement_key(item), **item})
        header = {_FOLD_KEY: _HEADER_KEY, **{k: v for k, v in data.items() if k != "measurements"}}
        append_row(detail_path, header)

        append_row(self._index_path(), _summary(data))

        return detail_path

    def compact_run(self, run_id: str) -> bool:
        """``factor=1`` is required, not a tuning choice: a walk of S samples leaves S header rows
        against ONE live one, so the default 2× guard would never fire."""
        return compact(self._detail_path(run_id), _FOLD_KEY, factor=1)

    # -- field compaction: the cold store -------------------------------------
    #
    # Dropping a field from a measurement row destroys paid LLM spend, so the archive does not
    # offer a way to drop one. It offers a way to MOVE one: the payload lands gzipped beside the
    # runs and `restore_cold` puts it back. Deleting the cold store is a separate act, and the
    # only one that is irreversible.

    def _cold_dir(self) -> Path:
        return self._store_dir() / "cold"

    def cold_path(self, run_id: str) -> Path:
        return self._cold_dir() / f"{run_id}{_COLD_SUFFIX}"

    def has_cold(self, run_id: str) -> bool:
        return self.cold_path(run_id).exists()

    def detail_lines(self, run_id: str) -> list[str]:
        """The detail log's raw lines, IN ORDER. Order is load-bearing — the file folds last-wins on
        ``k``, so a caller rewriting it must preserve both the sequence and the unparseable lines."""
        path = self._detail_path(run_id)
        try:
            with path.open(encoding="utf-8") as fh:
                return fh.readlines()
        except FileNotFoundError:
            return []

    def replace_detail(self, run_id: str, lines: Iterable[str]) -> int:
        """Swap the whole detail log atomically; returns the byte count written. ``append`` is not
        crash-atomic and neither is a truncate-then-write, so a rewrite never happens in place."""
        content = "".join(lines)
        write_text(self._detail_path(run_id), content)
        return len(content.encode("utf-8"))

    @staticmethod
    def _encode_cold(rows: Iterable[dict[str, Any]]) -> bytes:
        blob = "\n".join(json.dumps(r, separators=(",", ":"), default=str) for r in rows)
        return gzip.compress(blob.encode("utf-8"), 6)

    def cold_size(self, rows: Iterable[dict[str, Any]]) -> int:
        """What :meth:`write_cold` WOULD cost, without writing it — so a dry run reports the same
        number the apply produces rather than a ratio someone guessed."""
        return len(self._encode_cold(rows))

    def cold_bytes_on_disk(self, run_id: str) -> int:
        """Bytes the run's cold payload occupies; 0 when absent."""
        try:
            return self.cold_path(run_id).stat().st_size
        except OSError:
            return 0

    def write_cold(self, run_id: str, rows: Iterable[dict[str, Any]]) -> int:
        """Persist the moved payload; returns bytes on disk. Written WHOLE rather than appended: a
        second compaction of the same run must not leave two generations under one name."""
        data = self._encode_cold(rows)
        write_bytes(self.cold_path(run_id), data)
        return len(data)

    def read_cold(self, run_id: str) -> list[dict[str, Any]] | None:
        """``None`` when the run was never compacted — distinct from a compaction that moved
        nothing, which reads as an empty list."""
        raw = read_bytes_optional(self.cold_path(run_id))
        if raw is None:
            return None
        text = gzip.decompress(raw).decode("utf-8")
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def drop_cold(self, run_id: str) -> int:
        """Delete one run's cold payload for good; returns the bytes reclaimed, 0 if absent."""
        path = self.cold_path(run_id)
        try:
            size = path.stat().st_size
        except OSError:
            return 0
        path.unlink(missing_ok=True)
        return size

    def reset_run(self, run_id: str) -> None:
        """Append-only does not overwrite, so a re-measure meaning to REPLACE has to say so — an
        interrupted force-fresh otherwise leaves post-fix and pre-fix rows under one header."""
        self._detail_path(run_id).unlink(missing_ok=True)

    def maintain_index(self) -> bool:
        """Self-limiting — ``compact`` no-ops on a tight log, so calling this every run costs one fold
        and usually rewrites nothing."""
        if compact(self._index_path(), "content_hash"):
            self._invalidate()
            return True
        return False

    def load_by_id(self, run_id: str) -> dict[str, Any] | None:
        """Fold a run's detail log into the full run dict (no index scan); ``None`` if the log
        is absent or carries no header row yet."""
        return _fold_detail(self._detail_path(run_id))

    def detail_signatures(self) -> dict[str, tuple[int, int]]:
        """Lets a caller memoize a DERIVATION of the details rather than the details themselves. A run_id
        is NOT immutable content — the scoring walk appends to the same log, so it grows under a reader."""
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
        """Idempotent: only entries still stamped *old_name* are touched, so a re-run after a crash is a
        no-op. Each rename is one appended row per log — no measurement is rewritten."""
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
        """GC is positive-identification-only: a log is deleted only if it folded to a detail carrying a
        ``content_hash`` and lost to a newer run. Losing the index loses nothing — this reproduces it."""
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
        """Caller-supplied *run_ids* must already be dataset-scoped (true when sourced from
        ``Sample.run_ids``); without the hint this walks every batch."""
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
        """*dataset_name* is REQUIRED — ``sample_id`` identifies a sample WITHIN a dataset, so a pooled
        slice serves one dataset's measurement under another's. A config change at node N re-measures past N."""
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
                    terminal_node = (item.get("pipeline_data") or {}).get("terminal_node", "")
                    if not (terminal_node and terminal_node in trusted_nodes):
                        continue
                # FIRST wins: `find_by_node_configs` sorted best-first, so assigning
                # unconditionally would serve the row matching the FEWEST nodes. The one upgrade
                # allowed is replacing a fatal row, which is not an answer, with a live one.
                existing = cache.get(sid)
                if existing is not None and not (
                    is_fatal is not None and is_fatal(existing) and not is_fatal(item)
                ):
                    continue
                cache[sid] = item
        return cache


def _fold_detail(path: Path) -> dict[str, Any] | None:
    """Last-wins per key. ``None`` when the log has no header yet — a headerless log is a walk that
    appended measurements and died before its first commit, and it must not read as a run."""
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
        fitness=float(fitness) if fitness is not None else None,
        node_configs=node_configs,
        pipeline_data=item.get("pipeline_data") or {},
        created_at=detail.get("created_at", ""),
    )


__all__ = ["MeasurementArchive"]
