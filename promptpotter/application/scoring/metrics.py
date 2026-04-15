"""Scoring and metric computation for evaluation results.

Pure computation — no I/O, no eval infrastructure dependencies.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from promptpotter.shared.errors import is_error_result

if TYPE_CHECKING:
    from promptpotter.domain.analysis import (
        DifficultyClass,
        FailureAnalysis,
        FailurePattern,
        QueryDifficulty,
    )
    from promptpotter.domain.pipeline_schema import (
        PipelineNode,
        PipelineSchema,
    )
    from promptpotter.domain.scoring import QueryResult


class IntermediateMetric(BaseModel):
    """A metric derived from a pipeline node's type."""

    model_config = {"frozen": True}

    name: str
    node_type: str
    pipeline_data_key: str
    description: str = ""
    default_weight: float = 0.0  # 0 = display-only


NODE_TYPE_METRICS: dict[str, list[IntermediateMetric]] = {
    "candidate_source": [
        IntermediateMetric(
            name="source_recall",
            node_type="candidate_source",
            pipeline_data_key="candidate_ranking",
            description="Fraction of queries where ground truth appears in candidate list",
        ),
    ],
    "ranker": [
        IntermediateMetric(
            name="candidate_recall",
            node_type="ranker",
            pipeline_data_key="final_ranking",
            description="Fraction of LLM-ranked queries where ground truth was available",
        ),
    ],
    "cache": [
        IntermediateMetric(
            name="cache_hit_rate",
            node_type="cache",
            pipeline_data_key="step_timings",
            description="Fraction of queries resolved by cache",
        ),
    ],
    "enricher": [],
}


__all__ = [
    "compile_failure_analysis",
    "compile_query_difficulty",
    "compute_composite_score",
    "compute_pipeline_metrics",
    "count_degraded_queries",
    "count_failures",
    "extract_sample_diagnostics",
    "find_rank",
]


def find_rank(candidates: list, ground_truth: str) -> int | None:
    """Find 1-based rank of ground_truth in a candidates list.

    Works with plain strings, dicts with a ``candidate`` key,
    and list/tuple entries (uses first element).
    """
    if not candidates or not ground_truth:
        return None
    for i, c in enumerate(candidates):
        name = (
            c.get("candidate", c)
            if isinstance(c, dict)
            else (c[0] if isinstance(c, (list, tuple)) else str(c))
        )
        if str(name) == ground_truth:
            return i + 1
    return None


def _compute_accuracy(results: list[QueryResult]) -> dict:
    """Compute accuracy metrics from evaluation results.

    Args:
        results: List of result dicts with ``hit`` and ``error`` keys.

    Returns:
        Dict with keys: hits, total, accuracy, errors.
    """
    total = len(results)
    hits = sum(1 for r in results if r.get("hit"))
    errors = sum(1 for r in results if is_error_result(r))
    # Use mean per-query score (continuous) instead of binary hit rate.
    # For exact-match scoring (score is 0 or 1), this equals hits/total.
    # For rank-weighted scoring, this yields MRR.
    accuracy = sum(r.get("score", 0.0) for r in results) / total if total else 0.0
    return {"hits": hits, "total": total, "accuracy": accuracy, "errors": errors}


def count_failures(results: list[QueryResult]) -> int:
    """Count non-hit results (misses + errors)."""
    return sum(1 for r in results if not r.get("hit"))


def is_degraded(result: Mapping[str, Any]) -> bool:
    """A query is degraded iff its pipeline_data.diagnostics.warnings list is non-empty."""
    return bool((result.get("pipeline_data") or {}).get("diagnostics", {}).get("warnings"))


def count_degraded_queries(results: Sequence[Mapping[str, Any]]) -> int:
    """Count queries that have pipeline degradation warnings."""
    return sum(1 for r in results if is_degraded(r))


def _extract_candidate_label(c) -> str:
    """Extract the display name from a candidate (dict, tuple, or string)."""
    if isinstance(c, dict):
        return str(c.get("candidate", c))
    return c[0] if isinstance(c, (list, tuple)) else str(c)


def _compute_recall(
    step: PipelineNode,
    results: list[QueryResult],
    candidate_key: str = "candidate_ranking",
) -> float:
    """Fraction of queries where GT appears in the candidate list for *step*."""

    def _step_ran(r: QueryResult) -> bool:
        pd = r.get("pipeline_data") or {}
        if pd.get("terminated_at") == step.name:
            return True
        return (pd.get("step_timings") or {}).get(step.name) is not None

    scoped = [r for r in results if _step_ran(r) and not is_error_result(r)]
    if not scoped:
        return 0.0
    found = 0
    for r in scoped:
        pd = r.get("pipeline_data") or {}
        candidates: list[Any] = pd.get(candidate_key, [])  # type: ignore[assignment]
        gt = r.get("ground_truth", "")
        if any(_extract_candidate_label(c) == gt for c in candidates):
            found += 1
    return found / len(scoped)


def _compute_cache_hit_rate(step: PipelineNode, results: list[QueryResult]) -> float:
    """Fraction of queries resolved by cache (non-null cache timing)."""
    if not results:
        return 0.0
    cache_hits = non_error = 0
    for r in results:
        if is_error_result(r):
            continue
        non_error += 1
        pd = r.get("pipeline_data") or {}
        if (pd.get("step_timings") or {}).get(step.name) is not None:
            cache_hits += 1
    return cache_hits / non_error if non_error else 0.0


def _compute_type_metric(
    metric_def: IntermediateMetric,
    step: PipelineNode,
    results: list[QueryResult],
) -> float:
    """Compute a single type-based metric value."""
    if metric_def.name in ("source_recall", "candidate_recall"):
        return _compute_recall(step, results, candidate_key=metric_def.pipeline_data_key)
    if metric_def.name == "cache_hit_rate":
        return _compute_cache_hit_rate(step, results)
    return 0.0


def extract_sample_diagnostics(
    result: Mapping[str, Any],
    pipeline_schema: PipelineSchema,
) -> dict[str, float | bool | int | str | None]:
    """Extract per-query diagnostic signals from a single evaluation result.

    Per-query complement to ``compute_pipeline_metrics()`` — returns a flat
    dict of named diagnostic values derived from the result's ``pipeline_data``
    and the schema's node types.

    Node-type metrics are namespaced as ``{node_name}_{metric}`` when multiple
    nodes share a type, bare ``{metric}`` otherwise (same convention as
    ``compute_pipeline_metrics``).
    """
    pd = result.get("pipeline_data") or {}
    gt = result.get("ground_truth", "")
    diag: dict[str, float | bool | int | str | None] = {}

    # --- Infrastructure diagnostics (always present) ---
    diag["terminated_at"] = pd.get("terminated_at")
    diag["total_time_ms"] = pd.get("total_time")
    diag["degraded"] = bool((pd.get("diagnostics") or {}).get("warnings"))
    diag["error"] = is_error_result(result)

    if not pd:
        return diag

    # --- Node-type diagnostics ---
    type_steps: dict[str, list[PipelineNode]] = {}
    for step in pipeline_schema.nodes:
        if step.node_type and step.node_type in NODE_TYPE_METRICS:
            type_steps.setdefault(step.node_type, []).append(step)

    for _ntype, steps in type_steps.items():
        needs_namespace = len(steps) > 1
        for step in steps:
            prefix = f"{step.name}_" if needs_namespace else ""
            node_diag = _extract_node_diagnostics(step, pd, gt)
            for k, v in node_diag.items():
                diag[f"{prefix}{k}"] = v

    return diag


def _extract_node_diagnostics(
    node: PipelineNode,
    pipeline_data: Mapping[str, Any],
    ground_truth: str,
) -> dict[str, float | bool | int | str | None]:
    """Extract per-query diagnostics for a single node, dispatched on node_type."""
    ntype = node.node_type

    if ntype == "candidate_source":
        return _diag_candidate_source(node, pipeline_data, ground_truth)
    if ntype == "ranker":
        return _diag_ranker(node, pipeline_data, ground_truth)
    if ntype == "enricher":
        return _diag_enricher(node, pipeline_data)
    if ntype == "cache":
        return _diag_cache(node, pipeline_data)
    return {}


def _gt_position(candidates: list, ground_truth: str) -> int | None:
    """Return 0-based position of ground_truth in candidates, or None."""
    for i, c in enumerate(candidates):
        if _extract_candidate_label(c) == ground_truth:
            return i
    return None


def _diag_candidate_source(
    node: PipelineNode,
    pd: Mapping[str, Any],
    gt: str,
) -> dict[str, float | bool | int | str | None]:
    metrics = NODE_TYPE_METRICS.get("candidate_source", [])
    key = metrics[0].pipeline_data_key if metrics else "candidate_ranking"
    candidates = pd.get(key, [])
    pos = _gt_position(candidates, gt)
    return {
        "gt_in_source": pos is not None,
        "n_source_candidates": len(candidates),
        "gt_source_rank": pos,
    }


def _diag_ranker(
    node: PipelineNode,
    pd: Mapping[str, Any],
    gt: str,
) -> dict[str, float | bool | int | str | None]:
    metrics = NODE_TYPE_METRICS.get("ranker", [])
    key = metrics[0].pipeline_data_key if metrics else "final_ranking"
    candidates = pd.get(key, [])
    pos = _gt_position(candidates, gt)

    # top_score_gap: difference between rank-1 and rank-2 scores (confidence signal)
    top_score_gap: float | None = None
    if len(candidates) >= 2:
        scores = []
        for c in candidates[:2]:
            if isinstance(c, dict):
                scores.append(float(c.get("score") or c.get("similarity") or 0.0))
            elif isinstance(c, (list, tuple)) and len(c) >= 2:
                scores.append(float(c[1]))
        if len(scores) == 2:
            top_score_gap = scores[0] - scores[1]

    return {
        "gt_in_ranked": pos is not None,
        "n_final_ranking": len(candidates),
        "gt_rank": pos,
        "top_score_gap": top_score_gap,
    }


def _diag_enricher(
    node: PipelineNode,
    pd: Mapping[str, Any],
) -> dict[str, float | bool | int | str | None]:
    # Count enriched fields from observation mappings if available
    n = 0
    for mapping in node.observation_mappings:
        if pd.get(mapping.pipeline_key) is not None:
            n += 1
    return {"n_enriched_fields": n}


def _diag_cache(
    node: PipelineNode,
    pd: Mapping[str, Any],
) -> dict[str, float | bool | int | str | None]:
    timings = pd.get("step_timings") or {}
    return {"cache_hit": timings.get(node.name) is not None}


# Composite-score default weights. Sum = 1.0. Accuracy stays dominant;
# the rest is split across health/cost/recall bundles so single-node
# pipelines (e.g. BBEH llm_only) still get a meaningful composite beyond
# raw accuracy, and multi-node pipelines (e.g. TermNorm) pick up the
# recall signals on top.
COMPOSITE_WEIGHT_ACCURACY = 0.7
COMPOSITE_WEIGHT_HEALTH = 0.15  # errors + degradation + runtime failures
COMPOSITE_WEIGHT_COST = 0.10  # latency + (future) token cost
COMPOSITE_WEIGHT_RECALL = 0.05  # node-type recall metrics (enriches multi-node)

# Latency budget for the cost bundle. Results with mean latency ≥ this
# contribute 0 to the cost bundle; ≤ 0.2× contribute 1.0. Prototype value.
COMPOSITE_LATENCY_BUDGET_MS = 10_000.0


def _compute_health_bundle(
    results: list[QueryResult],
    *,
    runtime_failure_count: int,
) -> dict[str, float]:
    """JobSP + OptSP health signals rolled into a single [0, 1] score.

    - error_rate, degraded_rate: derived from results.
    - runtime_failure_rate: count on opt_sp.memory divided by total queries
      (bounded to [0, 1]).

    Each sub-signal contributes as (1 - rate); the bundle is the mean so
    one catastrophic dimension doesn't wipe the other two.
    """
    total = len(results) or 1
    errors = sum(1 for r in results if is_error_result(r))
    degraded = count_degraded_queries(results)
    error_rate = errors / total
    degraded_rate = degraded / total
    rf_rate = min(runtime_failure_count / total, 1.0)
    components = [
        1.0 - error_rate,
        1.0 - degraded_rate,
        1.0 - rf_rate,
    ]
    return {
        "health": sum(components) / len(components),
        "error_rate": error_rate,
        "degraded_rate": degraded_rate,
        "runtime_failure_rate": rf_rate,
    }


def _compute_cost_bundle(results: list[QueryResult]) -> dict[str, float]:
    """Cost / latency signals rolled into a single [0, 1] score.

    Only latency for now. Token cost is a natural future extension —
    slot it into this bundle when the backend starts reporting it.
    """
    latencies = []
    for r in results:
        pd = r.get("pipeline_data") or {}
        t = pd.get("total_time")
        if t is not None:
            try:
                latencies.append(float(t))
            except (TypeError, ValueError):
                continue
    if not latencies:
        return {"cost": 1.0, "mean_latency_ms": 0.0}
    mean_latency = sum(latencies) / len(latencies)
    latency_score = max(0.0, 1.0 - mean_latency / COMPOSITE_LATENCY_BUDGET_MS)
    return {"cost": latency_score, "mean_latency_ms": mean_latency}


def compute_pipeline_metrics(
    pipeline_schema: PipelineSchema,
    results: list[QueryResult],
    *,
    metric_weights: dict[str, float] | None = None,
    opt_sp: Any = None,
) -> dict[str, float]:
    """Compute intermediate metrics from pipeline node types.

    Walks ``pipeline_schema.nodes``; for each with a ``node_type`` in the
    registry, computes the corresponding metric scoped to queries where
    the step ran.

    The composite is **recorded, not gating**: ``_select_round_winner``
    compares candidates on ``accuracy`` (the user's performance scoring
    function). Composite is displayed and persisted so operators can see
    whether a win came with hidden costs (latency, error bursts,
    runtime failures), but it does not drive the optimization loop.

    Signals folded into composite:
      - accuracy (JobSP, dominant)
      - health bundle: errors + degradation + OptSP runtime_failures
      - cost bundle: mean latency (future: token cost)
      - recall bundle: node-type recall metrics when the pipeline has them

    ``opt_sp`` is an optional ``OptSearchPoint``; when provided, its
    ``memory.validation_failures`` forces composite to 0.0 (a structurally
    invalid candidate has no meaningful composite) and
    ``memory.runtime_failures`` feeds the health bundle.
    """
    base = _compute_accuracy(results)
    accuracy = base["accuracy"]
    weights = dict(metric_weights or {})
    metric_values: dict[str, float] = {}

    # Node-type metrics (display + recall bundle)
    type_steps: dict[str, list] = {}
    for step in pipeline_schema.nodes:
        if step.node_type and step.node_type in NODE_TYPE_METRICS:
            type_steps.setdefault(step.node_type, []).append(step)

    for _ntype, steps in type_steps.items():
        metrics = NODE_TYPE_METRICS.get(_ntype, [])
        needs_namespace = len(steps) > 1
        for step in steps:
            for metric_def in metrics:
                metric_name = (
                    f"{step.name}_{metric_def.name}" if needs_namespace else metric_def.name
                )
                metric_values[metric_name] = _compute_type_metric(
                    metric_def,
                    step,
                    results,
                )

    # OptSP signals
    runtime_failure_count = 0
    validation_failure_count = 0
    if opt_sp is not None and hasattr(opt_sp, "memory"):
        runtime_failure_count = len(getattr(opt_sp.memory, "runtime_failures", []) or [])
        validation_failure_count = len(getattr(opt_sp.memory, "validation_failures", []) or [])

    health = _compute_health_bundle(results, runtime_failure_count=runtime_failure_count)
    cost = _compute_cost_bundle(results)

    # Recall bundle: mean of available node-type metrics, or fall back to
    # accuracy so single-node pipelines don't silently discount the weight.
    recall_score = sum(metric_values.values()) / len(metric_values) if metric_values else accuracy

    # Weighted composite — weights can be overridden per-metric via
    # metric_weights for tuning experiments.
    w_acc = weights.get("accuracy", COMPOSITE_WEIGHT_ACCURACY)
    w_health = weights.get("health", COMPOSITE_WEIGHT_HEALTH)
    w_cost = weights.get("cost", COMPOSITE_WEIGHT_COST)
    w_recall = weights.get("recall", COMPOSITE_WEIGHT_RECALL)
    composite = (
        w_acc * accuracy
        + w_health * health["health"]
        + w_cost * cost["cost"]
        + w_recall * recall_score
    )

    # Hard zero for structurally invalid candidates — keeps the composite
    # honest even though the synthetic-0 path already sets accuracy=0.
    if validation_failure_count > 0:
        composite = 0.0

    degraded = count_degraded_queries(results)

    return {
        **base,
        **metric_values,
        "composite": round(composite, 6),
        "degraded_queries": degraded,
        # Display-only breakdown so operators can see what moved the composite.
        "health_bundle": round(health["health"], 6),
        "error_rate": round(health["error_rate"], 6),
        "degraded_rate": round(health["degraded_rate"], 6),
        "runtime_failure_rate": round(health["runtime_failure_rate"], 6),
        "cost_bundle": round(cost["cost"], 6),
        "mean_latency_ms": round(cost["mean_latency_ms"], 2),
        "recall_bundle": round(recall_score, 6),
        "validation_failure_count": validation_failure_count,
        "runtime_failure_count": runtime_failure_count,
    }


def compute_composite_score(
    results: list[QueryResult],
    pipeline_schema: PipelineSchema,
    *,
    opt_sp: Any = None,
    metric_weights: dict[str, float] | None = None,
) -> dict:
    """Compute composite score — delegates to ``compute_pipeline_metrics()``.

    The composite is a **recorded** multi-signal diagnostic, not the
    winner-selection key. ``_select_round_winner`` compares candidates on
    ``accuracy`` (the user's performance scoring function). See
    ``compute_pipeline_metrics`` for the signal breakdown.

    Returns dict with at least: hits, total, accuracy, errors, composite,
    plus the health/cost/recall bundle breakdown and any type-derived
    metrics from the schema's node types.
    """
    return compute_pipeline_metrics(
        pipeline_schema,
        results,
        opt_sp=opt_sp,
        metric_weights=metric_weights,
    )


def _classify_difficulty(hit_rate: float) -> DifficultyClass:
    if hit_rate >= 1.0 or hit_rate <= 0.0:
        return "dead"
    if hit_rate > 0.9:
        return "easy"
    if hit_rate >= 0.1:
        return "discriminating"
    return "hard"


def _failure_pattern_key(diag: dict) -> tuple[str, ...]:
    """Build a grouping key from the most discriminative diagnostic signals."""
    parts: list[str] = []
    for field in ("gt_in_source", "gt_in_ranked", "terminated_at"):
        if field in diag:
            parts.append(f"{field}={diag[field]}")
    return tuple(parts) if parts else ("unknown",)


def _auto_name(key: tuple[str, ...]) -> str:
    """Generate a human-readable pattern name from a diagnostic key."""
    if key == ("unknown",):
        return "unknown"
    names: list[str] = []
    for part in key:
        field, _, val = part.partition("=")
        if field == "gt_in_source" and val == "False":
            names.append("source_miss")
        elif field == "gt_in_ranked" and val == "False":
            names.append("rank_miss")
        elif field == "terminated_at":
            names.append(f"stopped_at_{val}")
    return "_".join(names) if names else "pattern"


def compile_failure_analysis(
    results: list[QueryResult],
    pipeline_schema: PipelineSchema,
    *,
    max_patterns: int = 5,
    max_examples: int = 3,
) -> FailureAnalysis:
    """Group query failures by diagnostic pattern.

    Calls ``extract_sample_diagnostics()`` on each failing result, groups by
    ``(gt_in_source, gt_in_ranked, terminated_at)`` key, and returns the top
    patterns ranked by failure count.
    """
    from promptpotter.domain.analysis import FailureAnalysis, FailurePattern

    failures = [r for r in results if not r.get("hit") and not is_error_result(r)]
    if not failures:
        return FailureAnalysis(total_failures=0, total_results=len(results))

    # Group by diagnostic key
    groups: dict[tuple[str, ...], list[QueryResult]] = defaultdict(list)
    diag_cache: dict[tuple[str, ...], dict] = {}
    for r in failures:
        diag = extract_sample_diagnostics(r, pipeline_schema)
        key = _failure_pattern_key(diag)
        groups[key].append(r)
        if key not in diag_cache:
            diag_cache[key] = diag

    total_failures = len(failures)
    patterns: list[FailurePattern] = []
    for key, group in sorted(groups.items(), key=lambda x: -len(x[1])):
        patterns.append(
            FailurePattern(
                name=_auto_name(key),
                query_count=len(group),
                fraction=len(group) / total_failures,
                diagnostic_key=key,
                example_queries=[r.get("query", "") for r in group[:max_examples]],
                signals=diag_cache.get(key, {}),
            )
        )
        if len(patterns) >= max_patterns:
            break

    return FailureAnalysis(
        patterns=patterns,
        total_failures=total_failures,
        total_results=len(results),
    )


def compile_query_difficulty(
    historical_results: Sequence[Sequence[Mapping[str, Any]]],
) -> QueryDifficulty:
    """Classify queries by hit rate across multiple evaluation rounds.

    Args:
        historical_results: List of result sets (one per evaluation/round).

    Returns:
        QueryDifficulty with per-query classification.
    """
    from promptpotter.domain.analysis import QueryDifficulty, QueryProfile

    query_hits: dict[str, list[bool]] = defaultdict(list)
    for round_results in historical_results:
        for r in round_results:
            q = r.get("query", "")
            if q:
                query_hits[q].append(bool(r.get("hit")))

    profiles = []
    for query, hits in sorted(query_hits.items()):
        hit_rate = sum(hits) / len(hits) if hits else 0.0
        profiles.append(
            QueryProfile(
                query=query,
                hit_rate=hit_rate,
                n_measurements=len(hits),
                classification=_classify_difficulty(hit_rate),
            )
        )

    return QueryDifficulty(profiles=profiles)
