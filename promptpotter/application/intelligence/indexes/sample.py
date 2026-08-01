"""SampleIndex — per-sample derived view + record dataclasses."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import KeysView
from dataclasses import dataclass
from typing import Any

from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import is_hit
from promptpotter.shared.errors import is_error_result


@dataclass
class SampleRecord:
    """Per-sample pattern summary across measurements."""

    query: str
    sample_id: int
    hit_rate: float
    variance: float


@dataclass
class FailureCluster:
    """Samples grouped by shared failure reason."""

    failure_mode: str
    sample_count: int
    fraction: float


class SampleIndex:
    """Per-sample state keyed by ``sample.id: int``.

    Pure derived view over the ``MeasurementArchive``: holds Sample
    primitives plus per-sample aggregate tables that are populated by
    :meth:`ingest_run` during ``AxisIndex.refresh``. ``_seen_runs`` is an
    in-process delta cursor — it is never persisted across processes.
    """

    def __init__(self) -> None:
        self._samples: dict[int, Sample] = {}
        self._seen_runs: set[str] = set()
        self._hits: dict[int, list[bool]] = defaultdict(list)
        self._hit_run_ids: dict[int, list[str]] = defaultdict(list)
        self._failure_modes: dict[int, list[str]] = defaultdict(list)
        self._degradation_counts: dict[int, int] = defaultdict(int)
        self._flips: list[dict[str, Any]] = []
        # Cache for derived query records; cleared on ingest.
        self._cache_records: list[SampleRecord] | None = None

    def register(self, sample: Sample) -> None:
        """Register a Sample at dataset-load time."""
        self._samples[sample.id] = sample

    def register_many(self, samples: list[Sample]) -> None:
        for s in samples:
            self.register(s)

    def sample(self, sample_id: int) -> Sample | None:
        return self._samples.get(sample_id)

    def sample_ids(self) -> KeysView[int]:
        """Live view of registered sample ids — iterates without copying."""
        return self._samples.keys()

    def ingest_run(self, run_detail: dict[str, Any]) -> None:
        """Replay a measurement-archive entry into the index."""
        items = run_detail.get("measurements", [])
        run_id = run_detail.get("run_id", "")

        for item in items:
            sid = item.get("sample_id")
            if sid is None:
                continue

            if sid not in self._samples:
                query = item.get("query", "")
                gt = item.get("ground_truth", "")
                self.register(Sample(id=sid, query=query, ground_truth=gt))

            hit = is_hit(item.get("fitness"))
            self._hits[sid].append(hit)
            if hit and run_id:
                self._hit_run_ids[sid].append(run_id)

            pd = item.get("pipeline_data") or {}
            if (pd.get("diagnostics") or {}).get("warnings"):
                self._degradation_counts[sid] += 1

            if not hit and not is_error_result(item):
                terminated = pd.get("terminated_at", "unknown")
                self._failure_modes[sid].append(terminated)

            sample = self._samples.get(sid)
            if sample is not None and run_id and run_id not in sample.run_ids:
                sample.run_ids.append(run_id)

        self._cache_records = None

    def record_flips(
        self,
        round_num: int,
        changes_description: str,
        prev_results: list[dict[str, Any]],
        new_results: list[dict[str, Any]],
    ) -> int:
        """Record hit/miss flips between rounds; return the count."""
        prev_hits: dict[int, bool] = {}
        for r in prev_results:
            sid = r.get("sample_id")
            if sid is not None:
                prev_hits[sid] = is_hit(r.get("fitness"))

        count = 0
        for r in new_results:
            sid = r.get("sample_id")
            if sid is None or sid not in prev_hits:
                continue
            new_hit = is_hit(r.get("fitness"))
            old_hit = prev_hits[sid]
            if new_hit != old_hit:
                self._flips.append(
                    {
                        "sample_id": sid,
                        "query": r.get("query", ""),
                        "round": round_num,
                        "changes_description": changes_description[:80],
                        "old_hit": old_hit,
                        "new_hit": new_hit,
                    }
                )
                count += 1
        return count

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

    def flips(self, sample_id: int | None = None, limit: int = 20) -> list[dict[str, Any]]:
        flips = self._flips
        if sample_id is not None:
            flips = [f for f in flips if f.get("sample_id") == sample_id]
        return flips[-limit:]

    def all_flips(self) -> list[dict[str, Any]]:
        return self._flips

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
        """Zero-signal samples — always-hit and/or always-miss."""
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
        """Samples whose outcome varies across configurations."""
        return [r for r in self.records() if r.variance >= min_variance]

    def persistent_failures(self, min_streak: int = 3) -> list[SampleRecord]:
        """Intractable (hit_rate == 0) + chronic (failed last ``min_streak``) samples."""
        records = []
        for r in self.records():
            hits = self._hits.get(r.sample_id, [])
            if len(hits) >= min_streak and not any(hits[-min_streak:]):
                records.append(r)
        records.sort(key=lambda r: r.hit_rate)
        return records

    def rare_hit_samples(
        self,
        *,
        max_hits: int = 3,
        min_observations: int = 10,
    ) -> list[tuple[int, str, int, int, list[str]]]:
        """Samples hit by ≤ ``max_hits`` candidates out of ≥ ``min_observations``.

        Each rare hit is a *recipe pointer* — the one or two runs that cracked
        a chronically-failing sample. Returns
        ``(sample_id, query_stem, hit_count, total_observations, hit_run_ids)``
        sorted by hit_count asc, then total desc. ``min_observations`` filters
        out the cold-start case (one cycle, n=4) where every sample is "rare".
        """
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
        """Samples grouped by dominant failure mode."""
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
        """``{terminated_at_step: fraction_of_failures}``."""
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


__all__ = ["FailureCluster", "SampleIndex", "SampleRecord"]
