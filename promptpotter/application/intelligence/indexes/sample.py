from __future__ import annotations

import logging
from collections import Counter, defaultdict
from collections.abc import KeysView
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.application.scoring.metrics import fold_cells
from promptpotter.domain.results import CellFold
from promptpotter.domain.results_health import UNKNOWN_STEP, terminal_node
from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import CellSheet, GradedCell, Scorer
from promptpotter.infrastructure.store import archive_queries
from promptpotter.infrastructure.store.archive_queries import SampleFoldRow
from promptpotter.shared.hashing import shapes_optimizer_prompt
from promptpotter.shared.measurement_context import MeasurementRole

if TYPE_CHECKING:
    from promptpotter.infrastructure.store.stores import Stores

logger = logging.getLogger(__name__)


def _learnable(cell: GradedCell) -> bool:
    facts = cell.facts
    return facts.provenance != "C" and facts.role != MeasurementRole.BENCH


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
    def __init__(self, *, sample_ids: frozenset[int] | None) -> None:
        # The search pool, applied at REPLAY so the persisted fold stays whole; ``None`` admits all.
        self._admitted = sample_ids
        self._rows: dict[str, SampleFoldRow] | None = None
        self._reset()
        self.runs: list[tuple[dict[str, Any], CellFold]] = []
        self.generation = 0

    def _reset(self) -> None:
        # Admitted or not, so the persisted fold does not depend on who wrote it.
        self._introduced: set[int] = set()
        self._samples: dict[int, Sample] = {}
        self._hits: dict[int, list[bool]] = defaultdict(list)
        self._hit_configs: dict[int, list[str]] = defaultdict(list)
        self._failure_modes: dict[int, list[str]] = defaultdict(list)
        self._degradation_counts: dict[int, int] = defaultdict(int)
        self._cache_records: list[SampleRecord] | None = None

    def register(self, sample: Sample) -> None:
        self._samples[sample.id] = sample

    def sample(self, sample_id: int) -> Sample | None:
        return self._samples.get(sample_id)

    def sample_ids(self) -> KeysView[int]:
        return self._samples.keys()

    def _derive(
        self, graded: CellSheet, prior: SampleFoldRow | None
    ) -> tuple[list[tuple[int, bool, bool, str | None]], list[tuple[int, str, str]]]:
        cells: list[tuple[int, bool, bool, str | None]] = []
        new_samples = [] if prior is None else list(prior.new_samples)

        for cell in graded:
            facts = cell.facts
            sid = facts.sample_id
            if sid not in self._introduced:
                self._introduced.add(sid)
                new_samples.append((sid, facts.query, facts.ground_truth))

            hit = cell.hit
            degraded = bool(facts.pipeline.diagnostics.warnings)
            # An errored row is not a bottleneck reading, so it carries no failure mode.
            failure_mode = None if (hit or facts.errored) else terminal_node(facts) or UNKNOWN_STEP
            cells.append((sid, hit, degraded, failure_mode))

        return cells, new_samples

    def replay_row(self, row: SampleFoldRow) -> None:
        """The SOLE mutation path, so a row derived now and one read off disk cannot disagree."""
        pointer = row.sp
        admitted = self._admitted

        for sid, query, ground_truth in row.new_samples:
            self._introduced.add(sid)
            if sid not in self._samples and (admitted is None or sid in admitted):
                self.register(Sample(id=sid, query=query, ground_truth=ground_truth))

        for sid, hit, degraded, failure_mode in row.cells:
            if admitted is not None and sid not in admitted:
                continue
            self._hits[sid].append(hit)
            if hit and pointer:
                self._hit_configs[sid].append(pointer)
            if degraded:
                self._degradation_counts[sid] += 1
            if failure_mode is not None:
                self._failure_modes[sid].append(failure_mode)

        self._cache_records = None

    def _replay(self) -> None:
        self._reset()
        for row in (self._rows or {}).values():
            self.replay_row(row)

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
        """``min_observations`` filters out the cold start, where every sample looks rare."""
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
            out.append((sid, query, hit_count, total, list(self._hit_configs.get(sid, []))))
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
        counts: dict[str, int] = defaultdict(int)
        total = 0
        for modes in self._failure_modes.values():
            for mode in modes:
                counts[mode] += 1
                total += 1
        if total == 0:
            return {}
        return {step: count / total for step, count in sorted(counts.items(), key=lambda x: -x[1])}

    def _fold_population(
        self,
        population: dict[str, Any],
        scorer: Scorer,
        sig: list[list[Any]],
        prior: SampleFoldRow | None,
    ) -> tuple[SampleFoldRow, str | None]:
        """The WHOLE population goes on the first ungradable row: a per-row skip folds a part as if scored entire."""
        sheet = scorer.read(population["measurements"])
        row = SampleFoldRow(
            config_key=population["config_key"],
            sp=population.get("prompt_fields_id") or population["config_key"],
            fk=scorer.id,
            sig=sig,
            graded=[
                (cell.sample_id, float(objective), cell.facts.provenance or "C")
                for cell in sheet
                if cell.scored and (objective := cell.grade.objective) is not None
            ],
        )
        unscored = next((why for cell in sheet if (why := cell.grade.unscored)), None)
        if unscored is not None:
            return row.model_copy(update={"unscoreable": True}), unscored
        learnable = sheet.where(_learnable)
        cells, new_samples = self._derive(sheet, prior)
        return row.model_copy(
            update={
                "cells": cells,
                "new_samples": new_samples,
                "reading": fold_cells(learnable) if learnable else None,
            }
        ), None

    def _persisted(
        self, stores: Stores, dataset_name: str | None, scorer_id: str
    ) -> dict[str, SampleFoldRow]:
        """A fold with any row graded under another formula is rejected whole."""
        if not dataset_name:
            return {}
        rows = archive_queries.sample_fold_rows(stores, dataset_name=dataset_name)
        if any(row.fk != scorer_id for row in rows):
            return {}
        return {row.config_key: row for row in rows}

    def refresh(
        self,
        stores: Stores,
        *,
        scorer: Scorer,
        dataset_name: str | None,
    ) -> None:
        """A population this formula cannot score is SKIPPED, never 0.0, which poisons every reading."""
        first = self._rows is None
        if self._rows is None:
            self._rows = self._persisted(stores, dataset_name, scorer.id)
            self._replay()

        # Signatures BEFORE populations: one growing between the two keeps the OLDER and refolds.
        signatures = archive_queries.population_signatures(stores, dataset_name=dataset_name)
        entries = archive_queries.list_populations(stores, dataset_name=dataset_name)
        # A controlled line's memory grows by the answers it REPLAYS, which move no file.
        scoped = archive_queries.memory_scoped()

        rows: dict[str, SampleFoldRow] = {}
        moved = 0
        skipped: list[str] = []
        for entry in entries:
            key = entry["config_key"]
            sig = signatures.get(key) or []
            held = self._rows.get(key)
            if scoped or held is None or held.sig != sig:
                population = archive_queries.load_population(stores, entry)
                if population is None:
                    continue
                held, unscored = self._fold_population(population, scorer, sig, held)
                moved += 1
                if unscored is not None:
                    logger.warning(
                        "sample refresh: configuration %r is unscoreable under the active "
                        "formula — skipping it (it predates the current observation "
                        "vocabulary). %s",
                        key,
                        unscored,
                    )
            if held.unscoreable:
                skipped.append(key)
            rows[key] = held

        if moved or rows.keys() != self._rows.keys():
            self._rows = rows
            self._replay()
            if dataset_name:
                archive_queries.write_sample_fold(
                    stores, dataset_name=dataset_name, rows=rows.values()
                )
        elif not first:
            return
        if skipped:
            logger.warning(
                "sample refresh: %d configuration(s) skipped as unscoreable under the active "
                "formula (%s) — the index is built from the %d that scored.",
                len(skipped),
                ", ".join(skipped[:5]),
                len(rows) - len(skipped),
            )

        self.runs = [
            (entry, row.reading)
            for entry in entries
            if (row := rows.get(entry["config_key"])) is not None and row.reading is not None
        ]
        self.generation += 1
        logger.debug("SampleIndex refreshed: %d of %d configurations folded", moved, len(rows))

    @classmethod
    def ensure_for(
        cls,
        stores: Stores | None,
        *,
        scorer: Scorer,
        dataset_name: str | None,
        sample_ids: frozenset[int] | None,
    ) -> SampleIndex | None:
        if stores is None:
            return None
        idx = cls(sample_ids=sample_ids)
        idx.refresh(stores, scorer=scorer, dataset_name=dataset_name)
        return idx


__all__ = ["FailureCluster", "SampleIndex", "SampleRecord"]
