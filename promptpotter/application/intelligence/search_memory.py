"""SearchMemory — interaction layer over the cross-campaign index.

SearchMemory is the **digest + derived-view façade** that L1/L2/L3 prompts
consume. The per-sample index storage (hits, failure modes, degradation
counts, flips) lives in :class:`SampleIndex`; SearchMemory composes it
and retains only the axis-side aggregate state + digest builders.

Public surface — consumers import only these:
    * ``digest_for_l1_generate()`` / ``digest_for_l1_critique()``
    * ``digest_for_l2()`` / ``digest_for_l3()``
    * ``on_round_complete()`` / ``record_flips_from_rounds()``
    * ``query_degradation_rate()`` / ``query_degradation_count()``
    * ``axis_rankings()`` / ``top_k_values()``
    * ``refresh()`` / ``ensure_for()`` / ``save()`` / ``load()``

Per-sample queries (tractability, dead samples, failure clusters,
bottleneck distribution) are exposed directly on
``SearchMemory.sample_index`` — call :class:`SampleIndex` instead of
adding a forwarder here.

Persistence is split:
    * ``library/search_memory.json`` — axis-side state (this class)
    * ``library/sample_index.json`` — per-sample state (``SampleIndex``)
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.application.intelligence.sample_index import (
    FailureCluster,
    QueryRecord,
    SampleIndex,
)
from promptpotter.shared.scoring import Scorer, rescore_results

if TYPE_CHECKING:
    from promptpotter.infrastructure.store import Stores

logger = logging.getLogger(__name__)

NOISE_THRESHOLD = 0.02  # delta below this is noise


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


class SearchMemory:
    """Materialized view over historical evaluation data (axis side)."""

    def __init__(self, sample_index: SampleIndex | None = None) -> None:
        self.sample_index: SampleIndex = sample_index or SampleIndex()
        self._axis_values: dict[str, dict[str, list[float]]] = defaultdict(
            lambda: defaultdict(list),
        )
        self._axis_failure_group_deltas: dict[str, dict[str, float]] = {}
        self._cache_axis_impacts: dict[str, AxisImpact | None] = {}

    # --- Parameter Impact ---

    def axis_rankings(self) -> list[AxisImpact]:
        """Return all axes ranked by effect size (descending)."""
        impacts = []
        for axis, values in self._axis_values.items():
            impact = self._compute_axis_impact(axis, values)
            if impact:
                impacts.append(impact)
        impacts.sort(key=lambda a: -a.effect_size)
        return impacts

    def top_k_values(self, axis: str, k: int = 5) -> list[ValueRecord]:
        """Return top-k performing values for an axis."""
        values = self._axis_values.get(axis, {})
        if not values:
            return []
        records = [
            ValueRecord(
                value_preview=v,
                mean_accuracy=sum(accs) / len(accs),
                sample_count=len(accs),
            )
            for v, accs in values.items()
            if accs
        ]
        records.sort(key=lambda r: -r.mean_accuracy)
        return records[:k]

    # --- Degradation (bridged via query→sample_id lookup; null-fallback) ---

    def query_degradation_rate(self, query: str) -> float:
        """Return fraction of evaluations where *query* was degraded."""
        sid = self.sample_index.id_for_query(query)
        if sid is None:
            return 0.0
        return self.sample_index.degradation_rate(sid)

    def query_degradation_count(self, query: str) -> int:
        """Return total number of past evaluations where *query* was degraded."""
        sid = self.sample_index.id_for_query(query)
        if sid is None:
            return 0
        return self.sample_index.degradation_count(sid)

    # --- Digest-internal helpers ---

    def _exhausted_axes(self, min_values: int = 4, max_effect: float = 0.02) -> list[AxisImpact]:
        """Axes thoroughly tested with negligible effect — further exploration wastes budget."""
        exhausted = []
        for axis, values in self._axis_values.items():
            if len(values) < min_values:
                continue
            impact = self._compute_axis_impact(axis, values)
            if impact and impact.effect_size <= max_effect:
                exhausted.append(impact)
        exhausted.sort(key=lambda a: a.effect_size)
        return exhausted

    def _values_tested_count(self, axis: str) -> int:
        """How many distinct values have been tested for *axis*."""
        return len(self._axis_values.get(axis, {}))

    def _axis_value_trend(self, axis: str) -> str:
        """One of: ``increasing``, ``decreasing``, ``peaked``, ``flat``, ``non_numeric``."""
        values = self._axis_values.get(axis, {})
        if len(values) < 3:
            return "flat"

        numeric_pairs: list[tuple[float, float]] = []
        for v_preview, accs in values.items():
            if not accs:
                continue
            try:
                num = float(v_preview)
            except (ValueError, TypeError):
                return "non_numeric"
            numeric_pairs.append((num, sum(accs) / len(accs)))

        if len(numeric_pairs) < 3:
            return "flat"

        numeric_pairs.sort(key=lambda p: p[0])
        means = [p[1] for p in numeric_pairs]

        deltas = [means[i + 1] - means[i] for i in range(len(means) - 1)]
        positive = sum(1 for d in deltas if d > NOISE_THRESHOLD)
        negative = sum(1 for d in deltas if d < -NOISE_THRESHOLD)
        total = len(deltas)

        if positive > total * 0.6 and negative == 0:
            return "increasing"
        if negative > total * 0.6 and positive == 0:
            return "decreasing"
        if positive > 0 and negative > 0:
            peak_idx = means.index(max(means))
            if 0 < peak_idx < len(means) - 1:
                return "peaked"
        return "flat"

    def _parameter_failure_correlation(self, axis: str) -> dict[str, float]:
        """``{failure_mode: delta}`` for ``axis``, or empty if correlations are unset."""
        if not self._axis_failure_group_deltas:
            return {}
        return self._axis_failure_group_deltas.get(axis, {})

    def _format_recent_attributions(self, limit: int = 5) -> str | None:
        """Format recent positive flips (miss→hit) for injection into L1 critique."""
        flips = self.sample_index.all_flips()
        positive = [f for f in flips if f["new_hit"] and not f["old_hit"]]
        if not positive:
            return None
        recent = positive[-limit:]
        parts = []
        for f in recent:
            parts.append(
                f"  Round {f['round']}: {f['query'][:50]} started hitting "
                f"after: {f['changes_description']}"
            )
        return f"{len(positive)} queries improved (last {len(recent)}):\n" + "\n".join(parts)

    def _build_failure_groups(self, clusters: list[FailureCluster]) -> dict[str, set[int]]:
        """Map each failure mode to the full set of sample_ids whose dominant mode matches.

        ``cluster.example_queries`` only holds the top few; we scan every sample's
        failure-mode counter to recover full membership.
        """
        groups: dict[str, set[int]] = {}
        for cluster in clusters:
            mode = cluster.failure_mode
            group_sids: set[int] = set()
            for q in cluster.example_queries:
                sid = self.sample_index.id_for_query(q)
                if sid is not None:
                    group_sids.add(sid)
            for sid in self.sample_index._samples:
                modes = self.sample_index.failure_modes(sid)
                if modes and Counter(modes).most_common(1)[0][0] == mode:
                    group_sids.add(sid)
            if group_sids:
                groups[mode] = group_sids
        return groups

    def _compute_group_hit_rate(self, sids: set[int]) -> float:
        """Average per-sample hit rate across a failure-group's sample ids."""
        if not sids:
            return 0.0
        total = 0.0
        for sid in sids:
            hits = self.sample_index.hits(sid)
            if hits:
                total += sum(hits) / len(hits)
        return total / len(sids)

    def _recompute_failure_group_correlations(self) -> bool:
        """Recompute failure-group × axis deltas from current hits/axis values."""
        clusters = self.sample_index.failure_clusters(5)
        if not clusters:
            return False

        groups = self._build_failure_groups(clusters)
        if not groups:
            return False

        # Hit rate per group is constant across axes — compute once.
        hit_rates = {name: self._compute_group_hit_rate(sids) for name, sids in groups.items()}

        new_deltas: dict[str, dict[str, float]] = {}
        for axis, values in self._axis_values.items():
            if len(values) < 2:
                continue
            impact = self._compute_axis_impact(axis, values)
            if not (impact and impact.effect_size > NOISE_THRESHOLD):
                continue
            for group_name, hit_rate in hit_rates.items():
                correlation = impact.effect_size * (1 - hit_rate)
                if correlation > 0.005:
                    new_deltas.setdefault(axis, {})[group_name] = round(correlation, 4)

        if new_deltas and new_deltas != self._axis_failure_group_deltas:
            self._axis_failure_group_deltas = new_deltas
            return True
        return False

    # --- Prompt digests (one method per consumer layer) ---

    def digest_for_l1_generate(self) -> dict[str, str] | None:
        """Build the SearchMemory digest for the L1 generate inbox.

        Keys: ``failure_clusters`` (top-2, with counts), ``dead_queries``
        (count), ``top_axes`` (top-3 rankings), ``top_values`` (top-2 on
        the winning axis).
        """
        ctx: dict[str, str] = {}

        c2 = self.sample_index.failure_clusters(2)
        if c2:
            ctx["failure_clusters"] = _fmt_clusters(c2, with_counts=True)

        dead = self.sample_index.dead(include_always_hit=False)
        if dead:
            ctx["dead_queries"] = f"{len(dead)} queries never hit"

        rankings3 = self.axis_rankings()[:3]
        if rankings3:
            ctx["top_axes"] = _fmt_axis_rankings(rankings3)
            top_vals = self.top_k_values(rankings3[0].axis, k=2)
            if top_vals:
                ctx["top_values"] = "; ".join(
                    f"{v.value_preview} (acc={v.mean_accuracy:.1%})" for v in top_vals
                )

        return ctx or None

    def digest_for_l1_critique(self) -> dict[str, str] | None:
        """Build the SearchMemory digest for the L1 critique agent.

        Keys: ``discriminating_queries``, ``failure_clusters`` (top-2, no counts),
        ``tractability``, ``exhausted_axes``, ``value_trends``,
        ``improvement_attribution``.
        """
        ctx: dict[str, str] = {}

        disc = self.sample_index.discriminating()
        if disc:
            ctx["discriminating_queries"] = f"{len(disc)} queries vary across configs"

        c2 = self.sample_index.failure_clusters(2)
        if c2:
            ctx["failure_clusters"] = _fmt_clusters(c2, with_counts=False)

        persistent = self.sample_index.persistent_failures(min_streak=3)
        if persistent:
            ctx["tractability"] = _fmt_persistent_failures(persistent)

        exhausted = self._exhausted_axes()
        if exhausted:
            ctx["exhausted_axes"] = "; ".join(
                f"{a.axis} ({self._values_tested_count(a.axis)} values tested, "
                f"effect={a.effect_size:.3f})"
                for a in exhausted[:5]
            )

        trend_parts: list[str] = []
        for a in self.axis_rankings()[:3]:
            trend = self._axis_value_trend(a.axis)
            if trend not in ("flat", "non_numeric"):
                trend_parts.append(f"{a.axis}: {trend}")
        if trend_parts:
            ctx["value_trends"] = "; ".join(trend_parts)

        attributions = self._format_recent_attributions(limit=3)
        if attributions:
            ctx["improvement_attribution"] = attributions

        return ctx or None

    def digest_for_l2(self) -> dict[str, str] | None:
        """Build the SearchMemory digest for the L2 refine_strategy inbox.

        Keys: ``axis_rankings`` (top-5), ``bottleneck_distribution``,
        ``persistent_failures`` (terse), ``failure_group_insights``,
        ``volatile_queries``. Always includes correlations.
        """
        ctx: dict[str, str] = {}

        rankings5 = self.axis_rankings()[:5]
        if rankings5:
            ctx["axis_rankings"] = _fmt_axis_rankings(rankings5)

        bottleneck = self.sample_index.bottleneck_distribution()
        if bottleneck:
            ctx["bottleneck_distribution"] = "; ".join(
                f"{step}: {frac:.0%}" for step, frac in bottleneck.items()
            )

        persistent = self.sample_index.persistent_failures(min_streak=3)
        if persistent:
            ctx["persistent_failures"] = _fmt_persistent_failures(persistent, terse=True)

        rankings3 = self.axis_rankings()[:3]
        if rankings3:
            fg_lines: list[str] = []
            for a in rankings3:
                corr = self._parameter_failure_correlation(a.axis)
                if corr:
                    parts = [
                        f"{mode}: {delta:+.0%}"
                        for mode, delta in sorted(corr.items(), key=lambda x: -abs(x[1]))[:3]
                    ]
                    fg_lines.append(f"{a.axis} → {', '.join(parts)}")
            if fg_lines:
                ctx["failure_group_insights"] = "; ".join(fg_lines)

            flips = self.sample_index.flips(limit=50)
            if flips:
                flip_counts = Counter(f["query"] for f in flips)
                volatile = [(q, n) for q, n in flip_counts.most_common(5) if n >= 2]
                if volatile:
                    ctx["volatile_queries"] = "; ".join(
                        f"{q[:50]} ({n} flips)" for q, n in volatile
                    )

        return ctx or None

    def digest_for_l3(self) -> dict[str, str] | None:
        """Build the SearchMemory digest for the L3 modify_plan inbox.

        Keys: ``failure_clusters`` (top-3, with counts), ``axis_rankings``
        (top-5), ``bottleneck_distribution``, ``persistent_failures`` (terse).
        """
        ctx: dict[str, str] = {}

        c3 = self.sample_index.failure_clusters(3)
        if c3:
            ctx["failure_clusters"] = _fmt_clusters(c3, with_counts=True)

        rankings5 = self.axis_rankings()[:5]
        if rankings5:
            ctx["axis_rankings"] = _fmt_axis_rankings(rankings5)

        bottleneck = self.sample_index.bottleneck_distribution()
        if bottleneck:
            ctx["bottleneck_distribution"] = "; ".join(
                f"{step}: {frac:.0%}" for step, frac in bottleneck.items()
            )

        persistent = self.sample_index.persistent_failures(min_streak=3)
        if persistent:
            ctx["persistent_failures"] = _fmt_persistent_failures(persistent, terse=True)

        return ctx or None

    # --- Lifecycle ---

    def refresh(
        self,
        store: Stores,
        backend_id: str,
        scorer: Scorer | None = None,
        scorer_id: str = "none",
        scorer_formula: str | None = None,
    ) -> bool:
        """Incrementally update from new dataset runs; returns True if anything was added."""
        added = 0
        for run_id, detail in store.dataset_runs.load_since(
            backend_id, self.sample_index._watermark
        ):
            if scorer is not None:
                items = detail.get("dataset_run_items") or []
                rescore_results(items, scorer, scorer_id, scorer_formula)
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
        """Per-round hook: refresh, persist, recompute correlations, adapt the scoring dataset."""
        from promptpotter.application.intelligence.scoring_set_adaptation import (
            adapt_scoring_set,
        )
        from promptpotter.application.scoring.metrics import compile_query_difficulty

        backend_id = session.backend_id
        store = session.store
        scorer = session.scorer
        scorer_id = session.scorer_id
        scorer_formula = session.scorer_formula
        if (
            store
            and backend_id
            and self.refresh(
                store,
                backend_id,
                scorer=scorer,
                scorer_id=scorer_id,
                scorer_formula=scorer_formula,
            )
        ):
            if (
                round_num > 0
                and round_num % 5 == 0
                and self._recompute_failure_group_correlations()
            ):
                logger.info(
                    "SearchMemory: recomputed failure group correlations at round %d",
                    round_num,
                )
            self.save(Path(store.base_dir) / "library" / "search_memory.json")
            self.sample_index.save(Path(store.base_dir) / "library" / "sample_index.json")

        if round_num < 2:
            return
        hist = [r.results for r in state.rounds if r.results]
        if len(hist) < 3:
            return
        qd = compile_query_difficulty(hist)
        session.scoring_dataset, adapt_info = adapt_scoring_set(
            session.scoring_dataset,
            qd,
            full_dataset,
            seed=config.optimization.seed + round_num,
        )
        if not adapt_info.get("unchanged"):
            logger.info("Adaptive scoring-set: %s", adapt_info)

    def record_flips_from_rounds(self, rounds: list[Any], round_num: int) -> None:
        if len(rounds) < 2:
            return
        prev_round = rounds[-2]
        curr_round = rounds[-1]
        if not prev_round.results or not curr_round.results:
            return
        desc = (
            curr_round.candidate_scores[0].get("changes_description", "")
            if curr_round.candidate_scores
            else ""
        )
        flips = self.sample_index.record_flips(
            round_num, desc, prev_round.results, curr_round.results
        )
        if flips:
            logger.debug("Round %d: %d query flips recorded", round_num, flips)

    def save(self, path: Path) -> None:
        data = {
            "axis_values": {axis: dict(vals.items()) for axis, vals in self._axis_values.items()},
            "axis_failure_group_deltas": self._axis_failure_group_deltas,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @classmethod
    def ensure_for(
        cls,
        store: Stores | None,
        backend_id: str,
        scorer: Scorer | None = None,
        scorer_id: str = "none",
        scorer_formula: str | None = None,
    ) -> SearchMemory | None:
        """Load + refresh; returns ``None`` when ``store`` or ``backend_id`` is missing."""
        if not (store and backend_id):
            return None
        base = Path(store.base_dir) / "library"
        sample_index = SampleIndex.load(base / "sample_index.json")
        mem = cls.load(base / "search_memory.json", sample_index=sample_index)
        if mem.refresh(
            store,
            backend_id,
            scorer=scorer,
            scorer_id=scorer_id,
            scorer_formula=scorer_formula,
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

    # --- Internals ---

    def _ingest_run(self, detail: dict[str, Any]) -> None:
        """Ingest axis-side state from a dataset_runs/ entry."""
        accuracy = detail.get("scores", {}).get("accuracy", 0.0)
        pipeline_params = detail.get("pipeline_params") or {}

        for node_name, node_config in pipeline_params.items():
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

        all_means = []
        total_samples = 0
        for _v, accs in values.items():
            if accs:
                all_means.append(sum(accs) / len(accs))
                total_samples += len(accs)

        impact: AxisImpact | None
        if len(all_means) < 2:
            impact = AxisImpact(
                axis=axis,
                effect_size=0.0,
                consistency=0.0,
                classification="dead",
                sample_count=total_samples,
            )
            self._cache_axis_impacts[axis] = impact
            return impact

        deltas = []
        for i in range(len(all_means)):
            for j in range(i + 1, len(all_means)):
                deltas.append(abs(all_means[i] - all_means[j]))

        effect = sum(deltas) / len(deltas) if deltas else 0.0
        above_noise = sum(1 for d in deltas if d > NOISE_THRESHOLD)
        consistency = above_noise / len(deltas) if deltas else 0.0

        if consistency >= 0.7:
            classification = "consistently_impactful"
        elif consistency >= 0.3:
            classification = "sometimes_impactful"
        else:
            classification = "dead"

        top_values = self.top_k_values(axis, k=5)

        impact = AxisImpact(
            axis=axis,
            effect_size=round(effect, 4),
            consistency=round(consistency, 4),
            classification=classification,
            top_values=top_values,
            sample_count=total_samples,
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
