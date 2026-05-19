"""Evaluator registry, materializers, and the pure compute functions they bind.

The registry (``_REGISTRY``) is the single source of truth for which round-
and query-level evaluators exist; each entry's ``compute`` callable is defined
in this same module just below. Per-round evaluators receive the round's
result list (and optionally an ``OptSearchPoint`` for memory-derived signals);
per-sample evaluators receive a single result. Every compute fn returns a
float in [0, 1].
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any, Literal

from promptpotter.application.scoring.formula import extract_item_label
from promptpotter.shared.errors import is_degraded, is_error_result

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineNode, PipelineSchema
    from promptpotter.domain.scoring import QueryMeasurement


Scope = Literal["per_sample", "per_round"]
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


# ---------------------------------------------------------------------------
# Compute functions — keyword-only, referenced by ``_REGISTRY`` below.
# ---------------------------------------------------------------------------


def compute_accuracy(*, results: list[QueryMeasurement], **_: Any) -> float:
    """Mean per-sample score across **non-deprecated** samples.

    Aligns with ``_compute_accuracy`` (metrics.py): deprecated samples
    (``is_deprecated`` — fatal or infra classifications) are excluded from
    both numerator and denominator. They are infrastructure failures, not
    signal about prompt quality, and they are already penalized via the
    ``runtime_failure_rate`` self-heal evaluator. Counting them in
    accuracy's denominator would double-penalize and break the
    ``accuracy == hits/total`` invariant downstream readers depend on.
    """
    # Lazy import to dodge the application/scoring → application/optimization
    # circular (matches the pattern in _compute_accuracy at metrics.py).
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
    return sum(1 for r in results if is_degraded(r)) / len(results)


@dataclass(frozen=True)
class SelfHealerSpec:
    """One self-healing channel: the OptSearchPoint attribute that lists its
    events, the evaluator id used in formula strings, and a short description.

    Each healer fires when a layer below patches a wound: L2 heals L1 on
    ``validation_failures`` / ``runtime_failures``; L3 heals L2 on
    ``l2_guard_breaches``; L3 self-heals on ``l3_guard_breaches``. Every
    event implies budget was spent recovering from a candidate that should
    have produced clean output the first time — composite_fitness penalizes
    candidates whose round triggers any of them.
    """

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
    """One parametric Evaluator per self-healing channel: events / sample-count, clipped."""

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
    """Fraction of non-error queries (for which *node* ran) where GT is in the
    node's output candidate list. Shared between source_recall and
    candidate_recall."""

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
        candidates: list = list(raw) if isinstance(raw, list) else []
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


def _retrieval_shortfall_for_result(
    result: QueryMeasurement, schema: PipelineSchema
) -> float | None:
    """Per-sample min(observed / target, 1.0) across all limit-bearing nodes.

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
    composite_fitness term degrades gracefully rather than cliff-edging at the
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
    # ``high`` = larger raw value is better (accuracy, recall, latency_norm,
    # …); ``low`` = larger raw value is worse (error_rate, degraded_rate,
    # runtime_failure_rate). Read by the webapp's What-If panel to
    # direction-correct values when recomputing under alternative
    # evaluator subsets. The active composite_fitness formula already
    # bakes direction in (e.g. ``1 - error_rate``); this field is the
    # raw-axis semantic.
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
    """Registry projection for the read-only webapp's What-If panel.

    Drops the ``compute`` callable and ``applies`` predicate (neither is
    JSON-serializable) and emits the static descriptive fields. The
    panel determines per-pipeline applicability from whichever names
    actually show up in a candidate's ``evaluators`` dict — the registry
    only needs to publish names, descriptions, and direction.
    """
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
    """Default per-round formula: ``0.55*accuracy + 0.30*self_heal + 0.05*latency
    + 0.05*recall + 0.05*prompt_compactness``.

    Self-healing carries combined 0.30 — second only to accuracy. Each wound
    channel rides its own ``(1 - <healer>_rate)`` term so the four healers
    surface independently in the dashboard (a candidate that triggers L3
    healing is penalized harder than one that only tripped validation). The
    weights split inside the self-heal budget by severity: L1/L2 wounds at
    0.10 each (these fire frequently and are the primary signal), L2/L3
    guard breaches at 0.05 each (rarer but indicate a deeper failure).

    The ``prompt_compactness`` term keeps a small verbosity penalty. Operators
    who want a stronger penalty (or a different split between healers)
    override the formula via ``campaign.json::scoring``.
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
    return f"0.55*acc + 0.30*SH + 0.05*lat + 0.05*{recall_token} + 0.05*pc"
