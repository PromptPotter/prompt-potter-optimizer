"""Pure compute functions backing the evaluator registry.

Every ``_compute_*`` function is pure (keyword-only, no I/O, no
mutation) and returns a float. They're referenced by name from
``evaluators._REGISTRY``.

Adding a new evaluator = write a ``_compute_*`` here, then register it in
``evaluators.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from promptpotter.shared.errors import is_error_result

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineNode, PipelineSchema
    from promptpotter.domain.scoring import QueryResult


# Latency budget for the ``latency_norm`` evaluator. Mean latency ≥ this
# contributes 0; ≤ 0 contributes 1.0. Kept here so the composite math that
# used to live in ``metrics.py`` has one home.
LATENCY_BUDGET_MS = 10_000.0


__all__ = [
    "LATENCY_BUDGET_MS",
    "compute_accuracy",
    "compute_cache_hit_rate",
    "compute_candidate_recall",
    "compute_degraded_rate",
    "compute_error_rate",
    "compute_latency_norm",
    "compute_mean_retrieval_shortfall",
    "compute_pipeline_compactness",
    "compute_retrieval_shortfall_per_query",
    "compute_runtime_failure_rate",
    "compute_source_recall",
    "has_limit_node",
]


# ---------------------------------------------------------------------------
# Core per-round computes.
# ---------------------------------------------------------------------------


def compute_accuracy(*, results: list[QueryResult], **_: Any) -> float:
    if not results:
        return 0.0
    return sum(r.get("score", 0.0) for r in results) / len(results)


def compute_error_rate(*, results: list[QueryResult], **_: Any) -> float:
    if not results:
        return 0.0
    return sum(1 for r in results if is_error_result(r)) / len(results)


def _is_degraded(result: Any) -> bool:
    return bool((result.get("pipeline_data") or {}).get("diagnostics", {}).get("warnings"))


def compute_degraded_rate(*, results: list[QueryResult], **_: Any) -> float:
    if not results:
        return 0.0
    return sum(1 for r in results if _is_degraded(r)) / len(results)


def compute_runtime_failure_rate(
    *, results: list[QueryResult], opt_sp: Any = None, **_: Any
) -> float:
    if not results or opt_sp is None or not hasattr(opt_sp, "memory"):
        return 0.0
    count = len(getattr(opt_sp.memory, "runtime_failures", []) or [])
    return min(count / len(results), 1.0)


def compute_latency_norm(*, results: list[QueryResult], **_: Any) -> float:
    latencies: list[float] = []
    for r in results:
        pd = r.get("pipeline_data") or {}
        t = pd.get("total_time")
        if t is None:
            continue
        try:
            latencies.append(float(t))
        except (TypeError, ValueError):
            continue
    if not latencies:
        return 1.0
    mean_ms = sum(latencies) / len(latencies)
    return max(0.0, 1.0 - mean_ms / LATENCY_BUDGET_MS)


# ---------------------------------------------------------------------------
# Node-type-bound computes.
# ---------------------------------------------------------------------------


def _extract_candidate_label(c: Any) -> str:
    if isinstance(c, dict):
        return str(c.get("candidate", c))
    return c[0] if isinstance(c, (list, tuple)) else str(c)


def _compute_recall(
    *,
    results: list[QueryResult],
    node: PipelineNode,
    candidate_key: str,
    **_: Any,
) -> float:
    """Fraction of non-error queries (for which *node* ran) where GT is in the
    node's output candidate list. Shared between source_recall and
    candidate_recall."""

    def _step_ran(r: QueryResult) -> bool:
        pd = r.get("pipeline_data") or {}
        if pd.get("terminated_at") == node.name:
            return True
        return (pd.get("step_timings") or {}).get(node.name) is not None

    scoped = [r for r in results if _step_ran(r) and not is_error_result(r)]
    if not scoped:
        return 0.0
    found = 0
    for r in scoped:
        pd = r.get("pipeline_data") or {}
        raw = pd.get(candidate_key)
        candidates: list = list(raw) if isinstance(raw, list) else []
        gt = r.get("ground_truth", "")
        if any(_extract_candidate_label(c) == gt for c in candidates):
            found += 1
    return found / len(scoped)


def compute_source_recall(**kwargs: Any) -> float:
    return _compute_recall(candidate_key="candidate_ranking", **kwargs)


def compute_candidate_recall(**kwargs: Any) -> float:
    return _compute_recall(candidate_key="final_ranking", **kwargs)


def compute_cache_hit_rate(*, results: list[QueryResult], node: PipelineNode, **_: Any) -> float:
    if not results:
        return 0.0
    cache_hits = non_error = 0
    for r in results:
        if is_error_result(r):
            continue
        non_error += 1
        pd = r.get("pipeline_data") or {}
        if (pd.get("step_timings") or {}).get(node.name) is not None:
            cache_hits += 1
    return cache_hits / non_error if non_error else 0.0


# ---------------------------------------------------------------------------
# Retrieval shortfall + pipeline compactness.
# ---------------------------------------------------------------------------


_LIMIT_KEY_SUFFIXES = ("max_sites", "num_results", "max_token_candidates", "max_tokens")


def _limit_nodes(schema: PipelineSchema) -> list[tuple[PipelineNode, str, int]]:
    """Return ``(node, limit_key, target)`` for each node whose current_config
    carries a numeric ``max_*``/``num_*`` key — the target size of its output list.
    """
    out: list[tuple[PipelineNode, str, int]] = []
    for node in schema.nodes:
        cfg = node.current_config or {}
        for key in cfg:
            if not any(key == s or key.endswith(s) for s in _LIMIT_KEY_SUFFIXES):
                continue
            target = cfg.get(key)
            if not isinstance(target, int) or target <= 0:
                continue
            out.append((node, key, target))
            break
    return out


def has_limit_node(schema: PipelineSchema) -> bool:
    return bool(_limit_nodes(schema))


def _retrieval_shortfall_for_result(result: QueryResult, schema: PipelineSchema) -> float | None:
    """Per-query min(observed / target, 1.0) across all limit-bearing nodes.

    Returns None when no limit-bearing node has a list-valued output on this
    result; lets the per-round aggregator skip queries with nothing to measure.
    """
    pd = result.get("pipeline_data") or {}
    ratios: list[float] = []
    for node, _key, target in _limit_nodes(schema):
        for mapping in node.observation_mappings:
            val = pd.get(mapping.pipeline_key)
            if isinstance(val, list):
                ratios.append(min(len(val) / target, 1.0))
                break
    if not ratios:
        return None
    return sum(ratios) / len(ratios)


def compute_retrieval_shortfall_per_query(
    *, result: QueryResult, schema: PipelineSchema | None = None, **_: Any
) -> float:
    if schema is None:
        return 1.0
    v = _retrieval_shortfall_for_result(result, schema)
    return 1.0 if v is None else v


def compute_mean_retrieval_shortfall(
    *, results: list[QueryResult], schema: PipelineSchema, **_: Any
) -> float:
    values: list[float] = []
    for r in results:
        v = _retrieval_shortfall_for_result(r, schema)
        if v is not None:
            values.append(v)
    if not values:
        return 1.0
    return sum(values) / len(values)


def compute_pipeline_compactness(*, schema: PipelineSchema, **_: Any) -> float:
    """Return ``1 - (active_steps - 1) / (max(active_steps, 1) - 1)``.

    Smaller pipelines score higher (more compact). For a single-node pipeline
    compactness is 1.0. The cycle-wide baseline used to live in a comment in
    the plan; in practice we anchor to the schema itself (``len(active_steps)``
    vs. 1), which keeps the signal meaningful across datasets with very
    different pipeline depth.
    """
    n = len(schema.active_steps)
    if n <= 1:
        return 1.0
    # Anchor at 12 node pipelines as the worst case — tuned to produce a
    # graceful slope for real pipelines (2-6 nodes). Cycle-wide calibration
    # can replace this anchor when a campaign accumulates multi-pipeline data.
    worst = 12
    return max(0.0, 1.0 - (n - 1) / (worst - 1))
