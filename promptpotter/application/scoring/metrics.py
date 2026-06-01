"""Scoring and metric computation for measurement results.

Driven by the evaluator registry in ``application/scoring/evaluators.py``.
The per-round composite_fitness is whatever the dataset's per-round scoring formula
resolves to; when no formula is set, the default formula reproduces the
pre-migration 4-bundle weighted sum.

Pure computation — no I/O, no backend dependencies.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from promptpotter.application.scoring.evaluators import (
    Evaluator,
    all_evaluators,
    default_per_round_formula,
    materialize_round_values,
)
from promptpotter.application.scoring.formula import compile_round_scorer, extract_item_label
from promptpotter.domain.pipeline_schema import NodeType
from promptpotter.domain.scoring import RoundScorer
from promptpotter.shared.errors import has_pipeline_warnings, is_error_result

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import (
        PipelineNode,
        PipelineSchema,
    )
    from promptpotter.domain.scoring import QueryMeasurement


__all__ = [
    "Evaluator",
    "all_evaluators",
    "compute_composite_fitness",
    "count_degraded_samples",
    "extract_sample_diagnostics",
    "find_rank",
    "has_pipeline_warnings",
    "matched_origin_stats",
]


def find_rank(items: list[Any], ground_truth: str) -> int | None:
    """Return 1-based rank of *ground_truth* in *items*, or None."""
    if not items or not ground_truth:
        return None
    for i, c in enumerate(items):
        if extract_item_label(c) == ground_truth:
            return i + 1
    return None


def _compute_accuracy(results: list[QueryMeasurement]) -> dict[str, Any]:
    """Base scalars: hits, total, accuracy, errors, deprecated.

    Deprecated samples (those whose ``classify_result()`` returns any fatal
    code) are not valid measurements and are excluded from ``hits``,
    ``total``, ``errors``, and the accuracy denominator. Their count
    surfaces as ``deprecated`` for operator transparency.

    Kept as a thin function (not part of the registry) because several
    consumers read ``hits`` / ``total`` directly.
    """
    from promptpotter.application.optimization.pobb.elimination import is_deprecated

    deprecated = sum(1 for r in results if is_deprecated(r))
    valid = [r for r in results if not is_deprecated(r)]
    total = len(valid)
    hits = sum(1 for r in valid if r.get("hit"))
    errors = sum(1 for r in valid if is_error_result(r))
    accuracy = sum(r.get("fitness", 0.0) for r in valid) / total if total else 0.0
    return {
        "hits": hits,
        "total": total,
        "accuracy": accuracy,
        "errors": errors,
        "deprecated": deprecated,
    }


def count_degraded_samples(results: Sequence[Mapping[str, Any]]) -> int:
    """Count samples that have pipeline degradation warnings."""
    return sum(1 for r in results if has_pipeline_warnings(r))


# ---------------------------------------------------------------------------
# Per-sample diagnostics — typed mixed values (bool/int/str/None), keyed off
# ``PipelineNode.node_type``.
# ---------------------------------------------------------------------------


def extract_sample_diagnostics(
    result: Mapping[str, Any],
    pipeline_schema: PipelineSchema,
) -> dict[str, float | bool | int | str | None]:
    """Extract per-sample diagnostic signals; per-sample complement to ``compute_composite_fitness``."""
    pd = result.get("pipeline_data") or {}
    gt = result.get("ground_truth", "")
    diag: dict[str, float | bool | int | str | None] = {
        "terminated_at": pd.get("terminated_at"),
        "total_time_ms": pd.get("total_time"),
        "degraded": bool((pd.get("diagnostics") or {}).get("warnings")),
        "error": is_error_result(result),
    }
    if not pd:
        return diag

    # Namespace a node's diagnostics by step name only when ≥2 nodes share its type.
    type_counts = Counter(s.node_type for s in pipeline_schema.nodes if s.node_type)
    for step in pipeline_schema.nodes:
        extracted = _extract_node_diagnostics(step, pd, gt)
        if extracted is None:
            continue
        prefix = f"{step.name}_" if type_counts[step.node_type] > 1 else ""
        for k, v in extracted.items():
            diag[f"{prefix}{k}"] = v
    return diag


def _gt_pos(items: list[Any], gt: str) -> int | None:
    """0-based position of *gt* in *items*, or None."""
    for i, c in enumerate(items):
        if extract_item_label(c) == gt:
            return i
    return None


def _diag_ranking(
    pd: Mapping[str, Any],
    gt: str,
    *,
    key: str,
    label: str,
) -> dict[str, float | bool | int | str | None]:
    """Shared shape for candidate_source + ranker diagnostics."""
    candidates = pd.get(key, [])
    pos = _gt_pos(candidates, gt)
    return {
        f"gt_in_{label}": pos is not None,
        f"n_{label}_candidates": len(candidates),
        f"gt_{label}_rank": pos,
    }


def _diag_candidate_source(
    node: PipelineNode, pd: Mapping[str, Any], gt: str
) -> dict[str, float | bool | int | str | None]:
    return _diag_ranking(pd, gt, key="candidate_ranking", label="source")


def _diag_ranker(
    node: PipelineNode, pd: Mapping[str, Any], gt: str
) -> dict[str, float | bool | int | str | None]:
    candidates = pd.get("final_ranking", [])
    pos = _gt_pos(candidates, gt)
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
    node: PipelineNode, pd: Mapping[str, Any], _gt: str
) -> dict[str, float | bool | int | str | None]:
    n = sum(1 for m in node.observation_mappings if pd.get(m.pipeline_key) is not None)
    return {"n_enriched_fields": n}


def _diag_cache(
    node: PipelineNode, pd: Mapping[str, Any], _gt: str
) -> dict[str, float | bool | int | str | None]:
    timings = pd.get("step_timings") or {}
    return {"cache_hit": timings.get(node.name) is not None}


def _extract_node_diagnostics(
    node: PipelineNode, pd: Mapping[str, Any], gt: str
) -> dict[str, float | bool | int | str | None] | None:
    """Per-node diagnostic extractor — None for node types without one. Explicit match so
    ``grep _diag_ranker`` lands on the call site (no string-keyed dispatch table)."""
    match node.node_type:
        case NodeType.CANDIDATE_SOURCE:
            return _diag_candidate_source(node, pd, gt)
        case NodeType.RANKER:
            return _diag_ranker(node, pd, gt)
        case NodeType.ENRICHER:
            return _diag_enricher(node, pd, gt)
        case NodeType.CACHE:
            return _diag_cache(node, pd, gt)
        case _:
            return None


# ---------------------------------------------------------------------------
# Round-level composite_fitness — driven by the evaluator registry + scoring formula.
# ---------------------------------------------------------------------------


def compute_composite_fitness(
    results: list[QueryMeasurement],
    pipeline_schema: PipelineSchema,
    *,
    opt_sp: Any = None,
    round_scorer: RoundScorer | str | None = None,
    l1_diversity: float = 1.0,
) -> dict[str, Any]:
    """Compute round-level metrics from the evaluator registry.

    Every per-round evaluator whose ``applies(schema)`` is True is
    materialized into a flat ``evaluators`` dict; names are namespaced
    when multiple nodes of the same type exist. The composite_fitness score is
    the result of evaluating ``round_scorer`` against that namespace.

    - ``round_scorer`` can be a compiled callable (via
      ``domain.scoring.compile_round_scorer``), a formula string, or
      ``None``. ``None`` uses the default formula produced by
      ``default_per_round_formula(schema)`` — reproduces the pre-migration
      4-bundle weighted sum on schemas that ship with recall nodes.
    - ``opt_sp`` is an optional ``OptSearchPoint``; when provided, its
      ``memory.validation_failures`` forces composite_fitness to 0.0 (structurally
      invalid candidates), and ``memory.runtime_failures`` feeds the
      ``runtime_failure_rate`` evaluator.
    - ``l1_diversity`` is the round-level fraction of valid (non-no-op,
      non-duplicate) L1 variants; defaults to 1.0 for non-L1 calls.

    The composite_fitness is **recorded, not gating**: ``select_fittest``
    compares candidates on ``accuracy`` (the user's per-sample scoring
    function). Composite is displayed and persisted so operators can see
    whether a win came with hidden costs.
    """
    base = _compute_accuracy(results)
    evaluator_values = materialize_round_values(pipeline_schema, results, opt_sp=opt_sp)
    # L1-generation quality is a batch property, not a per-result derivation —
    # injected after registry materialization so operator formulas can
    # reference ``l1_diversity`` via campaign.json::scoring / scoring_steer.json.
    evaluator_values["l1_diversity"] = round(float(l1_diversity), 6)

    if callable(round_scorer):
        scorer = round_scorer
    elif isinstance(round_scorer, str):
        scorer = compile_round_scorer(round_scorer)
    else:
        scorer = compile_round_scorer(default_per_round_formula(pipeline_schema))

    composite_fitness = scorer(evaluator_values)

    # OptSP-layer counts for display and the validation-failure short-circuit.
    runtime_failure_count = 0
    validation_failure_count = 0
    if opt_sp is not None:
        runtime_failure_count = len(opt_sp.memory.wounds.runtime_failures)
        validation_failure_count = len(opt_sp.memory.wounds.validation_failures)

    if validation_failure_count > 0:
        composite_fitness = 0.0

    degraded = count_degraded_samples(results)

    return {
        **base,
        **evaluator_values,
        "evaluators": dict(evaluator_values),
        "composite_fitness": round(composite_fitness, 6),
        "degraded_samples": degraded,
        "validation_failure_count": validation_failure_count,
        "runtime_failure_count": runtime_failure_count,
    }


def matched_origin_stats(
    origin_results: list[QueryMeasurement],
    candidate_results: list[QueryMeasurement],
    pipeline_schema: PipelineSchema,
    *,
    round_scorer: RoundScorer | str | None = None,
) -> dict[str, Any]:
    """Origin's accuracy/hits/composite restricted to the candidate's measured samples.

    When PoBB leader-locks a candidate at q8/20, returns origin's stats on
    only those 8 samples — the apples-to-apples comparison PoBB's matched-pair
    posterior is built on. Degenerates to full origin stats when the candidate
    measured every sample. ``opt_sp=None`` for the origin composite so
    opt_sp-aware evaluators (e.g. ``prompt_compactness``) take their vacuous
    fallback in both numerator and denominator of the delta.
    """
    candidate_sids = {r.get("sample_id") for r in candidate_results}
    matched = [r for r in origin_results if r.get("sample_id") in candidate_sids]
    base = _compute_accuracy(matched)
    composite = compute_composite_fitness(
        matched,
        pipeline_schema,
        opt_sp=None,
        round_scorer=round_scorer,
        l1_diversity=1.0,
    )
    return {
        "accuracy": base["accuracy"],
        "hits": base["hits"],
        "total": base["total"],
        "composite_fitness": composite["composite_fitness"],
    }
