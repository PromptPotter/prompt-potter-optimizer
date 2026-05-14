"""AxisIndex — derived axis-keyed view + digest API for L1/L2/L3 prompts."""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import combinations, pairwise
from typing import TYPE_CHECKING, Any

from promptpotter.application.intelligence.indexes.config import ConfigIndex
from promptpotter.application.intelligence.indexes.format import (
    _fmt_axis_rankings,
    _fmt_bottleneck,
    _fmt_clusters,
    _fmt_persistent_failures,
    _value_preview,
)
from promptpotter.application.intelligence.indexes.sample import SampleIndex
from promptpotter.application.scoring.formula import rescore_results
from promptpotter.domain.scoring import Scorer
from promptpotter.infrastructure.store import archive_views

if TYPE_CHECKING:
    from promptpotter.infrastructure.store import Stores

logger = logging.getLogger(__name__)


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

    def __init__(
        self,
        sample_index: SampleIndex | None = None,
        config_index: ConfigIndex | None = None,
    ) -> None:
        self.sample_index: SampleIndex = sample_index or SampleIndex()
        self.config_index: ConfigIndex = config_index or ConfigIndex()
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
        trend_parts = [
            f"{a.axis}: {t}"
            for a in rankings5[:3]
            if (t := self._axis_value_trend(a.axis)) not in ("flat", "non_numeric")
        ]

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
            ("value_trends", "; ".join(trend_parts) if trend_parts else None),
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
        for run_id, detail in archive_views.runs_since(
            store, backend_id, self.sample_index._seen_runs
        ):
            if scorer is not None:
                rescore_results(detail.get("measurements") or [], scorer, scorer_id, scorer_formula)
            self.sample_index.ingest_run(detail)
            self.sample_index.mark_seen(run_id)
            self.config_index.ingest_run(detail)
            added += 1

        # Axis side: fold only new index entries into the persistent
        # ``_axis_values``, tracking which axes the delta touched so we
        # can invalidate exactly those impact-cache slots.
        touched_axes: set[str] = set()
        for entry in archive_views.list_runs(store, backend_id):
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


__all__ = ["NOISE_THRESHOLD", "AxisImpact", "AxisIndex", "ValueRecord"]
