from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import combinations, pairwise
from typing import TYPE_CHECKING, Annotated, Any

from promptpotter.application.scoring.metrics import CellFold
from promptpotter.domain.scoring import is_hit
from promptpotter.domain.search_point import PARAM_FORBIDDEN_KEYS
from promptpotter.shared.hashing import shapes_optimizer_prompt


@shapes_optimizer_prompt
def _is_forbidden_axis(axis: str) -> bool:
    _, _, param = axis.partition(".")
    return param in PARAM_FORBIDDEN_KEYS


if TYPE_CHECKING:
    from promptpotter.application.intelligence.indexes.sample import (
        FailureCluster,
        SampleIndex,
        SampleRecord,
    )

logger = logging.getLogger(__name__)


NOISE_THRESHOLD: Annotated[float, shapes_optimizer_prompt] = 0.02


@shapes_optimizer_prompt
def _value_preview(value: Any) -> str:
    s = str(value)
    return s[:80] if len(s) > 80 else s


@shapes_optimizer_prompt
def _fmt_axis_rankings(
    rankings: list[AxisImpact], peaked_axes: frozenset[str] | None = None
) -> str:
    """The top-axes line, with peakedness tagged INLINE. Split across two lines it let L1 read
    "highest effect ⇒ mutate" without ever seeing that the parent's value IS the measured peak."""
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


@shapes_optimizer_prompt
def _fmt_clusters(clusters: list[FailureCluster], *, with_counts: bool) -> str:
    if with_counts:
        return "; ".join(
            f"{c.failure_mode} ({c.fraction:.0%}, {c.sample_count} samples)" for c in clusters
        )
    return "; ".join(f"{c.failure_mode} ({c.fraction:.0%})" for c in clusters)


@shapes_optimizer_prompt
def _fmt_bottleneck(bottleneck: dict[str, float] | None) -> str | None:
    if not bottleneck:
        return None
    return "; ".join(f"{step}: {frac:.0%}" for step, frac in bottleneck.items())


@shapes_optimizer_prompt
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
    value_preview: str
    mean_accuracy: float
    sample_count: int


@dataclass
class AxisImpact:
    axis: str
    effect_size: float
    consistency: float
    classification: str
    top_values: list[ValueRecord] = field(default_factory=list)
    sample_count: int = 0


@dataclass
class RunRecord:
    run_id: str
    name: str
    accuracy: float
    composite: float
    total: int


@shapes_optimizer_prompt
def _collect(*items: tuple[str, str | None]) -> dict[str, str] | None:
    out = {k: v for k, v in items if v}
    return out or None


class AxisIndex:
    """Derived axis-keyed view (axis → value → [accuracy]) over the runs a ``SampleIndex`` read,
    plus the per-sample flips between consecutive rounds of the cycle reading it."""

    def __init__(self, sample_index: SampleIndex) -> None:
        self.sample_index = sample_index
        self._axis_values: dict[str, dict[str, list[float]]] = defaultdict(
            lambda: defaultdict(list),
        )
        self._axis_seen_runs: set[str] = set()
        self._axis_failure_group_deltas: dict[str, dict[str, float]] = {}
        self._top_runs: list[RunRecord] = []
        self._flips: list[dict[str, Any]] = []
        # The sample index's `generation` last folded here; `None` until the first refresh.
        self._folded: int | None = None

    # ----- axis analytics -----

    @shapes_optimizer_prompt
    def peaked_axes(self) -> frozenset[str]:
        return frozenset(
            axis for axis in self._axis_values if self._axis_value_trend(axis) == "peaked"
        )

    @shapes_optimizer_prompt
    def axis_rankings(self) -> list[AxisImpact]:
        impacts = [
            i
            for axis, vals in self._axis_values.items()
            if not _is_forbidden_axis(axis) and (i := self._compute_axis_impact(axis, vals))
        ]
        return sorted(impacts, key=lambda a: -a.effect_size)

    @shapes_optimizer_prompt
    def _exhausted_axes(self, min_values: int = 4, max_effect: float = 0.02) -> list[AxisImpact]:
        out = [
            i
            for axis, vals in self._axis_values.items()
            if not _is_forbidden_axis(axis)
            and len(vals) >= min_values
            and (i := self._compute_axis_impact(axis, vals))
            and i.effect_size <= max_effect
        ]
        return sorted(out, key=lambda a: a.effect_size)

    @shapes_optimizer_prompt
    def _axis_value_trend(self, axis: str) -> str:
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

    @shapes_optimizer_prompt
    def digest(self) -> dict[str, str] | None:
        """Layer-agnostic axis-keyed digest — one payload into every L1/L2/L3 prompt. Per-layer filtering,
        if it ever returns, lives in the renderers and not here."""
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

        flips = self._flips[-50:] if rankings5 else []
        # Counted by sample_id, the cell's identity — `record_flips_from_rounds` detects them
        # that way. Bucketing by the raw query text merged two samples that phrase the same
        # question into one inflated volatility score. The text is the LABEL, not the key.
        flip_counts = Counter(f["sample_id"] for f in flips)
        flip_labels = {f["sample_id"]: str(f.get("query", "")) for f in flips}
        volatile = [
            (flip_labels.get(sid, ""), n) for sid, n in flip_counts.most_common(5) if n >= 2
        ]

        return _collect(
            ("axis_rankings", _fmt_axis_rankings(rankings5, peaked) if rankings5 else None),
            ("top_values", top_vals_str),
            # One cluster partitions nothing — on a single-node pipeline it is "every failure is
            # in the node", at 100%.
            (
                "failure_clusters",
                _fmt_clusters(clusters, with_counts=True) if len(clusters) > 1 else None,
            ),
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

    @shapes_optimizer_prompt
    def _format_recent_attributions(self, limit: int = 5) -> str | None:
        positive = [f for f in self._flips if f["new_hit"] and not f["old_hit"]]
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

    # ----- refresh -----

    def refresh(self) -> None:
        """Fold what the sample index read at its last refresh; a no-op until it reads again, so
        the digest moves exactly when the archive view it is derived from does."""
        if self._folded == self.sample_index.generation:
            return
        for entry, reading in self.sample_index.runs:
            run_id = entry.get("run_id", "")
            if run_id in self._axis_seen_runs:
                continue
            self._fold_entry(self._axis_values, entry, reading)
            self._axis_seen_runs.add(run_id)
        self._recompute_failure_group_correlations()
        self._refresh_top_runs(self.sample_index.runs)
        self._folded = self.sample_index.generation

    def _refresh_top_runs(
        self, entries: list[tuple[dict[str, Any], CellFold]], k: int = 10
    ) -> None:
        """Top-K by (composite_fitness, accuracy) desc. Only the modal ``total`` count is kept: an 8/20
        composite is not comparable with a 20/20 one, and mixing them inflates the leaderboard. A
        one-cell run reads that cell, not a configuration, and backfills mint enough of them to
        become the mode, so they are excluded."""
        all_totals = [scores["total"] for _, scores in entries if scores["total"] > 1]
        if not all_totals:
            self._top_runs = []
            return
        modal_total = Counter(all_totals).most_common(1)[0][0]

        # One run can have several archive entries (e.g. per-sample backfill rows);
        # collapse to the best record per run_id so the leaderboard never lists the
        # same run twice (wasted bytes + a misleading panel for L1/L2).
        best_by_run: dict[str, RunRecord] = {}
        for entry, scores in entries:
            total = scores["total"]
            if total != modal_total:
                continue
            # An absence is not a measurement: a run that read no cell must not enter the
            # leaderboard as a 0% run (the rule `noise_floor.py` already states).
            accuracy, composite = scores["accuracy"], scores["composite_fitness"]
            if accuracy is None or composite is None:
                continue
            run_id = entry.get("run_id", "")
            rec = RunRecord(
                run_id=run_id,
                name=entry.get("name", ""),
                accuracy=accuracy,
                composite=composite,
                total=total,
            )
            prev = best_by_run.get(run_id)
            if prev is None or (rec.composite, rec.accuracy) > (prev.composite, prev.accuracy):
                best_by_run[run_id] = rec
        scored = sorted(best_by_run.values(), key=lambda r: (-r.composite, -r.accuracy))
        self._top_runs = scored[:k]

    def top_runs(self, k: int = 3) -> list[RunRecord]:
        return self._top_runs[:k]

    def record_flips_from_rounds(self, rounds: list[Any], round_num: int) -> None:
        if len(rounds) < 2 or not (rounds[-2].results and rounds[-1].results):
            return
        desc = (
            rounds[-1].candidate_scores[0].changes_description
            if rounds[-1].candidate_scores
            else ""
        )
        prev_hits: dict[int, bool] = {}
        for r in rounds[-2].results:
            sid = r.get("sample_id")
            if sid is not None:
                prev_hits[sid] = is_hit(r.get("fitness"))

        count = 0
        for r in rounds[-1].results:
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
                        "changes_description": desc[:80],
                        "old_hit": old_hit,
                        "new_hit": new_hit,
                    }
                )
                count += 1
        if count:
            logger.debug("Round %d: %d query flips recorded", round_num, count)

    # ----- helpers -----

    @staticmethod
    def _fold_entry(
        axis_values: dict[str, dict[str, list[float]]],
        entry: dict[str, Any],
        scores: CellFold,
    ) -> None:
        """An entry with no accuracy — an outer L4 cell, whose measurand is ``mean_round_delta`` — is
        skipped, never folded as 0.0, which manufactures ``effect_size`` against every real arm."""
        recorded = scores["accuracy"]
        if recorded is None:
            return
        accuracy = float(recorded)
        for node_name, node_config in (entry.get("pipeline_params") or {}).items():
            if isinstance(node_config, dict):
                for param, value in node_config.items():
                    axis = f"{node_name}.{param}" if node_name else param
                    axis_values[axis][_value_preview(value)].append(accuracy)
            else:
                axis_values[node_name][_value_preview(node_config)].append(accuracy)

    @shapes_optimizer_prompt
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


__all__ = ["NOISE_THRESHOLD", "AxisIndex"]
