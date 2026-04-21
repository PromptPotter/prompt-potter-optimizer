"""Evaluator registry + materializers. Registry is single source of truth; compute fns live in ``evaluator_computes``."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from promptpotter.application.scoring.evaluator_computes import (
    LATENCY_BUDGET_MS,
    compute_accuracy,
    compute_cache_hit_rate,
    compute_candidate_recall,
    compute_degraded_rate,
    compute_error_rate,
    compute_latency_norm,
    compute_mean_retrieval_shortfall,
    compute_pipeline_compactness,
    compute_retrieval_shortfall_per_query,
    compute_runtime_failure_rate,
    compute_source_recall,
    has_limit_node,
)

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineNode, PipelineSchema
    from promptpotter.domain.scoring import QueryResult


Scope = Literal["per_query", "per_round"]
DataType = Literal["NUMERIC", "BOOLEAN"]


__all__ = [
    "LATENCY_BUDGET_MS",
    "Evaluator",
    "all_evaluators",
    "default_per_round_formula",
    "materialize_query_values",
    "materialize_round_values",
]


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
    """Default per-round formula: 0.7*accuracy + 0.15*health + 0.10*latency_norm + 0.05*recall."""
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

    return f"0.7 * accuracy + 0.15 * {health_expr} + 0.10 * latency_norm + 0.05 * {recall_expr}"
