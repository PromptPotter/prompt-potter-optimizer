from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import combinations, pairwise
from typing import TYPE_CHECKING, Annotated, Any

from promptpotter.domain.arms import is_leader_eligible
from promptpotter.domain.search_point import PARAM_FORBIDDEN_KEYS
from promptpotter.shared.hashing import shapes_optimizer_prompt


@shapes_optimizer_prompt
def _is_forbidden_axis(axis: str) -> bool:
    _, _, param = axis.partition(".")
    return param in PARAM_FORBIDDEN_KEYS


if TYPE_CHECKING:
    from collections.abc import Sequence

    from promptpotter.application.intelligence.indexes.sample import (
        FailureCluster,
        SampleIndex,
        SampleRecord,
    )
    from promptpotter.domain.pipeline_schema import LocusKind, PipelineSchema
    from promptpotter.domain.results import RoundResult


NOISE_THRESHOLD: Annotated[float, shapes_optimizer_prompt] = 0.02


@shapes_optimizer_prompt
def _value_preview(value: Any) -> str:
    s = str(value)
    return s[:80] if len(s) > 80 else s


@shapes_optimizer_prompt
def _fmt_axis_rankings(
    rankings: list[AxisImpact], peaked_axes: frozenset[str] | None = None
) -> str:
    """Peakedness is tagged INLINE: on its own line L1 reads "highest effect ⇒ mutate" past it."""
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
    kind: LocusKind
    effect_size: float
    consistency: float
    classification: str
    top_values: list[ValueRecord] = field(default_factory=list)
    sample_count: int = 0


@shapes_optimizer_prompt
def _collect(*items: tuple[str, str | None]) -> dict[str, str] | None:
    out = {k: v for k, v in items if v}
    return out or None


def _flips(rounds: Sequence[RoundResult]) -> list[dict[str, Any]]:
    """Each sample whose hit moved between consecutive rounds, read off the rounds alone so a resume reads the same."""
    out: list[dict[str, Any]] = []
    for prev, rr in pairwise(rounds):
        if not (prev.results and rr.results):
            continue
        first = next(iter(rr.panel.values()), None)
        desc = "" if first is None else first.changes_description
        prev_hits = {key: cell.hit for key, cell in prev.results.by_key().items()}
        out.extend(
            {
                "sample_key": key,
                "query": cell.facts.query,
                "round": rr.round,
                "changes_description": desc[:80],
                "old_hit": prev_hits[key],
                "new_hit": cell.hit,
            }
            for key, cell in rr.results.by_key().items()
            if key in prev_hits and cell.hit != prev_hits[key]
        )
    return out


class AxisIndex:
    """Ranks the loci of the cycle's own measured arms; its failure readings remember as its
    sample index does."""

    def __init__(self, sample_index: SampleIndex, schema: PipelineSchema) -> None:
        self.sample_index = sample_index
        self._schema = schema
        self._axis_values: dict[str, dict[str, list[float]]] = defaultdict(
            lambda: defaultdict(list),
        )
        self._axis_kinds: dict[str, LocusKind] = {}
        self._axis_failure_group_deltas: dict[str, dict[str, float]] = {}
        self._flips: list[dict[str, Any]] = []

    @shapes_optimizer_prompt
    def peaked_axes(self) -> frozenset[str]:
        return frozenset(
            axis for axis in self._axis_values if self._axis_value_trend(axis) == "peaked"
        )

    @shapes_optimizer_prompt
    def _ranked_axes(self, only: LocusKind | None) -> list[tuple[str, dict[str, list[float]]]]:
        """``None`` ranks every kind."""
        return [
            (axis, vals)
            for axis, vals in self._axis_values.items()
            if not _is_forbidden_axis(axis) and only in (None, self._axis_kinds[axis])
        ]

    @shapes_optimizer_prompt
    def axis_rankings(self, only: LocusKind | None) -> list[AxisImpact]:
        impacts = [
            i
            for axis, vals in self._ranked_axes(only)
            if (i := self._compute_axis_impact(axis, vals))
        ]
        return sorted(impacts, key=lambda a: -a.effect_size)

    @shapes_optimizer_prompt
    def _exhausted_axes(
        self, only: LocusKind | None, min_values: int = 4, max_effect: float = 0.02
    ) -> list[AxisImpact]:
        out = [
            i
            for axis, vals in self._ranked_axes(only)
            if len(vals) >= min_values
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

    @shapes_optimizer_prompt
    def digest(self, *, only: LocusKind | None) -> dict[str, str] | None:
        rankings5 = self.axis_rankings(only)[:5]
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
        exhausted = self._exhausted_axes(only)
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
        # Counted by sample key: two samples may phrase the same question, so the text is only a LABEL.
        flip_counts = Counter(f["sample_key"] for f in flips)
        flip_labels = {f["sample_key"]: str(f.get("query", "")) for f in flips}
        volatile = [
            (flip_labels.get(key, ""), n) for key, n in flip_counts.most_common(5) if n >= 2
        ]

        return _collect(
            ("axis_rankings", _fmt_axis_rankings(rankings5, peaked) if rankings5 else None),
            ("top_values", top_vals_str),
            # One cluster partitions nothing: on a single-node pipeline it is the node, at 100%.
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

    def refresh(self, rounds: Sequence[RoundResult]) -> None:
        """An arm with no accuracy is skipped: folded as 0.0 it manufactures ``effect_size``."""
        self._flips = _flips(rounds)
        self._axis_values = defaultdict(lambda: defaultdict(list))
        self._axis_kinds = {}
        for rr in rounds:
            for key, arm in rr.arms.items():
                fold = rr.folds.get(key)
                if not is_leader_eligible(arm) or fold is None or fold.accuracy is None:
                    continue
                for locus, value in arm.individual.loci().items():
                    self._axis_kinds[locus.name] = self._schema.locus_kind(*locus)
                    # Keyed whole: two prompts sharing a preview are two values.
                    self._axis_values[locus.name][str(value)].append(fold.accuracy)
        self._recompute_failure_group_correlations()

    @shapes_optimizer_prompt
    def _compute_axis_impact(
        self,
        axis: str,
        values: dict[str, list[float]],
    ) -> AxisImpact | None:
        records = [
            ValueRecord(_value_preview(v), sum(a) / len(a), len(a)) for v, a in values.items() if a
        ]
        records.sort(key=lambda r: -r.mean_accuracy)
        means = [r.mean_accuracy for r in records]
        total = sum(r.sample_count for r in records)
        kind = self._axis_kinds[axis]

        if len(means) < 2:
            return AxisImpact(axis, kind, 0.0, 0.0, "dead", sample_count=total)

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
            kind=kind,
            effect_size=round(effect, 4),
            consistency=round(consistency, 4),
            classification=cls,
            top_values=records[:5],
            sample_count=total,
        )
