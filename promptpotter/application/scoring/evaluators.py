"""Evaluator registry, materializers, and the pure compute functions they bind.

The registry (``_REGISTRY``) is the single source of truth for which round-
and query-level evaluators exist; each entry's ``compute`` callable is defined
in this same module just below. Per-round evaluators receive the round's
result list (and optionally an ``OptSearchPoint`` for memory-derived signals);
per-query evaluators receive a single result. Every compute fn returns a
float in [0, 1].
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from promptpotter.domain.scoring import extract_candidate_label
from promptpotter.shared.errors import is_degraded, is_error_result

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineNode, PipelineSchema
    from promptpotter.domain.scoring import QueryResult


Scope = Literal["per_query", "per_round"]
DataType = Literal["NUMERIC", "BOOLEAN"]


# Latency budget for ``latency_norm``: ≥ budget → 0.0, ≤ 0 → 1.0.
LATENCY_BUDGET_MS = 10_000.0

# Prompt-length budget for ``prompt_compactness``: ≥ budget → 0.0, ≤ 0 → 1.0.
# 4 000 chars ≈ 1 000 tokens — a comfortable ceiling for a well-decomposed
# 8-field prompt. Operators who want a different ceiling override the
# weight or the formula in ``campaign.json::scoring``.
PROMPT_BUDGET_CHARS = 4_000


__all__ = [
    "LATENCY_BUDGET_MS",
    "PROMPT_BUDGET_CHARS",
    "Evaluator",
    "all_evaluators",
    "default_per_round_formula",
    "default_per_round_formula_short",
    "materialize_query_values",
    "materialize_round_values",
]


# ---------------------------------------------------------------------------
# Compute functions — keyword-only, referenced by ``_REGISTRY`` below.
# ---------------------------------------------------------------------------


def compute_accuracy(*, results: list[QueryResult], **_: Any) -> float:
    if not results:
        return 0.0
    return sum(r.get("score", 0.0) for r in results) / len(results)


def compute_error_rate(*, results: list[QueryResult], **_: Any) -> float:
    if not results:
        return 0.0
    return sum(1 for r in results if is_error_result(r)) / len(results)


def compute_degraded_rate(*, results: list[QueryResult], **_: Any) -> float:
    if not results:
        return 0.0
    return sum(1 for r in results if is_degraded(r)) / len(results)


def compute_runtime_failure_rate(
    *, results: list[QueryResult], opt_sp: Any = None, **_: Any
) -> float:
    if not results or opt_sp is None:
        return 0.0
    count = len(getattr(opt_sp, "runtime_failures", []) or [])
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
        if any(extract_candidate_label(c) == gt for c in candidates):
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
    """Smaller pipelines score higher; single-node = 1.0, worst-case anchored at 12 nodes."""
    n = len(schema.active_steps)
    if n <= 1:
        return 1.0
    worst = 12
    return max(0.0, 1.0 - (n - 1) / (worst - 1))


def compute_prompt_compactness(*, opt_sp: Any = None, **_: Any) -> float:
    """Shorter rendered prompts score higher; ``len(opt_sp.render()) >= budget`` → 0.0.

    Reads the candidate's full rendered prompt (the same string that goes
    onto ``pipeline_params[prompt_node]["prompt"]``). Returns 1.0 when the
    candidate is missing or the prompt is empty so this evaluator never
    masks a real signal — operators see "1.0 (vacuous)" rather than a
    spurious penalty.

    The 4 000-char budget is a soft ceiling; the curve is linear so the
    composite term degrades gracefully rather than cliff-edging at the
    threshold. To mark prompts above a hard threshold, build a per-round
    formula like ``... + 0.10 * (1 if prompt_compactness > 0.5 else 0)``.
    """
    if opt_sp is None or not hasattr(opt_sp, "render"):
        return 1.0
    rendered = opt_sp.render() or ""
    if not rendered:
        return 1.0
    return max(0.0, 1.0 - len(rendered) / PROMPT_BUDGET_CHARS)


# ---------------------------------------------------------------------------
# Registry + materializers.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Evaluator:
    name: str
    description: str
    scope: Scope
    compute: Callable[..., float]
    data_type: DataType = "NUMERIC"
    node_type: str | None = None
    applies: Callable[[PipelineSchema], bool] = field(default=lambda _schema: True)


_REGISTRY: list[Evaluator] = [
    Evaluator(
        name="accuracy",
        description="Mean per-query score across the round's result set.",
        scope="per_round",
        compute=compute_accuracy,
    ),
    Evaluator(
        name="error_rate",
        description="Fraction of queries that errored (ERROR predicted or exception).",
        scope="per_round",
        compute=compute_error_rate,
    ),
    Evaluator(
        name="degraded_rate",
        description="Fraction of queries that completed with pipeline degradation warnings.",
        scope="per_round",
        compute=compute_degraded_rate,
    ),
    Evaluator(
        name="runtime_failure_rate",
        description="Runtime failure count on OptSP memory, normalized by total queries.",
        scope="per_round",
        compute=compute_runtime_failure_rate,
    ),
    Evaluator(
        name="latency_norm",
        description=(
            "Mean latency normalized against LATENCY_BUDGET_MS (1.0 = instant, 0.0 = ≥ budget)."
        ),
        scope="per_round",
        compute=compute_latency_norm,
    ),
    Evaluator(
        name="source_recall",
        description="Fraction of queries where GT appears in a candidate_source node's output.",
        scope="per_round",
        compute=compute_source_recall,
        node_type="candidate_source",
        applies=lambda s: any(n.node_type == "candidate_source" for n in s.nodes),
    ),
    Evaluator(
        name="candidate_recall",
        description="Fraction of queries where GT appears in a ranker node's final_ranking.",
        scope="per_round",
        compute=compute_candidate_recall,
        node_type="ranker",
        applies=lambda s: any(n.node_type == "ranker" for n in s.nodes),
    ),
    Evaluator(
        name="cache_hit_rate",
        description="Fraction of queries resolved by a cache node (non-null timing).",
        scope="per_round",
        compute=compute_cache_hit_rate,
        node_type="cache",
        applies=lambda s: any(n.node_type == "cache" for n in s.nodes),
    ),
    Evaluator(
        name="retrieval_shortfall",
        description=(
            "Per-query min(observed/target, 1.0) across nodes with max_*/num_* limits "
            "on list-valued outputs. 1.0 = target met or exceeded."
        ),
        scope="per_query",
        compute=compute_retrieval_shortfall_per_query,
        applies=has_limit_node,
    ),
    Evaluator(
        name="mean_retrieval_shortfall",
        description="Mean of retrieval_shortfall across the round's results.",
        scope="per_round",
        compute=compute_mean_retrieval_shortfall,
        applies=has_limit_node,
    ),
    Evaluator(
        name="pipeline_compactness",
        description=(
            "1 - (active_steps - 1) / 11 — shorter pipelines score higher (single-node = 1.0)."
        ),
        scope="per_round",
        compute=compute_pipeline_compactness,
    ),
    Evaluator(
        name="prompt_compactness",
        description=(
            "1 - len(rendered_prompt) / PROMPT_BUDGET_CHARS — shorter prompts score "
            "higher (≤ budget → 1.0, ≥ budget → 0.0). Penalizes overly verbose "
            "prompt templates in the composite score."
        ),
        scope="per_round",
        compute=compute_prompt_compactness,
    ),
]


def all_evaluators() -> list[Evaluator]:
    """Return the full registry (copy)."""
    return list(_REGISTRY)


def _concrete_round_entries(
    schema: PipelineSchema,
) -> list[tuple[str, Evaluator, PipelineNode | None]]:
    """Per-round evaluators that apply. Node-type-bound names get namespaced when >1 matching node."""
    out: list[tuple[str, Evaluator, PipelineNode | None]] = []
    for ev in _REGISTRY:
        if ev.scope != "per_round":
            continue
        if not ev.applies(schema):
            continue
        if ev.node_type is None:
            out.append((ev.name, ev, None))
            continue
        matching = [n for n in schema.nodes if n.node_type == ev.node_type]
        namespace = len(matching) > 1
        for node in matching:
            display_name = f"{node.name}_{ev.name}" if namespace else ev.name
            out.append((display_name, ev, node))
    return out


def materialize_round_values(
    schema: PipelineSchema,
    results: list[QueryResult],
    *,
    opt_sp: Any = None,
) -> dict[str, float]:
    """Run every per-round evaluator that applies; return ``{name: value}``."""
    values: dict[str, float] = {}
    for display_name, ev, node in _concrete_round_entries(schema):
        kwargs: dict[str, Any] = {"results": results, "schema": schema, "opt_sp": opt_sp}
        if node is not None:
            kwargs["node"] = node
        values[display_name] = round(float(ev.compute(**kwargs)), 6)
    return values


def materialize_query_values(
    schema: PipelineSchema,
    result: QueryResult,
) -> dict[str, float]:
    """Run every per-query evaluator that applies on a single result."""
    values: dict[str, float] = {}
    for ev in _REGISTRY:
        if ev.scope != "per_query":
            continue
        if not ev.applies(schema):
            continue
        values[ev.name] = round(float(ev.compute(result=result, schema=schema)), 6)
    return values


def default_per_round_formula(schema: PipelineSchema) -> str:
    """Default per-round formula: ``0.65*accuracy + 0.15*health + 0.10*latency_norm
    + 0.05*recall + 0.05*prompt_compactness``.

    The ``prompt_compactness`` term is a small but visible verbosity penalty
    that nudges the optimizer toward shorter prompts at near-equal accuracy.
    Operators who want a stronger or weaker penalty override the formula
    via ``campaign.json::scoring`` (or interactively via ``scoring_steer.json``
    — see ``docs/operations/improvement-tracking.md``).
    """
    recall_names: list[str] = []
    for display_name, ev, _node in _concrete_round_entries(schema):
        if ev.node_type in ("candidate_source", "ranker", "cache"):
            recall_names.append(display_name)

    if recall_names:
        terms = " + ".join(recall_names)
        recall_expr = f"(({terms}) / {len(recall_names)})"
    else:
        recall_expr = "accuracy"

    health_expr = "(((1 - error_rate) + (1 - degraded_rate) + (1 - runtime_failure_rate)) / 3)"

    return (
        f"0.65 * accuracy + 0.15 * {health_expr} + 0.10 * latency_norm "
        f"+ 0.05 * {recall_expr} + 0.05 * prompt_compactness"
    )


def default_per_round_formula_short(schema: PipelineSchema) -> str:
    """One-line abbreviation of the default per-round formula.

    Sub-expressions collapsed to single letters (``H`` for health, ``R``
    for recall) so the round-level live render fits in a 70-char node
    frame at full evaluator names. The legend is rendered alongside the
    block when needed.

    Returns the same string shape as ``default_per_round_formula`` only
    when *schema* would otherwise produce the standard 5-term default.
    Custom formulas (``campaign.json::scoring``) bypass this helper —
    operators see their literal formula, wrapped if too long.
    """
    has_recall = any(
        ev.node_type in ("candidate_source", "ranker", "cache")
        for _name, ev, _node in _concrete_round_entries(schema)
    )
    recall_token = "R" if has_recall else "acc"
    return f"0.65*acc + 0.15*H + 0.10*lat + 0.05*{recall_token} + 0.05*pc"
