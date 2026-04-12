"""SearchMemory — cross-campaign intelligence materialized view.

A persistent, incrementally-updated statistical index over ALL historical
search points and their evaluation results.  Exposes atomic data accessors
for consumers (recon_advisor, L1, L2, critique) to compose what they need.

Persisted to disk at ``{backend_id}/search_memory.json``.  Updated lazily
via ``refresh()`` which loads only new dataset runs since the last watermark.
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.shared.errors import is_error_result

if TYPE_CHECKING:
    from promptpotter.infrastructure.store.project_store import ProjectStore

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
    effect_size: float  # mean |accuracy delta| when this axis changes
    consistency: float  # fraction of comparisons where |delta| > noise
    classification: str  # "consistently_impactful" | "sometimes_impactful" | "dead"
    top_values: list[ValueRecord] = field(default_factory=list)
    sample_count: int = 0


@dataclass
class QueryRecord:
    """Per-query pattern summary across evaluations."""

    query: str
    hit_rate: float
    n_measurements: int
    variance: float  # hit/miss variance across configs
    dominant_failure_mode: str = ""  # most common terminated_at


@dataclass
class FailureCluster:
    """Queries grouped by shared failure reason."""

    failure_mode: str
    query_count: int
    fraction: float
    example_queries: list[str] = field(default_factory=list)


class SearchMemory:
    """Materialized view over all historical evaluation data.

    Three analysis pillars:
    - Parameter Impact: which axes matter, what values work
    - Query Patterns: tractability, discriminative power, sensitive axes
    - Failure Modes: bottleneck distribution, failure clusters
    """

    def __init__(self) -> None:
        # Watermark: set of run_ids already processed
        self._watermark: set[str] = set()

        # Parameter Impact internals
        # {axis: {value_preview: [accuracies]}}
        self._axis_values: dict[str, dict[str, list[float]]] = defaultdict(
            lambda: defaultdict(list),
        )

        # Query Patterns internals
        # {query: [hit_bools]}
        self._query_hits: dict[str, list[bool]] = defaultdict(list)
        # {query: [terminated_at_values]}
        self._query_failure_modes: dict[str, list[str]] = defaultdict(list)

        # Degradation tracking
        # {query: degradation_count}
        self._query_degradation_counts: dict[str, int] = defaultdict(int)

        # Failure Modes internals
        # {terminated_at: count}
        self._bottleneck_counts: dict[str, int] = defaultdict(int)
        self._total_failures: int = 0

        # Failure group analysis (populated by ingest_failure_groups)
        # {query: [axis_names]}
        self._query_axis_sensitivity: dict[str, list[str]] = {}
        # {axis: {failure_mode: delta}}
        self._axis_failure_group_deltas: dict[str, dict[str, float]] = {}

        # Query improvement attribution — tracks what caused hit/miss flips
        # [{query, round, changes_description, old_hit, new_hit}]
        self._query_flips: list[dict[str, Any]] = []

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

    # --- Query Patterns ---

    def query_tractability(self) -> list[QueryRecord]:
        """Return all queries with their tractability profiles."""
        return self._build_query_records()

    def discriminating_queries(self, min_variance: float = 0.1) -> list[QueryRecord]:
        """Return queries whose outcome varies across configurations."""
        return [q for q in self._build_query_records() if q.variance >= min_variance]

    def dead_queries(self, max_hit_rate: float = 0.0) -> list[QueryRecord]:
        """Return queries that never hit (or always hit if max_hit_rate=1.0)."""
        records = self._build_query_records()
        return [q for q in records if q.hit_rate <= max_hit_rate or q.hit_rate >= 1.0]

    def query_sensitive_axes(self, query: str) -> list[str]:
        """Return axes that most affect this query's outcome.

        Uses failure group analysis results if available. Falls back to empty
        list when no per-query scan data has been ingested.
        """
        if not self._query_axis_sensitivity:
            return []
        return self._query_axis_sensitivity.get(query, [])

    def persistent_failures(self, min_streak: int = 3) -> list[QueryRecord]:
        """Return queries that have failed in the last N consecutive evaluations.

        Classifies queries by failure severity:
        - Intractable: never hit across all evaluations (hit_rate == 0)
        - Chronic: failed last min_streak evaluations but not always
        - Intermittent: variable (not returned)

        Only returns intractable + chronic queries.
        """
        records = [
            r
            for r in self._build_query_records()
            if len(self._query_hits.get(r.query, [])) >= min_streak
            and not any(self._query_hits[r.query][-min_streak:])
        ]
        records.sort(key=lambda r: r.hit_rate)  # intractable first
        return records

    # --- Degradation ---

    def query_degradation_rate(self, query: str) -> float:
        """Return fraction of evaluations where *query* was degraded."""
        n_measurements = len(self._query_hits.get(query, []))
        if n_measurements == 0:
            return 0.0
        return self._query_degradation_counts.get(query, 0) / n_measurements

    def query_degradation_count(self, query: str) -> int:
        """Return total number of past evaluations where *query* was degraded."""
        return self._query_degradation_counts.get(query, 0)

    # --- Failure Modes ---

    def bottleneck_distribution(self) -> dict[str, float]:
        """Return {terminated_at_step: fraction_of_failures}."""
        if self._total_failures == 0:
            return {}
        return {
            step: count / self._total_failures
            for step, count in sorted(
                self._bottleneck_counts.items(),
                key=lambda x: -x[1],
            )
        }

    def failure_clusters(self, max_clusters: int = 5) -> list[FailureCluster]:
        """Return queries grouped by dominant failure mode."""
        mode_queries: dict[str, list[str]] = defaultdict(list)
        for query, modes in self._query_failure_modes.items():
            if modes:
                dominant = Counter(modes).most_common(1)[0][0]
                mode_queries[dominant].append(query)

        total = sum(len(qs) for qs in mode_queries.values())
        clusters = []
        for mode, queries in sorted(mode_queries.items(), key=lambda x: -len(x[1])):
            clusters.append(
                FailureCluster(
                    failure_mode=mode,
                    query_count=len(queries),
                    fraction=len(queries) / total if total else 0.0,
                    example_queries=queries[:3],
                )
            )
            if len(clusters) >= max_clusters:
                break
        return clusters

    def exhausted_axes(self, min_values: int = 4, max_effect: float = 0.02) -> list[AxisImpact]:
        """Return axes that have been thoroughly tested with negligible effect.

        An axis is "exhausted" when we've tried ``min_values``+ distinct values
        and the effect size is below ``max_effect`` — further exploration is
        unlikely to yield improvement.
        """
        exhausted = []
        for axis, values in self._axis_values.items():
            if len(values) < min_values:
                continue
            impact = self._compute_axis_impact(axis, values)
            if impact and impact.effect_size <= max_effect:
                exhausted.append(impact)
        exhausted.sort(key=lambda a: a.effect_size)
        return exhausted

    def values_tested_count(self, axis: str) -> int:
        """Return how many distinct values have been tested for *axis*."""
        return len(self._axis_values.get(axis, {}))

    def axis_value_trend(self, axis: str) -> str:
        """Analyze accuracy trend across numeric values for an axis.

        Returns one of: "increasing", "decreasing", "peaked", "flat", or
        "non_numeric" when values can't be parsed as numbers.
        """
        values = self._axis_values.get(axis, {})
        if len(values) < 3:
            return "flat"

        # Try to parse value previews as numbers
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

        # Compute successive deltas
        deltas = [means[i + 1] - means[i] for i in range(len(means) - 1)]
        positive = sum(1 for d in deltas if d > NOISE_THRESHOLD)
        negative = sum(1 for d in deltas if d < -NOISE_THRESHOLD)
        total = len(deltas)

        if positive > total * 0.6 and negative == 0:
            return "increasing"
        if negative > total * 0.6 and positive == 0:
            return "decreasing"
        if positive > 0 and negative > 0:
            # Check for peak: increases then decreases
            peak_idx = means.index(max(means))
            if 0 < peak_idx < len(means) - 1:
                return "peaked"
        return "flat"

    def parameter_failure_correlation(self, axis: str) -> dict[str, float]:
        """Return failure-mode correlation for an axis.

        Returns {failure_mode: delta} from failure group analysis if available.
        """
        if not self._axis_failure_group_deltas:
            return {}
        return self._axis_failure_group_deltas.get(axis, {})

    def ingest_failure_groups(self, result: Any) -> None:
        """Ingest failure group sensitivity results.

        Populates per-query sensitive axes and per-axis failure correlations
        from ``FailureGroupResult``.
        """
        self._query_axis_sensitivity.clear()
        self._axis_failure_group_deltas.clear()

        for s in result.sensitivities:
            # Per-axis → failure mode deltas
            self._axis_failure_group_deltas.setdefault(s.axis, {})[s.failure_group] = s.delta
            # Per-query → sensitive axes (for queries in this failure group)
            for query in result.groups.get(s.failure_group, []):
                if query not in self._query_axis_sensitivity:
                    self._query_axis_sensitivity[query] = []
                if s.axis not in self._query_axis_sensitivity[query]:
                    self._query_axis_sensitivity[query].append(s.axis)

    def record_query_flips(
        self,
        round_num: int,
        changes_description: str,
        prev_results: list[dict],
        new_results: list[dict],
    ) -> int:
        """Record queries that flipped hit/miss between rounds.

        Returns count of flips recorded.
        """
        prev_hits: dict[str, bool] = {}
        for r in prev_results:
            q = r.get("query", "")
            if q:
                prev_hits[q] = bool(r.get("hit"))

        count = 0
        for r in new_results:
            q = r.get("query", "")
            if not q or q not in prev_hits:
                continue
            new_hit = bool(r.get("hit"))
            old_hit = prev_hits[q]
            if new_hit != old_hit:
                self._query_flips.append(
                    {
                        "query": q,
                        "round": round_num,
                        "changes_description": changes_description[:80],
                        "old_hit": old_hit,
                        "new_hit": new_hit,
                    }
                )
                count += 1
        return count

    def query_flip_history(self, query: str | None = None, limit: int = 20) -> list[dict]:
        """Return recent query hit/miss flips, optionally filtered by query."""
        flips = self._query_flips
        if query:
            flips = [f for f in flips if f["query"] == query]
        return flips[-limit:]

    def format_recent_attributions(self, limit: int = 5) -> str | None:
        """Format recent positive flips (miss→hit) for injection into critique."""
        positive = [f for f in self._query_flips if f["new_hit"] and not f["old_hit"]]
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

    def recompute_failure_group_correlations(self) -> bool:
        """Recompute failure group × axis correlations from internal data.

        Uses ``_query_failure_modes`` to build failure groups and
        ``_query_hits`` + ``_axis_values`` to estimate which axes
        correlate with improvements for each group.

        Returns True if correlations were updated.
        """
        clusters = self.failure_clusters(5)
        if not clusters:
            return False

        # Build group membership: {failure_mode: set of queries}
        groups: dict[str, set[str]] = {}
        for cluster in clusters:
            # Include ALL queries with this dominant failure mode, not just examples
            mode = cluster.failure_mode
            group_queries: set[str] = set()
            for query, modes in self._query_failure_modes.items():
                if modes:
                    dominant = Counter(modes).most_common(1)[0][0]
                    if dominant == mode:
                        group_queries.add(query)
            if group_queries:
                groups[mode] = group_queries

        if not groups:
            return False

        # For each axis, compare mean hit rate across values for each group
        new_deltas: dict[str, dict[str, float]] = {}
        new_sensitivity: dict[str, list[str]] = {}

        for axis, values in self._axis_values.items():
            if len(values) < 2:
                continue

            for group_name, group_queries in groups.items():
                # Correlate axis effect with failure group membership
                group_hit_rate = 0.0
                for q in group_queries:
                    hits = self._query_hits.get(q, [])
                    if hits:
                        group_hit_rate += sum(hits) / len(hits)
                if group_queries:
                    group_hit_rate /= len(group_queries)

                # Check if the axis has meaningful variation
                impact = self._compute_axis_impact(axis, values)
                if impact and impact.effect_size > NOISE_THRESHOLD:
                    # Store the correlation as effect_size × (1 - group_hit_rate)
                    # Higher correlation for axes with large effect on hard groups
                    correlation = impact.effect_size * (1 - group_hit_rate)
                    if correlation > 0.005:
                        new_deltas.setdefault(axis, {})[group_name] = round(correlation, 4)
                        for q in group_queries:
                            if q not in new_sensitivity:
                                new_sensitivity[q] = []
                            if axis not in new_sensitivity[q]:
                                new_sensitivity[q].append(axis)

        if new_deltas:
            self._axis_failure_group_deltas = new_deltas
            self._query_axis_sensitivity = new_sensitivity
            return True
        return False

    # --- Prompt digests ---

    def to_l1_digest(self) -> dict[str, str] | None:
        """SearchMemory context dict for the L1 generation prompt."""
        ctx: dict[str, str] = {}

        clusters = self.failure_clusters(3)
        if clusters:
            ctx["failure_clusters"] = _fmt_clusters(clusters, with_counts=True)

        dead = self.dead_queries()
        if dead:
            ctx["dead_queries"] = f"{len(dead)} queries never hit"

        rankings = self.axis_rankings()[:3]
        if rankings:
            ctx["top_axes"] = _fmt_axis_rankings(rankings)
            top_vals = self.top_k_values(rankings[0].axis, k=3)
            if top_vals:
                ctx["top_values"] = "; ".join(
                    f"{v.value_preview} (acc={v.mean_accuracy:.1%})" for v in top_vals
                )

        return ctx or None

    def to_critique_digest(self) -> dict[str, str] | None:
        """SearchMemory context dict for the critique agent.

        Surfaces discriminating queries, failure clusters, tractability profiles,
        exhausted axes, value trends, and improvement attribution.
        """
        ctx: dict[str, str] = {}

        disc = self.discriminating_queries()
        if disc:
            ctx["discriminating_queries"] = f"{len(disc)} queries vary across configs"

        fc = self.failure_clusters(3)
        if fc:
            ctx["failure_clusters"] = _fmt_clusters(fc, with_counts=False)

        persistent = self.persistent_failures(min_streak=3)
        if persistent:
            ctx["tractability"] = _fmt_persistent_failures(persistent)

        exhausted = self.exhausted_axes()
        if exhausted:
            ctx["exhausted_axes"] = "; ".join(
                f"{a.axis} ({self.values_tested_count(a.axis)} values tested, "
                f"effect={a.effect_size:.3f})"
                for a in exhausted[:5]
            )

        rankings = self.axis_rankings()[:3]
        trend_parts = []
        for a in rankings:
            trend = self.axis_value_trend(a.axis)
            if trend not in ("flat", "non_numeric"):
                trend_parts.append(f"{a.axis}: {trend}")
        if trend_parts:
            ctx["value_trends"] = "; ".join(trend_parts)

        attributions = self.format_recent_attributions(limit=3)
        if attributions:
            ctx["improvement_attribution"] = attributions

        return ctx or None

    def to_strategic_digest(
        self,
        *,
        include_correlations: bool = False,
        include_clusters: bool = False,
    ) -> dict[str, str] | None:
        """SearchMemory context dict for L2/L3 strategic prompts.

        Base: axis rankings, bottleneck distribution, persistent failures.
        L2 opts in via ``include_correlations`` (failure-group × axis, volatile queries).
        L3 opts in via ``include_clusters`` (failure clusters with counts).
        """
        ctx: dict[str, str] = {}

        rankings = self.axis_rankings()[:5]
        if rankings:
            ctx["axis_rankings"] = _fmt_axis_rankings(rankings)

        bottleneck = self.bottleneck_distribution()
        if bottleneck:
            ctx["bottleneck_distribution"] = "; ".join(
                f"{step}: {frac:.0%}" for step, frac in bottleneck.items()
            )

        persistent = self.persistent_failures(min_streak=3)
        if persistent:
            ctx["persistent_failures"] = _fmt_persistent_failures(persistent, terse=True)

        if include_clusters:
            clusters = self.failure_clusters(3)
            if clusters:
                ctx["failure_clusters"] = _fmt_clusters(clusters, with_counts=True)

        if include_correlations and rankings:
            fg_lines = []
            for a in rankings[:3]:
                corr = self.parameter_failure_correlation(a.axis)
                if corr:
                    parts = [
                        f"{mode}: {delta:+.0%}"
                        for mode, delta in sorted(corr.items(), key=lambda x: -abs(x[1]))[:3]
                    ]
                    fg_lines.append(f"{a.axis} → {', '.join(parts)}")
            if fg_lines:
                ctx["failure_group_insights"] = "; ".join(fg_lines)

            flips = self.query_flip_history(limit=50)
            if flips:
                flip_counts = Counter(f["query"] for f in flips)
                volatile = [(q, n) for q, n in flip_counts.most_common(5) if n >= 2]
                if volatile:
                    ctx["volatile_queries"] = "; ".join(
                        f"{q[:50]} ({n} flips)" for q, n in volatile
                    )

        return ctx or None

    # --- Lifecycle ---

    def refresh(self, store: ProjectStore, backend_id: str) -> bool:
        """Incrementally update from new dataset runs.

        Returns True if new data was incorporated.
        """
        added = 0
        for run_id, detail in store.dataset_runs.load_since(backend_id, self._watermark):
            self._ingest_run(detail)
            self._watermark.add(run_id)
            added += 1
        if not added:
            return False
        logger.debug(
            "SearchMemory refreshed: %d new runs (total watermark: %d)",
            added,
            len(self._watermark),
        )
        return True

    def on_round_complete(
        self,
        state: Any,
        env: Any,
        config: Any,
        round_num: int,
        full_dataset: list[dict[str, Any]],
    ) -> None:
        """Per-round hook: refresh from disk, persist, recompute correlations,
        and adapt the scoring dataset via query-difficulty analysis.

        Owns the round-boundary work that used to live in the runner.
        """
        from promptpotter.application.intelligence.scoring_set_adaptation import (
            adapt_scoring_set,
        )
        from promptpotter.application.scoring.metrics import compile_query_difficulty

        # Refresh + correlations + save
        store = env.scoring_ctx.store if env.scoring_ctx else None
        if store and self.refresh(store, config.backend_id):
            if round_num > 0 and round_num % 5 == 0 and self.recompute_failure_group_correlations():
                logger.info(
                    "SearchMemory: recomputed failure group correlations at round %d",
                    round_num,
                )
            self.save(Path(store.base_dir) / config.backend_id / "search_memory.json")

        # Adaptive scoring-set swap (needs 3+ rounds of history)
        if round_num < 2:
            return
        hist = [r.results for r in state.rounds if r.results]
        if len(hist) < 3:
            return
        qd = compile_query_difficulty(hist)
        env.scoring_dataset, adapt_info = adapt_scoring_set(
            env.scoring_dataset,
            qd,
            full_dataset,
            seed=config.seed + round_num,
        )
        if not adapt_info.get("unchanged"):
            logger.info("Adaptive scoring-set: %s", adapt_info)

    def record_flips_from_rounds(self, rounds: list[Any], round_num: int) -> None:
        """Record query flips between the last two rounds."""
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
        flips = self.record_query_flips(round_num, desc, prev_round.results, curr_round.results)
        if flips:
            logger.debug("Round %d: %d query flips recorded", round_num, flips)

    def save(self, path: Path) -> None:
        """Persist to disk."""
        data = {
            "watermark": sorted(self._watermark),
            "axis_values": {axis: dict(vals.items()) for axis, vals in self._axis_values.items()},
            "query_hits": dict(self._query_hits),
            "query_failure_modes": dict(self._query_failure_modes),
            "bottleneck_counts": dict(self._bottleneck_counts),
            "total_failures": self._total_failures,
            "query_degradation_counts": dict(self._query_degradation_counts),
            "query_axis_sensitivity": self._query_axis_sensitivity,
            "axis_failure_group_deltas": self._axis_failure_group_deltas,
            "query_flips": self._query_flips,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @classmethod
    def ensure_for(
        cls,
        store: ProjectStore | None,
        backend_id: str,
    ) -> SearchMemory | None:
        """Load, refresh from historical data, and persist if changed.

        Returns ``None`` when ``store`` or ``backend_id`` is missing —
        SearchMemory requires both to exist.  Callers wire the result onto
        ``LoopState.search_memory`` and ``ScoringEnv.search_memory``.
        """
        if not (store and backend_id):
            return None
        path = Path(store.base_dir) / backend_id / "search_memory.json"
        mem = cls.load(path)
        if mem.refresh(store, backend_id):
            mem.save(path)
        return mem

    @classmethod
    def load(cls, path: Path) -> SearchMemory:
        """Load from disk, or return empty instance if file doesn't exist."""
        mem = cls()
        if not path.exists():
            return mem
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning("Failed to load SearchMemory from %s — starting fresh", path)
            return mem

        mem._watermark = set(data.get("watermark", []))
        for axis, vals in data.get("axis_values", {}).items():
            for v, accs in vals.items():
                mem._axis_values[axis][v] = accs
        for q, hits in data.get("query_hits", {}).items():
            mem._query_hits[q] = hits
        for q, modes in data.get("query_failure_modes", {}).items():
            mem._query_failure_modes[q] = modes
        mem._bottleneck_counts = defaultdict(int, data.get("bottleneck_counts", {}))
        mem._total_failures = data.get("total_failures", 0)
        mem._query_degradation_counts = defaultdict(int, data.get("query_degradation_counts", {}))
        mem._query_axis_sensitivity = data.get("query_axis_sensitivity", {})
        mem._axis_failure_group_deltas = data.get("axis_failure_group_deltas", {})
        mem._query_flips = data.get("query_flips", [])
        return mem

    # --- Internals ---

    def _ingest_run(self, detail: dict[str, Any]) -> None:
        """Process one dataset run detail into rolling stats."""
        accuracy = detail.get("scores", {}).get("accuracy", 0.0)
        pipeline_params = detail.get("pipeline_params") or {}
        items = detail.get("dataset_run_items", [])

        # Extract axis values from pipeline_params (flat keys)
        for node_name, node_config in pipeline_params.items():
            if isinstance(node_config, dict):
                for param, value in node_config.items():
                    axis = f"{node_name}.{param}" if node_name else param
                    preview = _value_preview(value)
                    self._axis_values[axis][preview].append(accuracy)
            else:
                preview = _value_preview(node_config)
                self._axis_values[node_name][preview].append(accuracy)

        # Extract prompt field axis values (from rendered prompt hash group)
        # The prompt text is too long for axis tracking — skip raw prompt
        # Prompt field decomposition tracked via sp_hash identity

        # Per-query stats
        for item in items:
            query = item.get("query", "")
            if not query:
                continue
            hit = bool(item.get("hit"))
            self._query_hits[query].append(hit)

            pd = item.get("pipeline_data") or {}

            # Degradation tracking
            if (pd.get("diagnostics") or {}).get("warnings"):
                self._query_degradation_counts[query] += 1

            if not hit and not is_error_result(item):
                terminated = pd.get("terminated_at", "unknown")
                self._query_failure_modes[query].append(terminated)
                self._bottleneck_counts[terminated] += 1
                self._total_failures += 1

    def _compute_axis_impact(
        self,
        axis: str,
        values: dict[str, list[float]],
    ) -> AxisImpact | None:
        """Compute effect size and consistency for one axis."""
        all_means = []
        total_samples = 0
        for _v, accs in values.items():
            if accs:
                all_means.append(sum(accs) / len(accs))
                total_samples += len(accs)

        if len(all_means) < 2:
            return AxisImpact(
                axis=axis,
                effect_size=0.0,
                consistency=0.0,
                classification="dead",
                sample_count=total_samples,
            )

        # Effect size: mean pairwise |delta| across value means
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

        return AxisImpact(
            axis=axis,
            effect_size=round(effect, 4),
            consistency=round(consistency, 4),
            classification=classification,
            top_values=top_values,
            sample_count=total_samples,
        )

    def _dominant_failure_mode(self, query: str) -> str:
        """Most common terminated_at value for a query, or empty string."""
        modes = self._query_failure_modes.get(query, [])
        if not modes:
            return ""
        return Counter(modes).most_common(1)[0][0]

    def _build_query_records(self) -> list[QueryRecord]:
        """Build QueryRecord list from internal stats."""
        records = []
        for query, hits in sorted(self._query_hits.items()):
            if not hits:
                continue
            hit_rate = sum(hits) / len(hits)
            variance = hit_rate * (1 - hit_rate)
            records.append(
                QueryRecord(
                    query=query,
                    hit_rate=round(hit_rate, 4),
                    n_measurements=len(hits),
                    variance=round(variance, 4),
                    dominant_failure_mode=self._dominant_failure_mode(query),
                )
            )
        return records


def _value_preview(value: Any) -> str:
    """Short string preview of an axis value for grouping."""
    s = str(value)
    return s[:80] if len(s) > 80 else s


def _fmt_axis_rankings(rankings: list[AxisImpact]) -> str:
    """Format axis impacts as ``axis (effect=X.XXX, classification)`` joined by ``; ``."""
    return "; ".join(f"{a.axis} (effect={a.effect_size:.3f}, {a.classification})" for a in rankings)


def _fmt_clusters(clusters: list[FailureCluster], *, with_counts: bool) -> str:
    """Format failure clusters, optionally with query counts."""
    if with_counts:
        return "; ".join(
            f"{c.failure_mode} ({c.fraction:.0%}, {c.query_count} queries)" for c in clusters
        )
    return "; ".join(f"{c.failure_mode} ({c.fraction:.0%})" for c in clusters)


def _fmt_persistent_failures(persistent: list[QueryRecord], *, terse: bool = False) -> str:
    """Split persistent failures into intractable (hit_rate==0) vs chronic (>0)."""
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
