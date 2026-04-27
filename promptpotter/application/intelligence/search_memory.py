"""SearchMemory — digest + derived-view façade over SampleIndex (axis side)."""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import combinations, pairwise
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.application.intelligence.sample_index import (
    FailureCluster,
    QueryRecord,
    SampleIndex,
)
from promptpotter.domain.scoring import Scorer, rescore_results

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


class SearchMemory:
    """Materialized view over historical evaluation data (axis side)."""

    def __init__(self, sample_index: SampleIndex | None = None) -> None:
        self.sample_index: SampleIndex = sample_index or SampleIndex()
        self._axis_values: dict[str, dict[str, list[float]]] = defaultdict(
            lambda: defaultdict(list),
        )
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
        """Digest for the L1 generate inbox: failure_clusters, dead_queries, top_axes, top_values."""
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
        """Digest for the L2 refine_strategy inbox: rankings, bottleneck distribution,
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
        """Digest for the L3 modify_plan inbox: clusters, rankings, bottleneck, persistent."""
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

    def _recompute_failure_group_correlations(self) -> bool:
        """Recompute failure-group × axis deltas from current hits/axis values."""
        clusters = self.sample_index.failure_clusters(5)
        if not clusters:
            return False

        # Map each failure mode to the full set of sample_ids whose dominant mode matches.
        # cluster.example_queries only carries the top few; scan every sample's
        # failure-mode counter to recover full membership.
        groups: dict[str, set[int]] = {}
        for cluster in clusters:
            mode = cluster.failure_mode
            sids: set[int] = {
                sid
                for q in cluster.example_queries
                if (sid := self.sample_index.id_for_query(q)) is not None
            }
            for sid in self.sample_index._samples:
                modes = self.sample_index.failure_modes(sid)
                if modes and Counter(modes).most_common(1)[0][0] == mode:
                    sids.add(sid)
            if sids:
                groups[mode] = sids

        if not groups:
            return False

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

        if new_deltas and new_deltas != self._axis_failure_group_deltas:
            self._axis_failure_group_deltas = new_deltas
            return True
        return False

    # ----- ingest / refresh / persist -----

    def refresh(
        self,
        store: Stores,
        backend_id: str,
        scorer: Scorer | None = None,
        scorer_id: str = "none",
        scorer_formula: str | None = None,
    ) -> bool:
        """Incrementally update from new dataset runs; True if anything was added."""
        added = 0
        for run_id, detail in store.dataset_runs.load_since(
            backend_id, self.sample_index._watermark
        ):
            if scorer is not None:
                rescore_results(
                    detail.get("dataset_run_items") or [], scorer, scorer_id, scorer_formula
                )
            self._ingest_run(detail)
            self.sample_index.ingest_run(detail)
            self.sample_index.mark_watermark(run_id)
            added += 1
        if not added:
            return False
        self._cache_axis_impacts = {}
        logger.debug(
            "SearchMemory refreshed: %d new runs (total watermark: %d)",
            added,
            len(self.sample_index._watermark),
        )
        return True

    def on_round_complete(
        self,
        state: Any,
        session: Any,
        config: Any,
        round_num: int,
        full_dataset: list[Any],
    ) -> None:
        """Per-round hook: refresh, persist, recompute correlations, adapt the scoring set."""
        from promptpotter.application.intelligence.scoring_set_adaptation import adapt_scoring_set
        from promptpotter.application.scoring.metrics import compile_query_difficulty

        if (
            session.store
            and session.backend_id
            and self.refresh(
                session.store,
                session.backend_id,
                scorer=session.scorer,
                scorer_id=session.scorer_id,
                scorer_formula=session.scorer_formula,
            )
        ):
            if (
                round_num > 0
                and round_num % 5 == 0
                and self._recompute_failure_group_correlations()
            ):
                logger.info(
                    "SearchMemory: recomputed failure group correlations at round %d", round_num
                )
            base = Path(session.store.base_dir) / "library"
            self.save(base / "search_memory.json")
            self.sample_index.save(base / "sample_index.json")

        if round_num < 2:
            return
        hist = [r.results for r in state.rounds if r.results]
        if len(hist) < 3:
            return
        qd = compile_query_difficulty(hist)
        session.scoring_dataset, info = adapt_scoring_set(
            session.scoring_dataset,
            qd,
            full_dataset,
            seed=config.optimization.seed + round_num,
        )
        if not info.get("unchanged"):
            logger.info("Adaptive scoring-set: %s", info)

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

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "axis_values": {a: dict(v) for a, v in self._axis_values.items()},
                    "axis_failure_group_deltas": self._axis_failure_group_deltas,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    @classmethod
    def ensure_for(
        cls,
        store: Stores | None,
        backend_id: str,
        scorer: Scorer | None = None,
        scorer_id: str = "none",
        scorer_formula: str | None = None,
    ) -> SearchMemory | None:
        """Load + refresh; ``None`` when ``store`` or ``backend_id`` is missing."""
        if not (store and backend_id):
            return None
        base = Path(store.base_dir) / "library"
        sample_index = SampleIndex.load(base / "sample_index.json")
        mem = cls.load(base / "search_memory.json", sample_index=sample_index)
        if mem.refresh(
            store, backend_id, scorer=scorer, scorer_id=scorer_id, scorer_formula=scorer_formula
        ):
            mem.save(base / "search_memory.json")
            mem.sample_index.save(base / "sample_index.json")
        return mem

    @classmethod
    def load(cls, path: Path, sample_index: SampleIndex | None = None) -> SearchMemory:
        mem = cls(sample_index=sample_index)
        if not path.exists():
            return mem
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning("Failed to load SearchMemory from %s — starting fresh", path)
            return mem
        for axis, vals in data.get("axis_values", {}).items():
            for v, accs in vals.items():
                mem._axis_values[axis][v] = accs
        mem._axis_failure_group_deltas = data.get("axis_failure_group_deltas", {})
        return mem

    def _ingest_run(self, detail: dict[str, Any]) -> None:
        """Ingest axis-side state from a dataset_runs/ entry."""
        accuracy = detail.get("scores", {}).get("accuracy", 0.0)
        for node_name, node_config in (detail.get("pipeline_params") or {}).items():
            if isinstance(node_config, dict):
                for param, value in node_config.items():
                    axis = f"{node_name}.{param}" if node_name else param
                    self._axis_values[axis][_value_preview(value)].append(accuracy)
            else:
                self._axis_values[node_name][_value_preview(node_config)].append(accuracy)

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
