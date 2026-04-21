"""Capability Evaluators — registry, adaptors, materializers.

One ``Evaluator`` type holds a named scoring signal. Each evaluator has a
``scope`` (``per_query`` | ``per_round``), an ``applies(schema)`` predicate,
and a single ``compute`` callable (imported from ``evaluator_computes``).
Three framework adaptors wrap the same underlying ``compute`` so callers
can hand an evaluator to DSPy, pydantic-evals, or Langfuse without
re-implementing the signal.

The registry in this module is the single source of truth for what
evaluators exist. ``materialize_round_values()`` and
``materialize_query_values()`` are the only functions callers invoke
directly — they evaluate the registry against a schema + results, handling
node-type namespacing for evaluators that bind to a specific
``PipelineNode.node_type``.

Adding a new evaluator:
    1. Write a ``compute_*`` function in ``evaluator_computes``.
    2. Append an ``Evaluator(...)`` entry to ``_REGISTRY`` below.
    3. Reference it by name in a per-round or per-query scoring formula.

Composite is not special — it is whatever the per-round formula evaluates to.
"""

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
    """A named scoring signal.

    ``compute`` is the single underlying math. The three public adaptors
    (``__call__``, ``evaluate``, ``to_score``) all route through it or
    accept its output; callers pick the shape they need.

    For evaluators that target a specific pipeline ``node_type``, set
    ``node_type`` — the materializer fans out one concrete value per
    matching node (namespaced when multiple nodes of that type exist).
    Leave ``node_type`` as ``None`` for schema-wide signals.
    """

    name: str
    description: str
    scope: Scope
    compute: Callable[..., float]
    data_type: DataType = "NUMERIC"
    node_type: str | None = None
    applies: Callable[[PipelineSchema], bool] = field(default=lambda _schema: True)

    # ----- Adaptors --------------------------------------------------------

    def __call__(
        self,
        gold: dict | None = None,
        pred: dict | None = None,
        trace: Any = None,
    ) -> float:
        """DSPy-style metric: ``(gold, pred, trace) -> float``.

        Builds a synthetic per-query ``result`` dict from ``gold`` and
        ``pred`` and runs ``compute``. Only meaningful for per-query
        evaluators; per-round evaluators should be materialized via
        ``materialize_round_values()``.
        """
        if self.scope != "per_query":
            raise ValueError(
                f"Evaluator {self.name!r} is per-round; use "
                "materialize_round_values() instead of the DSPy adaptor."
            )
        result: dict[str, Any] = {}
        if gold:
            result.update(gold)
        if pred:
            result.update(pred)
        return float(self.compute(result=result))

    def evaluate(self, ctx: Any) -> dict[str, float]:
        """pydantic-evals adaptor: ``evaluate(ctx) -> {name: value}``.

        Reads ``ctx.inputs``, ``ctx.output``, ``ctx.expected_output`` (the
        fields pydantic-evals' ``EvaluatorContext`` exposes) into a synthetic
        per-query result, runs ``compute``, and returns a single-entry dict
        keyed on this evaluator's name.
        """
        if self.scope != "per_query":
            raise ValueError(f"Evaluator {self.name!r} is per-round; call compute directly.")
        inputs = getattr(ctx, "inputs", None) or {}
        output = getattr(ctx, "output", None)
        expected = getattr(ctx, "expected_output", None)
        result: dict[str, Any] = {
            **(inputs if isinstance(inputs, dict) else {"query": inputs}),
            "predicted": output if not isinstance(output, dict) else output.get("predicted", ""),
            "ground_truth": expected if not isinstance(expected, dict) else "",
        }
        if isinstance(output, dict):
            result.update(output)
        return {self.name: float(self.compute(result=result))}

    def to_score(self, value: float, *, comment: str = "") -> dict[str, Any]:
        """Langfuse score shape: ``{name, value, dataType, comment}``.

        Adaptor consumers can hand the return value to
        ``LangfuseLogger.create_score(**...)`` directly.
        """
        payload: dict[str, Any] = {
            "name": self.name,
            "value": float(value),
            "dataType": self.data_type,
        }
        if comment:
            payload["comment"] = comment
        return payload


# ---------------------------------------------------------------------------
# Registry — single source of truth.
# ---------------------------------------------------------------------------


def _has_node_type(node_type: str) -> Callable[[PipelineSchema], bool]:
    def _check(schema: PipelineSchema) -> bool:
        return any(n.node_type == node_type for n in schema.nodes)

    return _check


_REGISTRY: list[Evaluator] = [
    # --- Core per-round signals (always apply) ---
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
    # --- Node-type-bound per-round signals (namespaced when multiple nodes share a type) ---
    Evaluator(
        name="source_recall",
        description="Fraction of queries where GT appears in a candidate_source node's output.",
        scope="per_round",
        compute=compute_source_recall,
        node_type="candidate_source",
        applies=_has_node_type("candidate_source"),
    ),
    Evaluator(
        name="candidate_recall",
        description="Fraction of queries where GT appears in a ranker node's final_ranking.",
        scope="per_round",
        compute=compute_candidate_recall,
        node_type="ranker",
        applies=_has_node_type("ranker"),
    ),
    Evaluator(
        name="cache_hit_rate",
        description="Fraction of queries resolved by a cache node (non-null timing).",
        scope="per_round",
        compute=compute_cache_hit_rate,
        node_type="cache",
        applies=_has_node_type("cache"),
    ),
    # --- New evaluators shipped with the migration ---
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


# ---------------------------------------------------------------------------
# Materialization — turn the registry into concrete values for a schema.
# ---------------------------------------------------------------------------


def _concrete_round_entries(
    schema: PipelineSchema,
) -> list[tuple[str, Evaluator, PipelineNode | None]]:
    """Yield ``(output_name, evaluator, node_or_none)`` for every per-round
    evaluator that ``applies(schema)``. For node-type-bound evaluators with
    multiple matching nodes, names are namespaced as ``{node_name}_{name}``.
    """
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
    """Run every per-round evaluator that applies; return ``{name: value}``.

    Node-type evaluators with multiple matching nodes produce one entry per
    matching node with a ``{node_name}_{name}`` prefix. Schema-wide
    evaluators produce exactly one entry at their declared name.
    """
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


# ---------------------------------------------------------------------------
# Default per-round formula — reproduces the pre-migration composite math.
# ---------------------------------------------------------------------------


def default_per_round_formula(schema: PipelineSchema) -> str:
    """Return the default per-round scoring formula for *schema*.

    Mirrors the pre-migration 4-bundle weighted sum:
    ``0.7*accuracy + 0.15*health + 0.10*latency_norm + 0.05*recall``, where
    the health bundle is mean((1-error_rate)+(1-degraded_rate)+(1-runtime_failure_rate))
    and the recall bundle is the mean of whichever recall evaluators apply
    (falls back to ``accuracy`` when none apply).
    """
    # Build the recall term from the concrete names produced by
    # _concrete_round_entries so multi-node schemas pick up their
    # namespaced keys (e.g. ``web_search_source_recall``).
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
