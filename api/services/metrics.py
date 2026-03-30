"""Scoring and metric computation for evaluation results.

Pure computation — no I/O, no eval infrastructure dependencies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
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
