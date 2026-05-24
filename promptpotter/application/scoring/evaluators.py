"""Evaluator registry + materializers; per-round/per-sample compute fns return float in [0, 1]."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any, Literal

from promptpotter.application.scoring.formula import extract_item_label
from promptpotter.shared.errors import has_pipeline_warnings, is_error_result

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineNode, PipelineSchema
    from promptpotter.domain.scoring import QueryMeasurement


Scope = Literal["per_sample", "per_round"]
DataType = Literal["NUMERIC", "BOOLEAN"]


LATENCY_BUDGET_MS = 10_000.0  # ≥ budget → 0.0, 0 → 1.0
PROMPT_BUDGET_CHARS = 4_000  # ≈ 1000 tokens; soft linear ceiling


__all__ = [
    "LATENCY_BUDGET_MS",
    "PROMPT_BUDGET_CHARS",
    "SELF_HEALERS",
    "Evaluator",
    "SelfHealerSpec",
    "all_evaluators",
    "default_per_round_formula",
    "default_per_round_formula_short",
    "evaluators_meta",
    "materialize_round_values",
    "materialize_sample_values",
]


def compute_accuracy(*, results: list[QueryMeasurement], **_: Any) -> float:
    """Mean fitness over non-deprecated samples (deprecated already penalized by runtime_failure_rate)."""
    # Lazy import: scoring → optimization circular.
    from promptpotter.application.optimization.pobb.elimination import is_deprecated

    valid = [r for r in results if not is_deprecated(r)]
    if not valid:
        return 0.0
    return sum(r.get("fitness", 0.0) for r in valid) / len(valid)


def compute_error_rate(*, results: list[QueryMeasurement], **_: Any) -> float:
    if not results:
        return 0.0
    return sum(1 for r in results if is_error_result(r)) / len(results)


def compute_degraded_rate(*, results: list[QueryMeasurement], **_: Any) -> float:
    if not results:
        return 0.0
    return sum(1 for r in results if has_pipeline_warnings(r)) / len(results)


@dataclass(frozen=True)
class SelfHealerSpec:
    name: str
    attr: str
    description: str


SELF_HEALERS: tuple[SelfHealerSpec, ...] = (
    SelfHealerSpec(
        "validation_failure_rate",
        "validation_failures",
        "Fraction of samples where L1 output was malformed; L2 healed.",
    ),
    SelfHealerSpec(
        "runtime_failure_rate",
        "runtime_failures",
        "Fraction of samples that triggered DegradationCheck; L2 healed.",
    ),
    SelfHealerSpec(
        "l2_guard_breach_rate",
        "l2_guard_breaches",
        "Fraction of samples where L2 refinement breached guards; L3 healed.",
    ),
    SelfHealerSpec(
        "l3_guard_breach_rate",
        "l3_guard_breaches",
        "Fraction of samples where L3 plan breached its own guards.",
    ),
)


def _make_self_healer_evaluator(spec: SelfHealerSpec) -> Evaluator:
    def compute(*, results: list[QueryMeasurement], opt_sp: Any = None, **_: Any) -> float:
        if not results or opt_sp is None:
            return 0.0
        events = getattr(opt_sp, spec.attr, None) or []
        return min(len(events) / len(results), 1.0)

    return Evaluator(
        name=spec.name,
        description=spec.description,
        scope="per_round",
        compute=compute,
        direction="low",
    )


def compute_latency_norm(*, results: list[QueryMeasurement], **_: Any) -> float:
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
    results: list[QueryMeasurement],
    node: PipelineNode,
    candidate_key: str,
    **_: Any,
) -> float:
    """Fraction of non-error queries (where *node* ran) with GT in *candidate_key*'s list."""

    def _step_ran(r: QueryMeasurement) -> bool:
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
        candidates: list[Any] = list(raw) if isinstance(raw, list) else []
        gt = r.get("ground_truth", "")
        if any(extract_item_label(c) == gt for c in candidates):
            found += 1
    return found / len(scoped)


def compute_cache_hit_rate(
    *, results: list[QueryMeasurement], node: PipelineNode, **_: Any
) -> float:
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


def _retrieval_shortfall_for_result(
    result: QueryMeasurement, schema: PipelineSchema
) -> float | None:
    """Per-sample mean of min(observed/target, 1.0) over limit-bearing nodes; None when none apply."""
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


def compute_retrieval_shortfall_per_sample(
    *, result: QueryMeasurement, schema: PipelineSchema | None = None, **_: Any
) -> float:
    if schema is None:
        return 1.0
    v = _retrieval_shortfall_for_result(result, schema)
    return 1.0 if v is None else v


def compute_mean_retrieval_shortfall(
    *, results: list[QueryMeasurement], schema: PipelineSchema, **_: Any
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
    n = len(schema.active_steps)
    if n <= 1:
        return 1.0
    worst = 12
    return max(0.0, 1.0 - (n - 1) / (worst - 1))


def compute_prompt_compactness(*, opt_sp: Any = None, **_: Any) -> float:
    """``1 - len(render)/budget``; returns 1.0 on missing/empty so a vacuous prompt doesn't fake a penalty."""
    if opt_sp is None or not hasattr(opt_sp, "render"):
        return 1.0
    rendered = opt_sp.render() or ""
    if not rendered:
        return 1.0
    return max(0.0, 1.0 - len(rendered) / PROMPT_BUDGET_CHARS)


@dataclass(frozen=True)
class Evaluator:
    name: str
    description: str
    scope: Scope
    compute: Callable[..., float]
    data_type: DataType = "NUMERIC"
    # `high` = larger raw is better; `low` = larger is worse. Webapp What-If panel uses this to direction-correct.
    direction: Literal["high", "low"] = "high"
    node_type: str | None = None
    applies: Callable[[PipelineSchema], bool] = field(default=lambda _schema: True)


_REGISTRY: list[Evaluator] = [
    Evaluator(
        name="accuracy",
        description="Mean per-sample score across non-deprecated samples.",
        scope="per_round",
        compute=compute_accuracy,
    ),
    Evaluator(
        name="error_rate",
        description="Fraction of queries that errored (ERROR predicted or exception).",
        scope="per_round",
        compute=compute_error_rate,
        direction="low",
    ),
    Evaluator(
        name="degraded_rate",
        description="Fraction of queries that completed with pipeline degradation warnings.",
        scope="per_round",
        compute=compute_degraded_rate,
        direction="low",
    ),
    # Self-healing channels — one Evaluator per SelfHealerSpec. Each penalizes
    # candidates whose round triggered the corresponding wound + lower-layer
    # heal. Operators weight them in the composite_fitness formula; the default
    # formula gives the four combined ~0.30, second only to accuracy.
    *(_make_self_healer_evaluator(spec) for spec in SELF_HEALERS),
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
        compute=partial(_compute_recall, candidate_key="candidate_ranking"),
        node_type="candidate_source",
        applies=lambda s: any(n.node_type == "candidate_source" for n in s.nodes),
    ),
    Evaluator(
        name="candidate_recall",
        description="Fraction of queries where GT appears in a ranker node's final_ranking.",
        scope="per_round",
        compute=partial(_compute_recall, candidate_key="final_ranking"),
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
            "Per-sample min(observed/target, 1.0) across nodes with max_*/num_* limits "
            "on list-valued outputs. 1.0 = target met or exceeded."
        ),
        scope="per_sample",
        compute=compute_retrieval_shortfall_per_sample,
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
        direction="low",
    ),
    Evaluator(
        name="prompt_compactness",
        description=(
            "1 - len(rendered_prompt) / PROMPT_BUDGET_CHARS — shorter prompts score "
            "higher (≤ budget → 1.0, ≥ budget → 0.0). Penalizes overly verbose "
            "prompt templates in the composite_fitness score."
        ),
        scope="per_round",
        compute=compute_prompt_compactness,
    ),
]


def all_evaluators() -> list[Evaluator]:
    """Return the full registry (copy)."""
    return list(_REGISTRY)


def evaluators_meta() -> list[dict[str, Any]]:
    """JSON-serializable registry projection for the webapp What-If panel — drops `compute`/`applies`."""
    return [
        {
            "name": ev.name,
            "description": ev.description,
            "scope": ev.scope,
            "data_type": ev.data_type,
            "direction": ev.direction,
            "node_type": ev.node_type,
        }
        for ev in _REGISTRY
    ]


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
    results: list[QueryMeasurement],
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


def materialize_sample_values(
    schema: PipelineSchema,
    result: QueryMeasurement,
) -> dict[str, float]:
    """Run every per-sample evaluator that applies on a single result."""
    values: dict[str, float] = {}
    for ev in _REGISTRY:
        if ev.scope != "per_sample":
            continue
        if not ev.applies(schema):
            continue
        values[ev.name] = round(float(ev.compute(result=result, schema=schema)), 6)
    return values


def default_per_round_formula(schema: PipelineSchema) -> str:
    """``0.55*accuracy + 0.30*self_heal + 0.05*latency + 0.05*recall + 0.05*prompt_compactness``.

    Self-heal split: L1/L2 wounds 0.10 each (frequent, primary signal); L2/L3 guard
    breaches 0.05 each (rare, deeper failures). Override via `campaign.json::scoring`.
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

    # Per-channel weights inside the 0.30 self-heal budget. Edit one place to
    # rebalance — name-driven so SELF_HEALERS additions only need a new entry.
    healer_weights = {
        "validation_failure_rate": 0.10,
        "runtime_failure_rate": 0.10,
        "l2_guard_breach_rate": 0.05,
        "l3_guard_breach_rate": 0.05,
    }
    self_heal_terms = " + ".join(
        f"{w} * (1 - {spec.name})" for spec in SELF_HEALERS if (w := healer_weights.get(spec.name))
    )

    return (
        f"0.55 * accuracy + {self_heal_terms} + 0.05 * latency_norm "
        f"+ 0.05 * {recall_expr} + 0.05 * prompt_compactness"
    )


def default_per_round_formula_short(schema: PipelineSchema) -> str:
    """Single-letter abbreviation of the default formula — fits the 70-char live-render frame."""
    has_recall = any(
        ev.node_type in ("candidate_source", "ranker", "cache")
        for _name, ev, _node in _concrete_round_entries(schema)
    )
    recall_token = "R" if has_recall else "acc"
    return f"0.55*acc + 0.30*SH + 0.05*lat + 0.05*{recall_token} + 0.05*pc"
