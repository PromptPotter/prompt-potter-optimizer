"""SampleIndex — per-sample derived view over MeasurementArchive."""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from promptpotter.application.intelligence.exploration import confidence_interval_width
from promptpotter.domain.sample import Sample
from promptpotter.shared.errors import is_error_result

if TYPE_CHECKING:
    from promptpotter.domain.sample import Measurement
    from promptpotter.infrastructure.store.measurement_archive import MeasurementArchive

logger = logging.getLogger(__name__)


@dataclass
class QueryRecord:
    """Per-sample pattern summary across measurements."""

    query: str
    sample_id: int
    hit_rate: float
    n_measurements: int
    variance: float
    dominant_failure_mode: str = ""


@dataclass
class FailureCluster:
    """Samples grouped by shared failure reason."""

    failure_mode: str
    query_count: int
    fraction: float
    example_queries: list[str] = field(default_factory=list)


@dataclass
class HardnessRecord:
    """Per-sample hardness summary derived from a fitted Rasch posterior.

    ``delta`` is sample difficulty in logits (higher = harder). ``ci_width``
    is the 95% credible interval; "confirmed-hard" means high delta with
    narrow CI, "suspected-hard" means high delta with wide CI.
    """

    sample_id: int
    query: str
    delta: float
    ci_width: float
    n_observations: int


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
        self._failure_modes: dict[int, list[str]] = defaultdict(list)
        self._degradation_counts: dict[int, int] = defaultdict(int)
        self._flips: list[dict[str, Any]] = []
        # Cache for derived query records; cleared on ingest.
        self._cache_records: list[QueryRecord] | None = None

    def register(self, sample: Sample) -> None:
        """Register a Sample at dataset-load time."""
        self._samples[sample.id] = sample

    def register_many(self, samples: list[Sample]) -> None:
        for s in samples:
            self.register(s)

    def sample(self, sample_id: int) -> Sample | None:
        return self._samples.get(sample_id)

    def measurements(
        self,
        sample_id: int,
        archive: MeasurementArchive,
        backend_id: str,
    ) -> list[Measurement]:
        """All cross-campaign measurements for ``sample_id``.

        Forwards to ``MeasurementArchive.measurements_for_sample``,
        passing the cached ``Sample.run_ids`` so the archive skips the
        index scan when the sample is registered here.
        """
        sample = self._samples.get(sample_id)
        run_ids = sample.run_ids if sample else None
        return archive.measurements_for_sample(backend_id, sample_id, run_ids=run_ids)

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

            hit = bool(item.get("hit"))
            self._hits[sid].append(hit)

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
        prev_results: list[dict],
        new_results: list[dict],
    ) -> int:
        """Record hit/miss flips between rounds; return the count."""
        prev_hits: dict[int, bool] = {}
        for r in prev_results:
            sid = r.get("sample_id")
            if sid is not None:
                prev_hits[sid] = bool(r.get("hit"))

        count = 0
        for r in new_results:
            sid = r.get("sample_id")
            if sid is None or sid not in prev_hits:
                continue
            new_hit = bool(r.get("hit"))
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

    def flips(self, sample_id: int | None = None, limit: int = 20) -> list[dict]:
        flips = self._flips
        if sample_id is not None:
            flips = [f for f in flips if f.get("sample_id") == sample_id]
        return flips[-limit:]

    def all_flips(self) -> list[dict]:
        return self._flips

    def records(self) -> list[QueryRecord]:
        """Build per-sample QueryRecord list, cached until next ingest."""
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
                QueryRecord(
                    query=query,
                    sample_id=sid,
                    hit_rate=round(hit_rate, 4),
                    n_measurements=len(hits),
                    variance=round(variance, 4),
                    dominant_failure_mode=self._dominant_failure_mode(sid),
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
    ) -> list[QueryRecord]:
        """Zero-signal samples — always-hit and/or always-miss."""
        out: list[QueryRecord] = []
        for r in self.records():
            if len(self._hits.get(r.sample_id, [])) < min_observations:
                continue
            if (include_always_miss and r.hit_rate == 0.0) or (
                include_always_hit and r.hit_rate == 1.0
            ):
                out.append(r)
        return out

    def discriminating(self, min_variance: float = 0.1) -> list[QueryRecord]:
        """Samples whose outcome varies across configurations."""
        return [r for r in self.records() if r.variance >= min_variance]

    def hardness_records(self, posterior: Any) -> list[HardnessRecord]:
        """Samples sorted by Rasch posterior δ_s, hardest first.

        ``posterior`` is a ``RaschPosterior`` (typed as ``Any`` here to
        keep the intelligence-layer import direction one-way). Confirmed
        hards have narrow ``ci_width``; suspected hards have wide ``ci_width``.
        """

        out: list[HardnessRecord] = []
        for sid, delta in posterior.delta.items():
            se = posterior.delta_se.get(sid, 0.0)
            sample = self._samples.get(sid)
            query = sample.query if sample else ""
            out.append(
                HardnessRecord(
                    sample_id=sid,
                    query=query,
                    delta=float(delta),
                    ci_width=float(confidence_interval_width(se)),
                    n_observations=int(posterior.n_obs_per_sample.get(sid, 0)),
                )
            )
        out.sort(key=lambda r: -r.delta)
        return out

    def persistent_failures(self, min_streak: int = 3) -> list[QueryRecord]:
        """Intractable (hit_rate == 0) + chronic (failed last ``min_streak``) samples."""
        records = []
        for r in self.records():
            hits = self._hits.get(r.sample_id, [])
            if len(hits) >= min_streak and not any(hits[-min_streak:]):
                records.append(r)
        records.sort(key=lambda r: r.hit_rate)
        return records

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
            example_queries = [self._samples[sid].query for sid in sids[:3] if sid in self._samples]
            clusters.append(
                FailureCluster(
                    failure_mode=mode,
                    query_count=len(sids),
                    fraction=len(sids) / total if total else 0.0,
                    example_queries=example_queries,
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

    def _dominant_failure_mode(self, sample_id: int) -> str:
        modes = self._failure_modes.get(sample_id, [])
        return Counter(modes).most_common(1)[0][0] if modes else ""


"""AxisIndex — derived axis-keyed view over the MeasurementArchive.

Both digest sides are pure derivations of the archive and refresh
**incrementally**: the axis side folds new index entries into
``_axis_values`` via an in-process ``_axis_seen_runs`` cursor, and the
sample-side :class:`SampleIndex` ingests delta runs via
``archive.load_since(sample_index._seen_runs)``. Neither owns an on-disk
artifact; the archive is the single source of truth.

The class hosts the digest API consumed by L1/L2/L3 prompts. Names are
stable LLM-context surfaces — ``digest_for_l1_generate`` / ``_l1_critique``
/ ``_l2`` / ``_l3``.
"""


import logging
from dataclasses import dataclass, field
from itertools import combinations, pairwise
from typing import TYPE_CHECKING

from promptpotter.application.scoring.formula import rescore_results
from promptpotter.domain.scoring import Scorer

if TYPE_CHECKING:
    from promptpotter.infrastructure.store import Stores


NOISE_THRESHOLD = 0.02


@dataclass
class ValueRecord:
    """A concrete value observed for an axis, with its performance stats."""

    value_preview: str
    mean_accuracy: float
    sample_count: int


@dataclass
class AxisImpact:
    """Parameter impact summary for one search space dimension."""

    axis: str
    effect_size: float
    consistency: float
    classification: str
    top_values: list[ValueRecord] = field(default_factory=list)
    sample_count: int = 0


def _collect(*items: tuple[str, str | None]) -> dict[str, str] | None:
    """Build a dict from (key, value) pairs, dropping pairs whose value is falsy."""
    out = {k: v for k, v in items if v}
    return out or None


class AxisIndex:
    """Derived axis-keyed view over the MeasurementArchive.

    Holds two collaborating pieces:

    * ``sample_index`` — per-sample derived state (no persistence).
    * ``_axis_values`` — axis → value → list[accuracy], grown
      incrementally from the archive index by folding only new entries
      (``_axis_seen_runs`` cursor).

    Failure-group × axis correlations are recomputed on every refresh —
    cheap at current scale and avoids drift from a throttle.
    """

    def __init__(self, sample_index: SampleIndex | None = None) -> None:
        self.sample_index: SampleIndex = sample_index or SampleIndex()
        self._axis_values: dict[str, dict[str, list[float]]] = defaultdict(
            lambda: defaultdict(list),
        )
        # In-process cursor tracking which archive entries have been folded
        # into ``_axis_values``. Mirrors ``sample_index._seen_runs`` for the
        # axis side; new processes re-walk the full archive on first refresh.
        self._axis_seen_runs: set[str] = set()
        self._axis_failure_group_deltas: dict[str, dict[str, float]] = {}
        self._cache_axis_impacts: dict[str, AxisImpact | None] = {}

    # ----- axis analytics -----

    def axis_rankings(self) -> list[AxisImpact]:
        """All axes ranked by effect size (descending)."""
        impacts = [
            i
            for axis, vals in self._axis_values.items()
            if (i := self._compute_axis_impact(axis, vals))
        ]
        return sorted(impacts, key=lambda a: -a.effect_size)

    def _exhausted_axes(self, min_values: int = 4, max_effect: float = 0.02) -> list[AxisImpact]:
        """Axes thoroughly tested with negligible effect — further exploration wastes budget."""
        out = [
            i
            for axis, vals in self._axis_values.items()
            if len(vals) >= min_values
            and (i := self._compute_axis_impact(axis, vals))
            and i.effect_size <= max_effect
        ]
        return sorted(out, key=lambda a: a.effect_size)

    def _axis_value_trend(self, axis: str) -> str:
        """One of: ``increasing``, ``decreasing``, ``peaked``, ``flat``, ``non_numeric``."""
        pairs: list[tuple[float, float]] = []
        for v, accs in self._axis_values.get(axis, {}).items():
            if not accs:
                continue
            try:
                pairs.append((float(v), sum(accs) / len(accs)))
            except (ValueError, TypeError):
                return "non_numeric"
        if len(pairs) < 3:
            return "flat"
        means = [m for _, m in sorted(pairs)]
        deltas = [b - a for a, b in pairwise(means)]
        pos = sum(1 for d in deltas if d > NOISE_THRESHOLD)
        neg = sum(1 for d in deltas if d < -NOISE_THRESHOLD)
        if pos > len(deltas) * 0.6 and neg == 0:
            return "increasing"
        if neg > len(deltas) * 0.6 and pos == 0:
            return "decreasing"
        if pos > 0 and neg > 0:
            peak = means.index(max(means))
            if 0 < peak < len(means) - 1:
                return "peaked"
        return "flat"

    # ----- digest construction (one entry-point per agent) -----

    def digest_for_l1_generate(self) -> dict[str, str] | None:
        """Digest for the L1 generate dispatch_msg: failure_clusters, dead_queries, top_axes, top_values."""
        rankings3 = self.axis_rankings()[:3]
        top_vals_str: str | None = None
        if rankings3:
            impact = self._compute_axis_impact(
                rankings3[0].axis, self._axis_values.get(rankings3[0].axis, {})
            )
            if impact and impact.top_values:
                top_vals_str = "; ".join(
                    f"{r.value_preview} (acc={r.mean_accuracy:.1%})" for r in impact.top_values[:2]
                )

        c2 = self.sample_index.failure_clusters(2)
        dead = self.sample_index.dead(include_always_hit=False)
        return _collect(
            ("failure_clusters", _fmt_clusters(c2, with_counts=True) if c2 else None),
            ("dead_queries", f"{len(dead)} queries never hit" if dead else None),
            ("top_axes", _fmt_axis_rankings(rankings3) if rankings3 else None),
            ("top_values", top_vals_str),
        )

    def digest_for_l1_critique(self) -> dict[str, str] | None:
        """Digest for the L1 critique agent: discriminating, clusters, tractability,
        exhausted axes, value trends, improvement attribution."""
        disc = self.sample_index.discriminating()
        c2 = self.sample_index.failure_clusters(2)
        persistent = self.sample_index.persistent_failures(min_streak=3)
        exhausted = self._exhausted_axes()
        exhausted_str = (
            "; ".join(
                f"{a.axis} ({len(self._axis_values.get(a.axis, {}))} values tested, "
                f"effect={a.effect_size:.3f})"
                for a in exhausted[:5]
            )
            if exhausted
            else None
        )
        trend_parts = [
            f"{a.axis}: {t}"
            for a in self.axis_rankings()[:3]
            if (t := self._axis_value_trend(a.axis)) not in ("flat", "non_numeric")
        ]
        return _collect(
            (
                "discriminating_queries",
                f"{len(disc)} queries vary across configs" if disc else None,
            ),
            ("failure_clusters", _fmt_clusters(c2, with_counts=False) if c2 else None),
            ("tractability", _fmt_persistent_failures(persistent) if persistent else None),
            ("exhausted_axes", exhausted_str),
            ("value_trends", "; ".join(trend_parts) if trend_parts else None),
            ("improvement_attribution", self._format_recent_attributions(limit=3)),
        )

    def digest_for_l2(self) -> dict[str, str] | None:
        """Digest for the L2 refine_strategy dispatch_msg: rankings, bottleneck distribution,
        persistent failures, failure-group correlations, volatile queries."""
        rankings5 = self.axis_rankings()[:5]
        bottleneck = self.sample_index.bottleneck_distribution()
        persistent = self.sample_index.persistent_failures(min_streak=3)

        fg_lines: list[str] = []
        for a in rankings5[:3]:
            corr = self._axis_failure_group_deltas.get(a.axis, {})
            if corr:
                parts = [
                    f"{m}: {d:+.0%}" for m, d in sorted(corr.items(), key=lambda x: -abs(x[1]))[:3]
                ]
                fg_lines.append(f"{a.axis} → {', '.join(parts)}")

        flips = self.sample_index.flips(limit=50) if rankings5 else []
        flip_counts = Counter(f["query"] for f in flips)
        volatile = [(q, n) for q, n in flip_counts.most_common(5) if n >= 2]

        return _collect(
            ("axis_rankings", _fmt_axis_rankings(rankings5) if rankings5 else None),
            ("bottleneck_distribution", _fmt_bottleneck(bottleneck)),
            (
                "persistent_failures",
                _fmt_persistent_failures(persistent, terse=True) if persistent else None,
            ),
            ("failure_group_insights", "; ".join(fg_lines) if fg_lines else None),
            (
                "volatile_queries",
                "; ".join(f"{q[:50]} ({n} flips)" for q, n in volatile) if volatile else None,
            ),
        )

    def digest_for_l3(self) -> dict[str, str] | None:
        """Digest for the L3 modify_plan dispatch_msg: clusters, rankings, bottleneck, persistent."""
        c3 = self.sample_index.failure_clusters(3)
        rankings5 = self.axis_rankings()[:5]
        bottleneck = self.sample_index.bottleneck_distribution()
        persistent = self.sample_index.persistent_failures(min_streak=3)
        return _collect(
            ("failure_clusters", _fmt_clusters(c3, with_counts=True) if c3 else None),
            ("axis_rankings", _fmt_axis_rankings(rankings5) if rankings5 else None),
            ("bottleneck_distribution", _fmt_bottleneck(bottleneck)),
            (
                "persistent_failures",
                _fmt_persistent_failures(persistent, terse=True) if persistent else None,
            ),
        )

    def _format_recent_attributions(self, limit: int = 5) -> str | None:
        """Format recent positive flips (miss→hit) for L1 critique injection."""
        positive = [f for f in self.sample_index.all_flips() if f["new_hit"] and not f["old_hit"]]
        if not positive:
            return None
        recent = positive[-limit:]
        parts = [
            f"  Round {f['round']}: {f['query'][:50]} started hitting "
            f"after: {f['changes_description']}"
            for f in recent
        ]
        return f"{len(positive)} queries improved (last {len(recent)}):\n" + "\n".join(parts)

    # ----- failure-group correlation -----

    def _recompute_failure_group_correlations(self) -> None:
        """Recompute failure-group × axis deltas from current hits/axis values.

        Always overwrites ``_axis_failure_group_deltas`` (including resetting
        to ``{}`` when no clusters are present).
        """
        clusters = self.sample_index.failure_clusters(5)
        if not clusters:
            self._axis_failure_group_deltas = {}
            return

        # Map each failure mode to the full set of sample_ids whose dominant mode matches.
        groups: dict[str, set[int]] = {}
        for cluster in clusters:
            mode = cluster.failure_mode
            sids: set[int] = set()
            for sid in self.sample_index._samples:
                modes = self.sample_index.failure_modes(sid)
                if modes and Counter(modes).most_common(1)[0][0] == mode:
                    sids.add(sid)
            if sids:
                groups[mode] = sids

        if not groups:
            self._axis_failure_group_deltas = {}
            return

        def _hit_rate(sids: set[int]) -> float:
            rates = [sum(h) / len(h) for sid in sids if (h := self.sample_index.hits(sid))]
            return sum(rates) / len(sids) if sids else 0.0

        hit_rates = {name: _hit_rate(sids) for name, sids in groups.items()}

        new_deltas: dict[str, dict[str, float]] = {}
        for axis, values in self._axis_values.items():
            if len(values) < 2:
                continue
            impact = self._compute_axis_impact(axis, values)
            if not (impact and impact.effect_size > NOISE_THRESHOLD):
                continue
            for group_name, hit_rate in hit_rates.items():
                corr = impact.effect_size * (1 - hit_rate)
                if corr > 0.005:
                    new_deltas.setdefault(axis, {})[group_name] = round(corr, 4)

        self._axis_failure_group_deltas = new_deltas

    # ----- ingest / refresh -----

    def refresh(
        self,
        store: Stores,
        backend_id: str,
        scorer: Scorer | None = None,
        scorer_id: str = "none",
        scorer_formula: str | None = None,
    ) -> None:
        """Incrementally update both sides from the archive.

        Sample side: walk ``archive.load_since(_seen_runs)``, rescore
        items via ``rescore_results``, ingest into ``sample_index``,
        mark seen.

        Axis side: walk ``archive.list_all()`` and fold only entries
        whose ``run_id`` is new (cursor: ``_axis_seen_runs``). Index
        entries already carry ``pipeline_params`` and ``scores.accuracy``,
        so no detail load is needed. Touched axes have their cached
        impact invalidated; untouched axes keep theirs. Failure-group
        correlations are recomputed unconditionally afterwards (they
        depend on aggregate state).

        Both cursors are in-process only; new processes re-walk the
        full archive on first refresh.
        """
        added = 0
        for run_id, detail in store.archive.load_since(backend_id, self.sample_index._seen_runs):
            if scorer is not None:
                rescore_results(detail.get("measurements") or [], scorer, scorer_id, scorer_formula)
            self.sample_index.ingest_run(detail)
            self.sample_index.mark_seen(run_id)
            added += 1

        # Axis side: fold only new index entries into the persistent
        # ``_axis_values``, tracking which axes the delta touched so we
        # can invalidate exactly those impact-cache slots.
        touched_axes: set[str] = set()
        for entry in store.archive.list_all(backend_id):
            run_id = entry.get("run_id", "")
            if not run_id or run_id in self._axis_seen_runs:
                continue
            self._fold_entry(self._axis_values, entry, touched_axes=touched_axes)
            self._axis_seen_runs.add(run_id)
        for axis in touched_axes:
            self._cache_axis_impacts.pop(axis, None)
        self._recompute_failure_group_correlations()

        if added:
            logger.debug(
                "AxisIndex refreshed: %d new runs (total seen: %d)",
                added,
                len(self.sample_index._seen_runs),
            )

    def record_flips_from_rounds(self, rounds: list[Any], round_num: int) -> None:
        if len(rounds) < 2 or not (rounds[-2].results and rounds[-1].results):
            return
        desc = (
            rounds[-1].candidate_scores[0].get("changes_description", "")
            if rounds[-1].candidate_scores
            else ""
        )
        flips = self.sample_index.record_flips(
            round_num, desc, rounds[-2].results, rounds[-1].results
        )
        if flips:
            logger.debug("Round %d: %d query flips recorded", round_num, flips)

    @classmethod
    def ensure_for(
        cls,
        store: Stores | None,
        backend_id: str,
        scorer: Scorer | None = None,
        scorer_id: str = "none",
        scorer_formula: str | None = None,
    ) -> AxisIndex | None:
        """Build a fresh ``AxisIndex`` and refresh once.

        Returns ``None`` when ``store`` or ``backend_id`` is missing.
        Both digest sides are pure derivations over the archive; nothing
        is read from or written to disk here.
        """
        if not (store and backend_id):
            return None
        idx = cls()
        idx.refresh(
            store, backend_id, scorer=scorer, scorer_id=scorer_id, scorer_formula=scorer_formula
        )
        return idx

    # ----- helpers -----

    @staticmethod
    def _fold_entry(
        axis_values: dict[str, dict[str, list[float]]],
        entry: dict[str, Any],
        *,
        touched_axes: set[str] | None = None,
    ) -> None:
        """Fold one archive index entry's pipeline_params + accuracy into ``axis_values``.

        When ``touched_axes`` is provided, every axis that gets a value
        appended is added to the set so callers can scope cache
        invalidation to just the delta.
        """
        accuracy = entry.get("scores", {}).get("accuracy", 0.0)
        for node_name, node_config in (entry.get("pipeline_params") or {}).items():
            if isinstance(node_config, dict):
                for param, value in node_config.items():
                    axis = f"{node_name}.{param}" if node_name else param
                    axis_values[axis][_value_preview(value)].append(accuracy)
                    if touched_axes is not None:
                        touched_axes.add(axis)
            else:
                axis_values[node_name][_value_preview(node_config)].append(accuracy)
                if touched_axes is not None:
                    touched_axes.add(node_name)

    def _compute_axis_impact(
        self,
        axis: str,
        values: dict[str, list[float]],
    ) -> AxisImpact | None:
        if axis in self._cache_axis_impacts:
            return self._cache_axis_impacts[axis]

        records = [ValueRecord(v, sum(a) / len(a), len(a)) for v, a in values.items() if a]
        records.sort(key=lambda r: -r.mean_accuracy)
        means = [r.mean_accuracy for r in records]
        total = sum(r.sample_count for r in records)

        if len(means) < 2:
            impact = AxisImpact(axis, 0.0, 0.0, "dead", sample_count=total)
            self._cache_axis_impacts[axis] = impact
            return impact

        deltas = [abs(a - b) for a, b in combinations(means, 2)]
        effect = sum(deltas) / len(deltas)
        consistency = sum(1 for d in deltas if d > NOISE_THRESHOLD) / len(deltas)
        cls = (
            "consistently_impactful"
            if consistency >= 0.7
            else "sometimes_impactful"
            if consistency >= 0.3
            else "dead"
        )
        impact = AxisImpact(
            axis=axis,
            effect_size=round(effect, 4),
            consistency=round(consistency, 4),
            classification=cls,
            top_values=records[:5],
            sample_count=total,
        )
        self._cache_axis_impacts[axis] = impact
        return impact


def _value_preview(value: Any) -> str:
    s = str(value)
    return s[:80] if len(s) > 80 else s


def _fmt_axis_rankings(rankings: list[AxisImpact]) -> str:
    return "; ".join(f"{a.axis} (effect={a.effect_size:.3f}, {a.classification})" for a in rankings)


def _fmt_clusters(clusters: list[FailureCluster], *, with_counts: bool) -> str:
    if with_counts:
        return "; ".join(
            f"{c.failure_mode} ({c.fraction:.0%}, {c.query_count} queries)" for c in clusters
        )
    return "; ".join(f"{c.failure_mode} ({c.fraction:.0%})" for c in clusters)


def _fmt_bottleneck(bottleneck: dict[str, float] | None) -> str | None:
    if not bottleneck:
        return None
    return "; ".join(f"{step}: {frac:.0%}" for step, frac in bottleneck.items())


def _fmt_persistent_failures(persistent: list[QueryRecord], *, terse: bool = False) -> str:
    intractable = [q for q in persistent if q.hit_rate == 0]
    chronic = [q for q in persistent if q.hit_rate > 0]
    parts: list[str] = []
    if intractable:
        suffix = "(never hit)" if terse else "(never hit in any config)"
        parts.append(f"{len(intractable)} intractable {suffix}")
    if chronic:
        suffix = "failures" if terse else "(recently failing but hit_rate > 0)"
        parts.append(f"{len(chronic)} chronic {suffix}")
    return "; ".join(parts)
