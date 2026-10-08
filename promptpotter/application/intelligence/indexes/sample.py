from __future__ import annotations

import logging
from collections import Counter, defaultdict
from collections.abc import KeysView
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.scoring.formula import rescore_results
from promptpotter.application.scoring.metrics import CellFold, fold_cells
from promptpotter.domain.measurement_provenance import entry_grade
from promptpotter.domain.results_health import UNKNOWN_STEP, terminal_node
from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import (
    CellScorer,
    QueryMeasurement,
    is_graded,
    is_hit,
    is_unscored,
)
from promptpotter.infrastructure.store import archive_queries
from promptpotter.shared.errors import is_error_result
from promptpotter.shared.hashing import shapes_optimizer_prompt
from promptpotter.shared.instrument import MeasurementRole

if TYPE_CHECKING:
    from promptpotter.infrastructure.store.stores import Stores

logger = logging.getLogger(__name__)


def _graded_reading(
    detail: dict[str, Any], scorer: CellScorer
) -> tuple[CellFold | None, str | None]:
    """Grade *detail*'s rows IN PLACE — the sample index reads them next — and fold them; ``None``
    and the reason where one row cannot be graded under *scorer*."""
    rows = rescore_results(detail.get("measurements") or [], scorer)
    if unscored := next((r for r in rows if is_unscored(r)), None):
        return None, unscored.get("unscored")
    return fold_cells(cast("list[QueryMeasurement]", rows)), None


@dataclass
class SampleRecord:
    query: str
    sample_id: int
    hit_rate: float
    variance: float


@dataclass
class FailureCluster:
    failure_mode: str
    sample_count: int
    fraction: float


class SampleIndex:
    """Per-sample and per-run derived view over the archive — the bench's: the scoring gateway
    reads each cell's degradation history off it, and an optimizer may read it as evidence.
    ``_seen_runs`` is a delta cursor that SURVIVES the process — the per-run derivation is
    persisted and replayed — and both paths mutate through :meth:`replay_row` so they cannot drift.

    *sample_ids* is the reading campaign's search pool, applied at replay so the persisted fold
    stays whole for every reader: a bench-set row must reach no panel. ``None`` admits all."""

    def __init__(self, *, sample_ids: frozenset[int] | None) -> None:
        self._admitted = sample_ids
        # Every id a replayed row introduced, admitted or not — what `ingest_run` derives
        # `new_samples` against, so the persisted fold does not depend on who wrote it.
        self._introduced: set[int] = set()
        self._samples: dict[int, Sample] = {}
        self._seen_runs: set[str] = set()
        self._hits: dict[int, list[bool]] = defaultdict(list)
        self._hit_run_ids: dict[int, list[str]] = defaultdict(list)
        self._failure_modes: dict[int, list[str]] = defaultdict(list)
        self._degradation_counts: dict[int, int] = defaultdict(int)
        # Cache for derived query records; cleared on ingest.
        self._cache_records: list[SampleRecord] | None = None
        # Archived runs this formula cannot score (they predate a term it names). Bound once,
        # read by BOTH halves of `refresh` — the per-sample ingest and the run snapshot.
        self._unscoreable_runs: set[str] = set()
        # run_id -> (the detail signature it was read at, ``fold_cells`` over the run's rows graded
        # under the refreshing scorer).
        self._readings: dict[str, tuple[list[int], CellFold]] = {}
        # Whether the persisted per-run fold was replayed instead of re-derived. Decides
        # append-vs-replace when this refresh writes back; `None` until the first refresh.
        self._fold_seeded: bool | None = None
        # `(archive entry, its reading)` for every run an optimizer may learn from, as of the last
        # refresh, and how many refreshes that has been — so a reader folds each one once.
        self.runs: list[tuple[dict[str, Any], CellFold]] = []
        self.generation = 0

    def register(self, sample: Sample) -> None:
        self._samples[sample.id] = sample

    def sample(self, sample_id: int) -> Sample | None:
        return self._samples.get(sample_id)

    def sample_ids(self) -> KeysView[int]:
        """Live view of registered sample ids — iterates without copying."""
        return self._samples.keys()

    def ingest_run(self, run_detail: dict[str, Any]) -> dict[str, Any]:
        """Derive an archive entry into the index, RETURNING the derived row — which the archive persists, so a later process
        rebuilds without re-scoring every detail file. It carries only what ``replay_row`` consumes, never the measurements."""
        run_id = run_detail.get("run_id", "")
        cells: list[list[Any]] = []
        new_samples: list[list[Any]] = []
        seen_new: set[int] = set()

        for item in run_detail.get("measurements", []):
            sid = item.get("sample_id")
            if sid is None:
                continue
            if sid not in self._introduced and sid not in seen_new:
                seen_new.add(sid)
                new_samples.append([sid, item.get("query", ""), item.get("ground_truth", "")])

            hit = is_hit(item.get("fitness"))
            pd = item.get("pipeline_data") or {}
            degraded = bool((pd.get("diagnostics") or {}).get("warnings"))
            # `None` means "contributes no failure mode" — an error result is not a
            # bottleneck reading, and neither is a hit.
            failure_mode = (
                None if (hit or is_error_result(item)) else terminal_node(item) or UNKNOWN_STEP
            )
            cells.append([sid, hit, degraded, failure_mode])

        row: dict[str, Any] = {"run_id": run_id, "cells": cells, "new_samples": new_samples}
        self.replay_row(row)
        return row

    def replay_row(self, row: dict[str, Any]) -> None:
        """Apply one derived row — the SOLE mutation path, so the live derivation above and
        the on-disk replay cannot drift into disagreeing about what a run contributed."""
        run_id = row.get("run_id") or ""
        admitted = self._admitted

        for sid, query, ground_truth in row.get("new_samples") or []:
            self._introduced.add(sid)
            if sid not in self._samples and (admitted is None or sid in admitted):
                self.register(Sample(id=sid, query=query, ground_truth=ground_truth))

        for sid, hit, degraded, failure_mode in row.get("cells") or []:
            if admitted is not None and sid not in admitted:
                continue
            self._hits[sid].append(hit)
            if hit and run_id:
                self._hit_run_ids[sid].append(run_id)
            if degraded:
                self._degradation_counts[sid] += 1
            if failure_mode is not None:
                self._failure_modes[sid].append(failure_mode)
            sample = self._samples.get(sid)
            if sample is not None and run_id and run_id not in sample.run_ids:
                sample.run_ids.append(run_id)

        self._cache_records = None

    def hits(self, sample_id: int) -> list[bool]:
        return self._hits.get(sample_id, [])

    def failure_modes(self, sample_id: int) -> list[str]:
        return self._failure_modes.get(sample_id, [])

    def degradation_count(self, sample_id: int) -> int:
        return self._degradation_counts.get(sample_id, 0)

    def degradation_rate(self, sample_id: int) -> float:
        n = len(self._hits.get(sample_id, []))
        if n == 0:
            return 0.0
        return self._degradation_counts.get(sample_id, 0) / n

    def records(self) -> list[SampleRecord]:
        """Build per-sample SampleRecord list, cached until next ingest."""
        if self._cache_records is not None:
            return self._cache_records
        records = []
        for sid, hits in sorted(self._hits.items()):
            if not hits:
                continue
            hit_rate = sum(hits) / len(hits)
            variance = hit_rate * (1 - hit_rate)
            sample = self._samples.get(sid)
            query = sample.query if sample else ""
            records.append(
                SampleRecord(
                    query=query,
                    sample_id=sid,
                    hit_rate=round(hit_rate, 4),
                    variance=round(variance, 4),
                )
            )
        self._cache_records = records
        return records

    def dead(
        self,
        *,
        min_observations: int = 1,
        include_always_hit: bool = True,
        include_always_miss: bool = True,
    ) -> list[SampleRecord]:
        out: list[SampleRecord] = []
        for r in self.records():
            if len(self._hits.get(r.sample_id, [])) < min_observations:
                continue
            if (include_always_miss and r.hit_rate == 0.0) or (
                include_always_hit and r.hit_rate == 1.0
            ):
                out.append(r)
        return out

    def discriminating(self, min_variance: float = 0.1) -> list[SampleRecord]:
        return [r for r in self.records() if r.variance >= min_variance]

    def persistent_failures(self, min_streak: int = 3) -> list[SampleRecord]:
        records = []
        for r in self.records():
            hits = self._hits.get(r.sample_id, [])
            if len(hits) >= min_streak and not any(hits[-min_streak:]):
                records.append(r)
        records.sort(key=lambda r: r.hit_rate)
        return records

    @shapes_optimizer_prompt
    def rare_hit_samples(
        self,
        *,
        max_hits: int = 3,
        min_observations: int = 10,
    ) -> list[tuple[int, str, int, int, list[str]]]:
        """Samples hit by ≤ ``max_hits`` candidates — each rare hit is a RECIPE POINTER to the run that cracked a
        chronically-failing sample. ``min_observations`` filters out the cold start, where every sample looks rare."""
        out: list[tuple[int, str, int, int, list[str]]] = []
        for sid, hits in self._hits.items():
            total = len(hits)
            if total < min_observations:
                continue
            hit_count = sum(1 for h in hits if h)
            if hit_count > max_hits:
                continue
            sample = self._samples.get(sid)
            query = (sample.query if sample else "").replace("\n", " ").strip()[:60]
            run_ids = list(self._hit_run_ids.get(sid, []))
            out.append((sid, query, hit_count, total, run_ids))
        out.sort(key=lambda t: (t[2], -t[3]))
        return out

    def failure_clusters(self, max_clusters: int = 5) -> list[FailureCluster]:
        mode_samples: dict[str, list[int]] = defaultdict(list)
        for sid, modes in self._failure_modes.items():
            if modes:
                dominant = Counter(modes).most_common(1)[0][0]
                mode_samples[dominant].append(sid)

        total = sum(len(xs) for xs in mode_samples.values())
        clusters = []
        for mode, sids in sorted(mode_samples.items(), key=lambda x: -len(x[1])):
            clusters.append(
                FailureCluster(
                    failure_mode=mode,
                    sample_count=len(sids),
                    fraction=len(sids) / total if total else 0.0,
                )
            )
        return clusters[:max_clusters]

    def bottleneck_distribution(self) -> dict[str, float]:
        """``{terminal_node: fraction_of_failures}``."""
        counts: dict[str, int] = defaultdict(int)
        total = 0
        for modes in self._failure_modes.values():
            for mode in modes:
                counts[mode] += 1
                total += 1
        if total == 0:
            return {}
        return {step: count / total for step, count in sorted(counts.items(), key=lambda x: -x[1])}

    def mark_seen(self, run_id: str) -> None:
        self._seen_runs.add(run_id)

    # ----- ingest / refresh -----

    def _fold_run(
        self, run_id: str, detail: dict[str, Any], scorer: CellScorer, stamp: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        """The WHOLE run goes on the first ungradable row: a per-row skip would fold a partial run
        under a ``scorer_id`` claiming it scored entire, and no reader could tell them apart."""
        reading, unscored = _graded_reading(detail, scorer)
        self.mark_seen(run_id)
        # Each graded cell's objective, by ROW: what a δ ruler is fit on, so the launch that fits
        # one reads it here instead of re-grading every detail (`hard_sample_archive`).
        graded = [
            [int(sid), float(row["objective"])]
            for row in detail.get("measurements") or []
            if (sid := row.get("sample_id")) is not None and is_graded(row)
        ]
        stamp = {**stamp, "graded": graded}
        if reading is None:
            self._unscoreable_runs.add(run_id)
            return {"run_id": run_id, "unscoreable": True, **stamp}, unscored
        self._readings[run_id] = (stamp["sig"], reading)
        return {**self.ingest_run(detail), "reading": reading, **stamp}, None

    def _seed_from_fold(
        self, stores: Stores, dataset_name: str | None, scorer: CellScorer, formula_key: str
    ) -> bool:
        """``True`` if the fold is trusted: another formula's, or one naming a run the archive
        lacks, is rejected whole. A grown run is re-derived IN PLACE, the replay order kept."""
        if not dataset_name:
            return False
        rows = archive_queries.sample_fold_rows(stores, dataset_name=dataset_name)
        if not rows:
            return False

        signatures = archive_queries.run_signatures(stores)
        if any(r.get("fk") != formula_key or r.get("run_id") not in signatures for r in rows):
            return False

        grown = 0
        for at, row in enumerate(rows):
            run_id = row["run_id"]
            sig = list(signatures[run_id])
            if sig != list(row.get("sig") or ()) or "graded" not in row:
                detail = archive_queries.load_run(stores, run_id)
                if detail is None:
                    continue
                stamp = {"fk": formula_key, "sig": sig}
                rows[at], _ = self._fold_run(run_id, detail, scorer, stamp)
                grown += 1
            elif row.get("unscoreable"):
                self._unscoreable_runs.add(run_id)
                self.mark_seen(run_id)
            else:
                self.replay_row(row)
                self._readings[run_id] = (row["sig"], row["reading"])
                self.mark_seen(run_id)
        if grown:
            archive_queries.write_sample_fold(
                stores, dataset_name=dataset_name, rows=rows, append=False
            )
        logger.debug("SampleIndex seeded %d run(s) from the persisted fold", len(rows))
        return True

    def refresh(
        self,
        stores: Stores,
        *,
        scorer: CellScorer,
        scorer_id: str,
        dataset_name: str | None,
    ) -> None:
        """Incremental archive refresh, dataset-scoped. A row this formula cannot score is SKIPPED, counted
        and logged — never 0.0, which would poison every reading — and the skip binds BOTH halves below.

        The fold is stamped with ``scorer_id`` alone: it hashes both formulas
        (`compiler.py::auto_scorer_id`), so a second spelling here could only disagree with it."""
        if self._fold_seeded is None:
            self._fold_seeded = self._seed_from_fold(stores, dataset_name, scorer, scorer_id)

        # Captured BEFORE the details are read, never after: a run whose log grows between the
        # two must end up stamped with the OLDER signature, so the next process re-derives it.
        # Stamping the newer one would leave a fold that silently omits the rows it gained.
        signatures = archive_queries.run_signatures(stores)

        added = 0
        skipped: list[str] = []
        folded: list[dict[str, Any]] = []
        for run_id, detail in archive_queries.runs_since(
            stores, self._seen_runs, dataset_name=dataset_name
        ):
            stamp = {"fk": scorer_id, "sig": list(signatures.get(run_id) or ())}
            row, unscored = self._fold_run(run_id, detail, scorer, stamp)
            folded.append(row)
            if row.get("unscoreable"):
                skipped.append(run_id)
                logger.warning(
                    "sample refresh: archived run %r is unscoreable under the active formula "
                    "— skipping it (it predates the current observation vocabulary). %s",
                    run_id,
                    unscored,
                )
                continue
            added += 1

        # A run seen earlier that has GROWN since is read again, so every reader ranks a run on
        # what it holds now; the per-sample ingest keeps first sight.
        for run_id, (sig, _) in list(self._readings.items()):
            if sig == (now := list(signatures.get(run_id) or ())):
                continue
            grown = archive_queries.load_run(stores, run_id)
            regraded = None if grown is None else _graded_reading(grown, scorer)[0]
            if regraded is None:
                del self._readings[run_id]
            else:
                self._readings[run_id] = (now, regraded)

        # Replace rather than append whenever the seed was rejected: what this process just
        # derived IS the whole fold, and appending would leave the rejected rows in front of it.
        if dataset_name and (folded or not self._fold_seeded):
            archive_queries.write_sample_fold(
                stores, dataset_name=dataset_name, rows=folded, append=self._fold_seeded
            )
            self._fold_seeded = True
        if skipped:
            logger.warning(
                "sample refresh: %d archived run(s) skipped as unscoreable under the active "
                "formula (%s) — the index is built from the %d that scored.",
                len(skipped),
                ", ".join(skipped[:5]),
                added,
            )

        # Grade-C runs (incidental connector-retrieval short-circuits) are dropped here so the
        # cross-cycle evidence an optimizer reads reflects the deliberately-explored datapoints,
        # not whichever connector replayed most. Unscoreable runs are dropped for the same
        # reason: a fitness from a dead vocabulary is not comparable to one from this run's.
        # A bench pass goes whole: its accuracy is a reading on rows no optimizer may learn from.
        self.runs = [
            (entry, self._readings[run_id][1])
            for entry in archive_queries.list_runs(stores, dataset_name=dataset_name)
            if entry_grade(entry) != "C"
            and (run_id := entry.get("run_id", "")) in self._readings
            and entry.get("name") != MeasurementRole.BENCH
        ]
        self.generation += 1

        if added:
            logger.debug(
                "SampleIndex refreshed: %d new runs (total seen: %d)", added, len(self._seen_runs)
            )

    @classmethod
    def ensure_for(
        cls,
        stores: Stores | None,
        *,
        scorer: CellScorer,
        scorer_id: str,
        dataset_name: str | None,
        sample_ids: frozenset[int] | None,
    ) -> SampleIndex | None:
        if stores is None:
            return None
        idx = cls(sample_ids=sample_ids)
        idx.refresh(stores, scorer=scorer, scorer_id=scorer_id, dataset_name=dataset_name)
        return idx


__all__ = ["FailureCluster", "SampleIndex", "SampleRecord"]
