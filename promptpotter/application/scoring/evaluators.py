"""Capability Evaluators — first-class scoring signals with a single registry.

One ``Evaluator`` type holds a named scoring signal. Each evaluator has a
``scope`` (``per_query`` | ``per_round``), an ``applies(schema)`` predicate,
and a single ``compute`` callable. Three framework adaptors wrap the same
underlying ``compute`` so callers can hand an evaluator to DSPy, pydantic-evals,
or Langfuse without re-implementing the signal.

The registry below is the single source of truth for what evaluators exist.
``materialize_round_values()`` and ``materialize_query_values()`` are the only
functions callers invoke directly — they evaluate the registry against a
schema + results, handling node-type namespacing for evaluators that bind to
a specific ``PipelineNode.node_type``.

Adding a new evaluator:
    1. Write a ``_compute_*`` function (takes keyword args).
    2. Append an ``Evaluator(...)`` entry to ``_REGISTRY``.
    3. Reference it by name in a per-round or per-query scoring formula.

Composite is not special — it is whatever the per-round formula evaluates to.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from promptpotter.shared.errors import is_error_result

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineNode, PipelineSchema
    from promptpotter.domain.scoring import QueryResult


Scope = Literal["per_query", "per_round"]
DataType = Literal["NUMERIC", "BOOLEAN"]

# Latency budget for the ``latency_norm`` evaluator. Mean latency ≥ this
# contributes 0; ≤ 0 contributes 1.0. Kept here so the composite math that
# used to live in ``metrics.py`` has one home.
LATENCY_BUDGET_MS = 10_000.0


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
# Compute functions — pure, keyword-only, no side effects.
# ---------------------------------------------------------------------------


def _compute_accuracy(*, results: list[QueryResult], **_: Any) -> float:
    if not results:
        return 0.0
    return sum(r.get("score", 0.0) for r in results) / len(results)


def _compute_error_rate(*, results: list[QueryResult], **_: Any) -> float:
    if not results:
        return 0.0
    return sum(1 for r in results if is_error_result(r)) / len(results)


def _is_degraded(result: Any) -> bool:
    return bool((result.get("pipeline_data") or {}).get("diagnostics", {}).get("warnings"))


def _compute_degraded_rate(*, results: list[QueryResult], **_: Any) -> float:
    if not results:
        return 0.0
    return sum(1 for r in results if _is_degraded(r)) / len(results)


def _compute_runtime_failure_rate(
    *, results: list[QueryResult], opt_sp: Any = None, **_: Any
) -> float:
    if not results or opt_sp is None or not hasattr(opt_sp, "memory"):
        return 0.0
    count = len(getattr(opt_sp.memory, "runtime_failures", []) or [])
    return min(count / len(results), 1.0)


def _compute_latency_norm(*, results: list[QueryResult], **_: Any) -> float:
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


def _compute_source_recall(**kwargs: Any) -> float:
    return _compute_recall(candidate_key="candidate_ranking", **kwargs)


def _compute_candidate_recall(**kwargs: Any) -> float:
    return _compute_recall(candidate_key="final_ranking", **kwargs)


def _compute_cache_hit_rate(*, results: list[QueryResult], node: PipelineNode, **_: Any) -> float:
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
# New evaluators — retrieval_shortfall, mean_retrieval_shortfall, pipeline_compactness.
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


def _compute_retrieval_shortfall_per_query(
    *, result: QueryResult, schema: PipelineSchema | None = None, **_: Any
) -> float:
    if schema is None:
        return 1.0
    v = _retrieval_shortfall_for_result(result, schema)
    return 1.0 if v is None else v


def _compute_mean_retrieval_shortfall(
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


def _compute_pipeline_compactness(*, schema: PipelineSchema, **_: Any) -> float:
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


# ---------------------------------------------------------------------------
# Registry — single source of truth.
# ---------------------------------------------------------------------------


def _has_node_type(node_type: str) -> Callable[[PipelineSchema], bool]:
    def _check(schema: PipelineSchema) -> bool:
        return any(n.node_type == node_type for n in schema.nodes)

    return _check


def _has_limit_node(schema: PipelineSchema) -> bool:
    return bool(_limit_nodes(schema))


_REGISTRY: list[Evaluator] = [
    # --- Core per-round signals (always apply) ---
    Evaluator(
        name="accuracy",
        description="Mean per-query score across the round's result set.",
        scope="per_round",
        compute=_compute_accuracy,
    ),
    Evaluator(
        name="error_rate",
        description="Fraction of queries that errored (ERROR predicted or exception).",
        scope="per_round",
        compute=_compute_error_rate,
    ),
    Evaluator(
        name="degraded_rate",
        description="Fraction of queries that completed with pipeline degradation warnings.",
        scope="per_round",
        compute=_compute_degraded_rate,
    ),
    Evaluator(
        name="runtime_failure_rate",
        description="Runtime failure count on OptSP memory, normalized by total queries.",
        scope="per_round",
        compute=_compute_runtime_failure_rate,
    ),
    Evaluator(
        name="latency_norm",
        description=(
            "Mean latency normalized against LATENCY_BUDGET_MS (1.0 = instant, 0.0 = ≥ budget)."
        ),
        scope="per_round",
        compute=_compute_latency_norm,
    ),
    # --- Node-type-bound per-round signals (namespaced when multiple nodes share a type) ---
    Evaluator(
        name="source_recall",
        description="Fraction of queries where GT appears in a candidate_source node's output.",
        scope="per_round",
        compute=_compute_source_recall,
        node_type="candidate_source",
        applies=_has_node_type("candidate_source"),
    ),
    Evaluator(
        name="candidate_recall",
        description="Fraction of queries where GT appears in a ranker node's final_ranking.",
        scope="per_round",
        compute=_compute_candidate_recall,
        node_type="ranker",
        applies=_has_node_type("ranker"),
    ),
    Evaluator(
        name="cache_hit_rate",
        description="Fraction of queries resolved by a cache node (non-null timing).",
        scope="per_round",
        compute=_compute_cache_hit_rate,
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
        compute=_compute_retrieval_shortfall_per_query,
        applies=_has_limit_node,
    ),
    Evaluator(
        name="mean_retrieval_shortfall",
        description="Mean of retrieval_shortfall across the round's results.",
        scope="per_round",
        compute=_compute_mean_retrieval_shortfall,
        applies=_has_limit_node,
    ),
    Evaluator(
        name="pipeline_compactness",
        description=(
            "1 - (active_steps - 1) / 11 — shorter pipelines score higher (single-node = 1.0)."
        ),
        scope="per_round",
        compute=_compute_pipeline_compactness,
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
