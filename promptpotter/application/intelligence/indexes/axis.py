"""AxisIndex — derived axis-keyed view + digest API for L1/L2/L3 prompts."""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import combinations, pairwise
from typing import TYPE_CHECKING, Any

from promptpotter.application.intelligence.indexes.sample import SampleIndex
from promptpotter.application.scoring.formula import rescore_results
from promptpotter.domain.measurement_provenance import entry_grade
from promptpotter.domain.scoring import Scorer
from promptpotter.domain.search_point import PARAM_FORBIDDEN_KEYS
from promptpotter.infrastructure.store import archive_views


def _is_forbidden_axis(axis: str) -> bool:
    """True iff ``<param>`` half is in ``PARAM_FORBIDDEN_KEYS`` (operator-locked)."""
    _, _, param = axis.partition(".")
    return param in PARAM_FORBIDDEN_KEYS


if TYPE_CHECKING:
    from promptpotter.application.intelligence.indexes.sample import FailureCluster, SampleRecord
    from promptpotter.infrastructure.store import Stores

logger = logging.getLogger(__name__)


NOISE_THRESHOLD = 0.02


def _value_preview(value: Any) -> str:
    s = str(value)
    return s[:80] if len(s) > 80 else s


def _fmt_axis_rankings(
    rankings: list[AxisImpact], peaked_axes: frozenset[str] | None = None
) -> str:
    """Render the top-axes line. When ``peaked_axes`` is provided, axes whose
    trend has converged on a measured peak are tagged inline so the LLM
    consuming the digest can't read the effect rank without also seeing
    the peakedness — the prior split (effect ranks here, ``value_trends``
    line elsewhere) let L1 latch onto "highest effect → mutate" while
    silently ignoring "peaked → don't mutate". Single annotated line
    removes the contradiction.
    """
    peaked = peaked_axes or frozenset()
    parts: list[str] = []
    for a in rankings:
        base = f"{a.axis} (effect={a.effect_size:.3f}, {a.classification}"
        if a.axis in peaked:
            base += (
                ", PEAKED — do not mutate unless the critique names this axis "
                "or exploration_budget=wide rebut"
            )
        base += ")"
        parts.append(base)
    return "; ".join(parts)


def _fmt_clusters(clusters: list[FailureCluster], *, with_counts: bool) -> str:
    if with_counts:
        return "; ".join(
            f"{c.failure_mode} ({c.fraction:.0%}, {c.sample_count} samples)" for c in clusters
        )
    return "; ".join(f"{c.failure_mode} ({c.fraction:.0%})" for c in clusters)


def _fmt_bottleneck(bottleneck: dict[str, float] | None) -> str | None:
    if not bottleneck:
        return None
    return "; ".join(f"{step}: {frac:.0%}" for step, frac in bottleneck.items())


def _fmt_persistent_failures(persistent: list[SampleRecord]) -> str:
    intractable = [q for q in persistent if q.hit_rate == 0]
    chronic = [q for q in persistent if q.hit_rate > 0]
    parts: list[str] = []
    if intractable:
        parts.append(f"{len(intractable)} intractable (never hit in any config)")
    if chronic:
        parts.append(f"{len(chronic)} chronic (recently failing but hit_rate > 0)")
    return "; ".join(parts)


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


@dataclass
class RunRecord:
    """One archive run's summary for the historical-best leaderboard."""

    run_id: str
    name: str
    accuracy: float
    composite: float
    hits: int
    total: int


def _collect(*items: tuple[str, str | None]) -> dict[str, str] | None:
    """Build a dict from (key, value) pairs, dropping pairs whose value is falsy."""
    out = {k: v for k, v in items if v}
    return out or None


class AxisIndex:
    """Derived axis-keyed view over the MeasurementArchive (axis → value → [accuracy])."""

    def __init__(
        self,
        sample_index: SampleIndex | None = None,
    ) -> None:
        self.sample_index: SampleIndex = sample_index or SampleIndex()
        self._axis_values: dict[str, dict[str, list[float]]] = defaultdict(
            lambda: defaultdict(list),
        )
        self._axis_seen_runs: set[str] = set()
        self._axis_failure_group_deltas: dict[str, dict[str, float]] = {}
        self._top_runs: list[RunRecord] = []

    # ----- axis analytics -----

    def peaked_axes(self) -> frozenset[str]:
        """Axes whose value trend is ``peaked`` — parent's value IS the measured peak."""
        return frozenset(
            axis for axis in self._axis_values if self._axis_value_trend(axis) == "peaked"
        )

    def axis_rankings(self) -> list[AxisImpact]:
        """All axes ranked by effect size desc; ``PARAM_FORBIDDEN_KEYS`` dropped."""
        impacts = [
            i
            for axis, vals in self._axis_values.items()
            if not _is_forbidden_axis(axis) and (i := self._compute_axis_impact(axis, vals))
        ]
        return sorted(impacts, key=lambda a: -a.effect_size)

    def _exhausted_axes(self, min_values: int = 4, max_effect: float = 0.02) -> list[AxisImpact]:
        """Axes thoroughly tested with negligible effect — further exploration wastes budget."""
        out = [
            i
            for axis, vals in self._axis_values.items()
            if not _is_forbidden_axis(axis)
            and len(vals) >= min_values
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

    # ----- digest construction (single entry-point, layer-agnostic) -----

    def digest(self) -> dict[str, str] | None:
        """Layer-agnostic axis-keyed digest — union of all axis observations.

        Same payload rendered into every L1/L2/L3 prompt; per-layer
        filtering, if it ever returns, lives in renderers, not here.
        """
        rankings5 = self.axis_rankings()[:5]
        top_vals_str: str | None = None
        if rankings5:
            impact = self._compute_axis_impact(
                rankings5[0].axis, self._axis_values.get(rankings5[0].axis, {})
            )
            if impact and impact.top_values:
                top_vals_str = "; ".join(
                    f"{r.value_preview} (acc={r.mean_accuracy:.1%})" for r in impact.top_values[:2]
                )

        clusters = self.sample_index.failure_clusters(2)
        dead = self.sample_index.dead(include_always_hit=False)
        disc = self.sample_index.discriminating()
        persistent = self.sample_index.persistent_failures(min_streak=3)
        bottleneck = self.sample_index.bottleneck_distribution()
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
        peaked = self.peaked_axes()

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
            ("axis_rankings", _fmt_axis_rankings(rankings5, peaked) if rankings5 else None),
            ("top_values", top_vals_str),
            ("failure_clusters", _fmt_clusters(clusters, with_counts=True) if clusters else None),
            ("dead_queries", f"{len(dead)} queries never hit" if dead else None),
            (
                "discriminating_queries",
                f"{len(disc)} queries vary across configs" if disc else None,
            ),
            ("bottleneck_distribution", _fmt_bottleneck(bottleneck)),
            (
                "persistent_failures",
                _fmt_persistent_failures(persistent) if persistent else None,
            ),
            ("failure_group_insights", "; ".join(fg_lines) if fg_lines else None),
            (
                "volatile_queries",
                "; ".join(f"{q[:50]} ({n} flips)" for q, n in volatile) if volatile else None,
            ),
            ("exhausted_axes", exhausted_str),
            ("improvement_attribution", self._format_recent_attributions(limit=3)),
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
        """Recompute failure-group × axis deltas; overwrites (resets to {} when no clusters)."""
        clusters = self.sample_index.failure_clusters(5)
        if not clusters:
            self._axis_failure_group_deltas = {}
            return

        groups: dict[str, set[int]] = {}
        for cluster in clusters:
            mode = cluster.failure_mode
            sids: set[int] = set()
            for sid in self.sample_index.sample_ids():
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
        *,
        dataset_name: str | None,
    ) -> None:
        """Incremental archive refresh, dataset-scoped (required) to prevent cross-dataset pollution."""
        added = 0
        for run_id, detail in archive_views.runs_since(
            store, backend_id, self.sample_index._seen_runs, dataset_name=dataset_name
        ):
            if scorer is not None:
                rescore_results(detail.get("measurements") or [], scorer, scorer_id, scorer_formula)
            self.sample_index.ingest_run(detail)
            self.sample_index.mark_seen(run_id)
            added += 1

        # Track touched axes to invalidate exactly those impact-cache slots.
        # Grade-C runs (incidental connector-retrieval short-circuits) are dropped
        # here so the cross-cycle digest the L1/L2/L3 prompts read reflects the
        # deliberately-explored datapoints, not whichever connector replayed most.
        touched_axes: set[str] = set()
        all_entries: list[dict[str, Any]] = []
        for entry in archive_views.list_runs(store, backend_id, dataset_name=dataset_name):
            if entry_grade(entry) == "C":
                continue
            all_entries.append(entry)
            run_id = entry.get("run_id", "")
            if not run_id or run_id in self._axis_seen_runs:
                continue
            self._fold_entry(self._axis_values, entry, touched_axes=touched_axes)
            self._axis_seen_runs.add(run_id)
        self._recompute_failure_group_correlations()
        self._refresh_top_runs(all_entries)

        if added:
            logger.debug(
                "AxisIndex refreshed: %d new runs (total seen: %d)",
                added,
                len(self.sample_index._seen_runs),
            )

    def _refresh_top_runs(self, entries: list[dict[str, Any]], k: int = 10) -> None:
        """Top-K leaderboard, sorted by (composite_fitness, accuracy) desc.

        Filters partial-coverage runs by keeping only modal ``total`` count —
        non-comparable composites (8/20 vs 20/20) would inflate the leaderboard.
        """
        from collections import Counter

        all_totals = [
            (entry.get("scores") or {}).get("total", 0)
            for entry in entries
            if (entry.get("scores") or {}).get("total", 0) > 0
        ]
        if not all_totals:
            self._top_runs = []
            return
        modal_total = Counter(all_totals).most_common(1)[0][0]

        # One run can have several archive entries (e.g. per-sample backfill rows);
        # collapse to the best record per run_id so the leaderboard never lists the
        # same run twice (wasted bytes + a misleading panel for L1/L2).
        best_by_run: dict[str, RunRecord] = {}
        for entry in entries:
            scores = entry.get("scores") or {}
            total = scores.get("total") or 0
            if total != modal_total:
                continue
            run_id = entry.get("run_id", "")
            rec = RunRecord(
                run_id=run_id,
                name=entry.get("name", ""),
                accuracy=scores.get("accuracy", 0.0),
                composite=scores.get("composite_fitness", 0.0),
                hits=scores.get("hits", 0),
                total=total,
            )
            prev = best_by_run.get(run_id)
            if prev is None or (rec.composite, rec.accuracy) > (prev.composite, prev.accuracy):
                best_by_run[run_id] = rec
        scored = sorted(best_by_run.values(), key=lambda r: (-r.composite, -r.accuracy))
        self._top_runs = scored[:k]

    def top_runs(self, k: int = 3) -> list[RunRecord]:
        """Top-K historical runs across the archive, by composite_fitness."""
        return self._top_runs[:k]

    def record_flips_from_rounds(self, rounds: list[Any], round_num: int) -> None:
        if len(rounds) < 2 or not (rounds[-2].results and rounds[-1].results):
            return
        desc = (
            rounds[-1].candidate_scores[0].changes_description
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
        *,
        dataset_name: str | None,
    ) -> AxisIndex | None:
        """Fresh ``AxisIndex`` + refresh once. Returns ``None`` when store/backend_id missing."""
        if not (store and backend_id):
            return None
        idx = cls()
        idx.refresh(
            store,
            backend_id,
            scorer=scorer,
            scorer_id=scorer_id,
            scorer_formula=scorer_formula,
            dataset_name=dataset_name,
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
        """Fold one entry into ``axis_values``; ``touched_axes`` records the delta for cache invalidation."""
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
        records = [ValueRecord(v, sum(a) / len(a), len(a)) for v, a in values.items() if a]
        records.sort(key=lambda r: -r.mean_accuracy)
        means = [r.mean_accuracy for r in records]
        total = sum(r.sample_count for r in records)

        if len(means) < 2:
            return AxisImpact(axis, 0.0, 0.0, "dead", sample_count=total)

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
        return AxisImpact(
            axis=axis,
            effect_size=round(effect, 4),
            consistency=round(consistency, 4),
            classification=cls,
            top_values=records[:5],
            sample_count=total,
        )


__all__ = ["NOISE_THRESHOLD", "AxisImpact", "AxisIndex", "ValueRecord"]
