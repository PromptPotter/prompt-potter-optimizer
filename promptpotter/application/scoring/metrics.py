"""Scoring and metric computation for evaluation results.

Driven by the evaluator registry in ``application/scoring/evaluators.py``.
The per-round composite is whatever the dataset's per-round scoring formula
evaluates to; when no formula is set, the default formula reproduces the
pre-migration 4-bundle weighted sum.

Pure computation — no I/O, no backend dependencies.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from promptpotter.application.scoring.evaluators import (
    Evaluator,
    all_evaluators,
    default_per_round_formula,
    materialize_round_values,
)
from promptpotter.shared.errors import is_error_result
from promptpotter.shared.scoring import RoundScorer, compile_round_scorer

if TYPE_CHECKING:
    from promptpotter.domain.analysis import (
        DifficultyClass,
        FailureAnalysis,
        FailurePattern,
        QueryDifficulty,
    )
    from promptpotter.domain.pipeline_schema import (
        PipelineNode,
        PipelineSchema,
    )
    from promptpotter.domain.scoring import QueryResult


__all__ = [
    "Evaluator",
    "all_evaluators",
    "compile_failure_analysis",
    "compile_query_difficulty",
    "compute_composite_score",
    "compute_pipeline_metrics",
    "count_degraded_queries",
    "count_failures",
    "extract_sample_diagnostics",
    "find_rank",
    "is_degraded",
]


def find_rank(candidates: list, ground_truth: str) -> int | None:
    """Find 1-based rank of ground_truth in a candidates list.

    Works with plain strings, dicts with a ``candidate`` key,
    and list/tuple entries (uses first element).
    """
    if not candidates or not ground_truth:
        return None
    for i, c in enumerate(candidates):
        name = (
            c.get("candidate", c)
            if isinstance(c, dict)
            else (c[0] if isinstance(c, (list, tuple)) else str(c))
        )
        if str(name) == ground_truth:
            return i + 1
    return None


def _compute_accuracy(results: list[QueryResult]) -> dict:
    """Base scalars: hits, total, accuracy, errors.

    Kept as a thin function (not part of the registry) because several
    consumers read ``hits`` / ``total`` directly.
    """
    total = len(results)
    hits = sum(1 for r in results if r.get("hit"))
    errors = sum(1 for r in results if is_error_result(r))
    accuracy = sum(r.get("score", 0.0) for r in results) / total if total else 0.0
    return {"hits": hits, "total": total, "accuracy": accuracy, "errors": errors}


def count_failures(results: list[QueryResult]) -> int:
    """Count non-hit results (misses + errors)."""
    return sum(1 for r in results if not r.get("hit"))


def is_degraded(result: Mapping[str, Any]) -> bool:
    """A query is degraded iff its pipeline_data.diagnostics.warnings list is non-empty."""
    return bool((result.get("pipeline_data") or {}).get("diagnostics", {}).get("warnings"))


def count_degraded_queries(results: Sequence[Mapping[str, Any]]) -> int:
    """Count queries that have pipeline degradation warnings."""
    return sum(1 for r in results if is_degraded(r))


def _extract_candidate_label(c) -> str:
    """Extract the display name from a candidate (dict, tuple, or string)."""
    if isinstance(c, dict):
        return str(c.get("candidate", c))
    return c[0] if isinstance(c, (list, tuple)) else str(c)


# ---------------------------------------------------------------------------
# Per-query diagnostics — used by failure analysis and critique payloads.
# These remain in this module (not in the Evaluator registry) because they
# return typed mixed values (bool/int/str/None), not the float the registry
# requires. They piggyback on ``PipelineNode.node_type`` the same way the
# registry's recall evaluators do.
# ---------------------------------------------------------------------------


_DIAG_NODE_TYPES = ("candidate_source", "ranker", "enricher", "cache")


def extract_sample_diagnostics(
    result: Mapping[str, Any],
    pipeline_schema: PipelineSchema,
) -> dict[str, float | bool | int | str | None]:
    """Extract per-query diagnostic signals from a single evaluation result.

    Per-query complement to ``compute_pipeline_metrics()`` — returns a flat
    dict of named diagnostic values derived from the result's ``pipeline_data``
    and the schema's node types. Node-type metrics are namespaced as
    ``{node_name}_{metric}`` when multiple nodes share a type.
    """
    pd = result.get("pipeline_data") or {}
    gt = result.get("ground_truth", "")
    diag: dict[str, float | bool | int | str | None] = {}

    diag["terminated_at"] = pd.get("terminated_at")
    diag["total_time_ms"] = pd.get("total_time")
    diag["degraded"] = bool((pd.get("diagnostics") or {}).get("warnings"))
    diag["error"] = is_error_result(result)

    if not pd:
        return diag

    type_steps: dict[str, list[PipelineNode]] = {}
    for step in pipeline_schema.nodes:
        if step.node_type and step.node_type in _DIAG_NODE_TYPES:
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
    pipeline_data: Mapping[str, Any],
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
    node: PipelineNode,
    pd: Mapping[str, Any],
    gt: str,
) -> dict[str, float | bool | int | str | None]:
    candidates = pd.get("candidate_ranking", [])
    pos = _gt_position(candidates, gt)
    return {
        "gt_in_source": pos is not None,
        "n_source_candidates": len(candidates),
        "gt_source_rank": pos,
    }


def _diag_ranker(
    node: PipelineNode,
    pd: Mapping[str, Any],
    gt: str,
) -> dict[str, float | bool | int | str | None]:
    candidates = pd.get("final_ranking", [])
    pos = _gt_position(candidates, gt)

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
    node: PipelineNode,
    pd: Mapping[str, Any],
) -> dict[str, float | bool | int | str | None]:
    n = 0
    for mapping in node.observation_mappings:
        if pd.get(mapping.pipeline_key) is not None:
            n += 1
    return {"n_enriched_fields": n}


def _diag_cache(
    node: PipelineNode,
    pd: Mapping[str, Any],
) -> dict[str, float | bool | int | str | None]:
    timings = pd.get("step_timings") or {}
    return {"cache_hit": timings.get(node.name) is not None}


# ---------------------------------------------------------------------------
# Round-level composite — driven by the evaluator registry + scoring formula.
# ---------------------------------------------------------------------------


def compute_pipeline_metrics(
    pipeline_schema: PipelineSchema,
    results: list[QueryResult],
    *,
    opt_sp: Any = None,
    round_scorer: RoundScorer | str | None = None,
) -> dict[str, Any]:
    """Compute round-level metrics from the evaluator registry.

    Every per-round evaluator whose ``applies(schema)`` is True is
    materialized into a flat ``evaluators`` dict; names are namespaced
    when multiple nodes of the same type exist. The composite score is
    the result of evaluating ``round_scorer`` against that namespace.

    - ``round_scorer`` can be a compiled callable (via
      ``shared.scoring.compile_round_scorer``), a formula string, or
      ``None``. ``None`` uses the default formula produced by
      ``default_per_round_formula(schema)`` — reproduces the pre-migration
      4-bundle weighted sum on schemas that ship with recall nodes.
    - ``opt_sp`` is an optional ``OptSearchPoint``; when provided, its
      ``memory.validation_failures`` forces composite to 0.0 (structurally
      invalid candidates), and ``memory.runtime_failures`` feeds the
      ``runtime_failure_rate`` evaluator.

    The composite is **recorded, not gating**: ``_select_round_winner``
    compares candidates on ``accuracy`` (the user's per-query scoring
    function). Composite is displayed and persisted so operators can see
    whether a win came with hidden costs.
    """
    base = _compute_accuracy(results)
    evaluator_values = materialize_round_values(pipeline_schema, results, opt_sp=opt_sp)

    if callable(round_scorer):
        scorer = round_scorer
    elif isinstance(round_scorer, str):
        scorer = compile_round_scorer(round_scorer)
    else:
        scorer = compile_round_scorer(default_per_round_formula(pipeline_schema))

    composite = scorer(evaluator_values)

    # OptSP-layer counts for display and the validation-failure short-circuit.
    runtime_failure_count = 0
    validation_failure_count = 0
    if opt_sp is not None and hasattr(opt_sp, "memory"):
        runtime_failure_count = len(getattr(opt_sp.memory, "runtime_failures", []) or [])
        validation_failure_count = len(getattr(opt_sp.memory, "validation_failures", []) or [])

    if validation_failure_count > 0:
        composite = 0.0

    degraded = count_degraded_queries(results)

    return {
        **base,
        **evaluator_values,
        "evaluators": dict(evaluator_values),
        "composite": round(composite, 6),
        "degraded_queries": degraded,
        "validation_failure_count": validation_failure_count,
        "runtime_failure_count": runtime_failure_count,
    }


def compute_composite_score(
    results: list[QueryResult],
    pipeline_schema: PipelineSchema,
    *,
    opt_sp: Any = None,
    round_scorer: RoundScorer | str | None = None,
) -> dict:
    """Compute composite score — delegates to ``compute_pipeline_metrics()``.

    The composite is a **recorded** multi-signal diagnostic, not the
    winner-selection key. ``_select_round_winner`` compares candidates on
    ``accuracy`` (the user's performance scoring function).

    Returns dict with at least: hits, total, accuracy, errors, composite,
    evaluators (dict of named registry values), plus each evaluator's
    value at top level for legacy key access.
    """
    return compute_pipeline_metrics(
        pipeline_schema,
        results,
        opt_sp=opt_sp,
        round_scorer=round_scorer,
    )


# ---------------------------------------------------------------------------
# Failure analysis + query difficulty — unchanged semantics, kept here so
# consumers don't need to import from a new module.
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
    results: list[QueryResult],
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
    from promptpotter.domain.analysis import FailureAnalysis, FailurePattern

    failures = [r for r in results if not r.get("hit") and not is_error_result(r)]
    if not failures:
        return FailureAnalysis(total_failures=0, total_results=len(results))

    groups: dict[tuple[str, ...], list[QueryResult]] = defaultdict(list)
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
        patterns.append(
            FailurePattern(
                name=_auto_name(key),
                query_count=len(group),
                fraction=len(group) / total_failures,
                diagnostic_key=key,
                example_queries=[r.get("query", "") for r in group[:max_examples]],
                signals=diag_cache.get(key, {}),
            )
        )
        if len(patterns) >= max_patterns:
            break

    return FailureAnalysis(
        patterns=patterns,
        total_failures=total_failures,
        total_results=len(results),
    )


def compile_query_difficulty(
    historical_results: Sequence[Sequence[Mapping[str, Any]]],
) -> QueryDifficulty:
    """Classify queries by hit rate across multiple evaluation rounds."""
    from promptpotter.domain.analysis import QueryDifficulty, QueryProfile

    query_hits: dict[str, list[bool]] = defaultdict(list)
    for round_results in historical_results:
        for r in round_results:
            q = r.get("query", "")
            if q:
                query_hits[q].append(bool(r.get("hit")))

    profiles = []
    for query, hits in sorted(query_hits.items()):
        hit_rate = sum(hits) / len(hits) if hits else 0.0
        profiles.append(
            QueryProfile(
                query=query,
                hit_rate=hit_rate,
                n_measurements=len(hits),
                classification=_classify_difficulty(hit_rate),
            )
        )

    return QueryDifficulty(profiles=profiles)
