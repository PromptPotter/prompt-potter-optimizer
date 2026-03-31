"""Scoring and metric computation for evaluation results.

Pure computation — no I/O, no eval infrastructure dependencies.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from api.models.analysis import (
        DifficultyClass,
        FailureAnalysis,
        FailurePattern,
        QueryDifficulty,
        TrendAnalysis,
    )
    from api.models.pipeline_schema import (
        IntermediateMetric,
        PipelineNode,
        PipelineSchema,
    )


def compute_accuracy(results: list) -> dict:
    """Compute accuracy metrics from evaluation results.

    Args:
        results: List of result dicts with ``hit`` and ``error`` keys.

    Returns:
        Dict with keys: hits, total, accuracy, errors.
    """
    total = len(results)
    hits = sum(1 for r in results if r.get("hit"))
    errors = sum(1 for r in results if r.get("error"))
    accuracy = hits / total if total else 0.0
    return {"hits": hits, "total": total, "accuracy": accuracy, "errors": errors}


def count_degraded_queries(results: list[dict]) -> int:
    """Count queries that have pipeline degradation warnings."""
    return sum(
        1 for r in results if (r.get("pipeline_data") or {}).get("diagnostics", {}).get("warnings")
    )


def _step_executed(step_name: str, result: dict) -> bool:
    """Check if a pipeline step executed for a result (via step_timings or terminated_at)."""
    pd = result.get("pipeline_data") or {}
    if pd.get("terminated_at") == step_name:
        return True
    timings = pd.get("step_timings") or {}
    return timings.get(step_name) is not None


def _extract_candidate_label(c) -> str:
    """Extract the display name from a candidate (dict, tuple, or string)."""
    if isinstance(c, dict):
        return str(c.get("candidate", c))
    return c[0] if isinstance(c, (list, tuple)) else str(c)


def _compute_recall(
    step: PipelineNode,
    results: list[dict],
    candidate_key: str = "token_matched_candidates",
) -> float:
    """Fraction of queries where GT appears in the candidate list for *step*."""
    scoped = [r for r in results if _step_executed(step.name, r) and not r.get("error")]
    if not scoped:
        return 0.0
    found = 0
    for r in scoped:
        pd = r.get("pipeline_data") or {}
        candidates = pd.get(candidate_key, [])
        gt = r.get("ground_truth", "")
        if any(_extract_candidate_label(c) == gt for c in candidates):
            found += 1
    return found / len(scoped)


def _compute_cache_hit_rate(step: PipelineNode, results: list[dict]) -> float:
    """Fraction of queries resolved by cache (non-null cache timing)."""
    if not results:
        return 0.0
    cache_hits = 0
    for r in results:
        if r.get("error"):
            continue
        pd = r.get("pipeline_data") or {}
        timings = pd.get("step_timings") or {}
        if timings.get(step.name) is not None:
            cache_hits += 1
    non_error = sum(1 for r in results if not r.get("error"))
    return cache_hits / non_error if non_error else 0.0


def _compute_type_metric(
    metric_def: IntermediateMetric,
    step: PipelineNode,
    results: list[dict],
) -> float:
    """Compute a single type-based metric value."""
    if metric_def.name in ("source_recall", "candidate_recall"):
        return _compute_recall(step, results, candidate_key=metric_def.pipeline_data_key)
    if metric_def.name == "cache_hit_rate":
        return _compute_cache_hit_rate(step, results)
    return 0.0


def extract_sample_diagnostics(
    result: dict,
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
    from api.models.pipeline_schema import NODE_TYPE_METRICS

    pd = result.get("pipeline_data") or {}
    gt = result.get("ground_truth", "")
    diag: dict[str, float | bool | int | str | None] = {}

    # --- Infrastructure diagnostics (always present) ---
    diag["terminated_at"] = pd.get("terminated_at")
    diag["total_time_ms"] = pd.get("total_time")
    diag["degraded"] = bool((pd.get("diagnostics") or {}).get("warnings"))
    diag["error"] = bool(result.get("error"))

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
    pipeline_data: dict,
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
    node: PipelineNode, pd: dict, gt: str,
) -> dict[str, float | bool | int | str | None]:
    from api.models.pipeline_schema import NODE_TYPE_METRICS

    metrics = NODE_TYPE_METRICS.get("candidate_source", [])
    key = metrics[0].pipeline_data_key if metrics else "token_matched_candidates"
    candidates = pd.get(key, [])
    pos = _gt_position(candidates, gt)
    return {
        "gt_in_source": pos is not None,
        "n_source_candidates": len(candidates),
        "gt_source_rank": pos,
    }


def _diag_ranker(
    node: PipelineNode, pd: dict, gt: str,
) -> dict[str, float | bool | int | str | None]:
    from api.models.pipeline_schema import NODE_TYPE_METRICS

    metrics = NODE_TYPE_METRICS.get("ranker", [])
    key = metrics[0].pipeline_data_key if metrics else "ranked_candidates"
    candidates = pd.get(key, [])
    pos = _gt_position(candidates, gt)

    # top_score_gap: difference between rank-1 and rank-2 scores (confidence signal)
    top_score_gap: float | None = None
    if len(candidates) >= 2:
        scores = []
        for c in candidates[:2]:
            if isinstance(c, dict):
                scores.append(float(c.get("score", c.get("similarity", 0.0))))
            elif isinstance(c, (list, tuple)) and len(c) >= 2:
                scores.append(float(c[1]))
        if len(scores) == 2:
            top_score_gap = scores[0] - scores[1]

    return {
        "gt_in_ranked": pos is not None,
        "n_ranked_candidates": len(candidates),
        "gt_rank": pos,
        "top_score_gap": top_score_gap,
    }


def _diag_enricher(
    node: PipelineNode, pd: dict,
) -> dict[str, float | bool | int | str | None]:
    # Count enriched fields from observation mappings if available
    n = 0
    for mapping in node.observation_mappings:
        if pd.get(mapping.pipeline_key) is not None:
            n += 1
    return {"n_enriched_fields": n}


def _diag_cache(
    node: PipelineNode, pd: dict,
) -> dict[str, float | bool | int | str | None]:
    timings = pd.get("step_timings") or {}
    return {"cache_hit": timings.get(node.name) is not None}


def compute_pipeline_metrics(
    pipeline_schema: PipelineSchema,
    results: list[dict],
    *,
    metric_weights: dict[str, float] | None = None,
    accuracy_weight: float = 0.9,
) -> dict[str, float]:
    """Compute intermediate metrics from pipeline node types.

    Walks ``pipeline_schema.nodes``; for each with a ``node_type`` in the
    registry, computes the corresponding metric scoped to queries where
    the step ran.

    Returns dict with per-metric values and a weighted ``composite`` score.
    """
    from api.models.pipeline_schema import NODE_TYPE_METRICS

    base = compute_accuracy(results)
    accuracy = base["accuracy"]
    weights = dict(metric_weights or {})
    metric_values: dict[str, float] = {}

    # Collect steps by type (namespace when >1 step shares a type)
    type_steps: dict[str, list] = {}
    for step in pipeline_schema.nodes:
        if step.node_type and step.node_type in NODE_TYPE_METRICS:
            type_steps.setdefault(step.node_type, []).append(step)

    for ntype, steps in type_steps.items():
        metrics = NODE_TYPE_METRICS.get(ntype, [])
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

    # Composite: accuracy_weight * accuracy + distributed remaining weight
    remaining_weight = 1.0 - accuracy_weight
    weighted_sum = accuracy_weight * accuracy
    if metric_values:
        n_metrics = len(metric_values)
        for m_name, m_val in metric_values.items():
            w = weights.get(m_name, remaining_weight / n_metrics)
            weighted_sum += w * m_val

    degraded = count_degraded_queries(results)

    return {
        **base,
        **metric_values,
        "composite": round(weighted_sum, 6),
        "degraded_queries": degraded,
    }


def compute_composite_score(
    results: list,
    pipeline_schema: PipelineSchema,
    *,
    accuracy_weight: float = 0.9,
) -> dict:
    """Compute composite score — delegates to ``compute_pipeline_metrics()``.

    Returns dict with at least: hits, total, accuracy, errors, composite,
    and type-derived metrics from the schema's node types.
    """
    return compute_pipeline_metrics(
        pipeline_schema,
        results,
        accuracy_weight=accuracy_weight,
    )


# ---------------------------------------------------------------------------
# Wave 1b — Failure analysis compilation
# ---------------------------------------------------------------------------


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
    results: list[dict],
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
    from api.models.analysis import FailureAnalysis, FailurePattern

    failures = [r for r in results if not r.get("hit") and not r.get("error")]
    if not failures:
        return FailureAnalysis(total_failures=0, total_results=len(results))

    # Group by diagnostic key
    groups: dict[tuple[str, ...], list[dict]] = defaultdict(list)
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
        patterns.append(FailurePattern(
            name=_auto_name(key),
            query_count=len(group),
            fraction=len(group) / total_failures,
            diagnostic_key=key,
            example_queries=[r.get("query", "") for r in group[:max_examples]],
            signals=diag_cache.get(key, {}),
        ))
        if len(patterns) >= max_patterns:
            break

    return FailureAnalysis(
        patterns=patterns,
        total_failures=total_failures,
        total_results=len(results),
    )


def compile_query_difficulty(
    historical_results: list[list[dict]],
) -> QueryDifficulty:
    """Classify queries by hit rate across multiple evaluation rounds.

    Args:
        historical_results: List of result sets (one per evaluation/round).

    Returns:
        QueryDifficulty with per-query classification.
    """
    from api.models.analysis import QueryDifficulty, QueryProfile

    query_hits: dict[str, list[bool]] = defaultdict(list)
    for round_results in historical_results:
        for r in round_results:
            q = r.get("query", "")
            if q:
                query_hits[q].append(bool(r.get("hit")))

    profiles = []
    for query, hits in sorted(query_hits.items()):
        hit_rate = sum(hits) / len(hits) if hits else 0.0
        profiles.append(QueryProfile(
            query=query,
            hit_rate=hit_rate,
            n_evaluations=len(hits),
            classification=_classify_difficulty(hit_rate),
        ))

    return QueryDifficulty(profiles=profiles)


def compile_temporal_trends(
    round_results: list[tuple[int, list[dict]]],
) -> TrendAnalysis:
    """Track query-level changes across consecutive rounds.

    Args:
        round_results: List of ``(round_number, results)`` tuples, ordered
            by round number.

    Returns:
        TrendAnalysis with per-pair snapshots showing improved/regressed queries.
    """
    from api.models.analysis import RoundSnapshot, TrendAnalysis

    if len(round_results) < 2:
        return TrendAnalysis()

    snapshots = []
    for i in range(len(round_results) - 1):
        from_round, from_results = round_results[i]
        to_round, to_results = round_results[i + 1]

        from_hits = {r.get("query", ""): bool(r.get("hit")) for r in from_results}
        to_hits = {r.get("query", ""): bool(r.get("hit")) for r in to_results}

        common = set(from_hits) & set(to_hits)
        improved = [q for q in sorted(common) if not from_hits[q] and to_hits[q]]
        regressed = [q for q in sorted(common) if from_hits[q] and not to_hits[q]]
        unchanged = len(common) - len(improved) - len(regressed)

        snapshots.append(RoundSnapshot(
            from_round=from_round,
            to_round=to_round,
            improved_queries=improved,
            regressed_queries=regressed,
            unchanged_queries=unchanged,
        ))

    return TrendAnalysis(snapshots=snapshots)
