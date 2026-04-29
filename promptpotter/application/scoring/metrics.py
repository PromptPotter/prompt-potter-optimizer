"""Scoring and metric computation for measurement results.

Driven by the evaluator registry in ``application/scoring/evaluators.py``.
The per-round composite is whatever the dataset's per-round scoring formula
resolves to; when no formula is set, the default formula reproduces the
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
from promptpotter.application.scoring.formula import compile_round_scorer, extract_candidate_label
from promptpotter.domain.analysis import (
    FailureAnalysis,
    FailurePattern,
    QueryDifficulty,
    QueryProfile,
)
from promptpotter.domain.pipeline_schema import NodeType
from promptpotter.domain.scoring import RoundScorer
from promptpotter.shared.errors import is_degraded, is_error_result

if TYPE_CHECKING:
    from promptpotter.domain.analysis import DifficultyClass
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
    "count_degraded_queries",
    "extract_sample_diagnostics",
    "find_rank",
    "is_degraded",
]


def find_rank(candidates: list, ground_truth: str) -> int | None:
    """Return 1-based rank of *ground_truth* in *candidates*, or None."""
    if not candidates or not ground_truth:
        return None
    for i, c in enumerate(candidates):
        if extract_candidate_label(c) == ground_truth:
            return i + 1
    return None


def _compute_accuracy(results: list[QueryResult]) -> dict:
    """Base scalars: hits, total, accuracy, errors, deprecated.

    Deprecated samples (those whose ``classify_result()`` returns any fatal
    code) are not valid measurements and are excluded from ``hits``,
    ``total``, ``errors``, and the accuracy denominator. Their count
    surfaces as ``deprecated`` for operator transparency.

    Kept as a thin function (not part of the registry) because several
    consumers read ``hits`` / ``total`` directly.
    """
    from promptpotter.application.optimization.elimination import is_deprecated

    deprecated = sum(1 for r in results if is_deprecated(r))
    valid = [r for r in results if not is_deprecated(r)]
    total = len(valid)
    hits = sum(1 for r in valid if r.get("hit"))
    errors = sum(1 for r in valid if is_error_result(r))
    accuracy = sum(r.get("score", 0.0) for r in valid) / total if total else 0.0
    return {
        "hits": hits,
        "total": total,
        "accuracy": accuracy,
        "errors": errors,
        "deprecated": deprecated,
    }


def count_degraded_queries(results: Sequence[Mapping[str, Any]]) -> int:
    """Count queries that have pipeline degradation warnings."""
    return sum(1 for r in results if is_degraded(r))


# ---------------------------------------------------------------------------
# Per-query diagnostics — typed mixed values (bool/int/str/None), keyed off
# ``PipelineNode.node_type``.
# ---------------------------------------------------------------------------


def extract_sample_diagnostics(
    result: Mapping[str, Any],
    pipeline_schema: PipelineSchema,
) -> dict[str, float | bool | int | str | None]:
    """Extract per-query diagnostic signals; per-query complement to ``compute_composite_score``."""
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

    type_steps: dict[str, list[PipelineNode]] = {}
    for step in pipeline_schema.nodes:
        if step.node_type and step.node_type in _DIAG_DISPATCH:
            type_steps.setdefault(step.node_type, []).append(step)

    for _ntype, steps in type_steps.items():
        needs_namespace = len(steps) > 1
        for step in steps:
            prefix = f"{step.name}_" if needs_namespace else ""
            for k, v in _DIAG_DISPATCH[step.node_type](step, pd, gt).items():
                diag[f"{prefix}{k}"] = v
    return diag


def _gt_pos(candidates: list, gt: str) -> int | None:
    """0-based position of *gt* in *candidates*, or None."""
    for i, c in enumerate(candidates):
        if extract_candidate_label(c) == gt:
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


_DIAG_DISPATCH: dict[str, Any] = {
    NodeType.CANDIDATE_SOURCE: _diag_candidate_source,
    NodeType.RANKER: _diag_ranker,
    NodeType.ENRICHER: _diag_enricher,
    NodeType.CACHE: _diag_cache,
}


# ---------------------------------------------------------------------------
# Round-level composite — driven by the evaluator registry + scoring formula.
# ---------------------------------------------------------------------------


def compute_composite_score(
    results: list[QueryResult],
    pipeline_schema: PipelineSchema,
    *,
    opt_sp: Any = None,
    round_scorer: RoundScorer | str | None = None,
    l1_diversity: float = 1.0,
) -> dict[str, Any]:
    """Compute round-level metrics from the evaluator registry.

    Every per-round evaluator whose ``applies(schema)`` is True is
    materialized into a flat ``evaluators`` dict; names are namespaced
    when multiple nodes of the same type exist. The composite score is
    the result of evaluating ``round_scorer`` against that namespace.

    - ``round_scorer`` can be a compiled callable (via
      ``domain.scoring.compile_round_scorer``), a formula string, or
      ``None``. ``None`` uses the default formula produced by
      ``default_per_round_formula(schema)`` — reproduces the pre-migration
      4-bundle weighted sum on schemas that ship with recall nodes.
    - ``opt_sp`` is an optional ``OptSearchPoint``; when provided, its
      ``memory.validation_failures`` forces composite to 0.0 (structurally
      invalid candidates), and ``memory.runtime_failures`` feeds the
      ``runtime_failure_rate`` evaluator.
    - ``l1_diversity`` is the round-level fraction of valid (non-no-op,
      non-duplicate) L1 variants; defaults to 1.0 for non-L1 calls.

    The composite is **recorded, not gating**: ``select_fittest``
    compares candidates on ``accuracy`` (the user's per-query scoring
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

    composite = scorer(evaluator_values)

    # OptSP-layer counts for display and the validation-failure short-circuit.
    runtime_failure_count = 0
    validation_failure_count = 0
    if opt_sp is not None:
        runtime_failure_count = len(getattr(opt_sp, "runtime_failures", []) or [])
        validation_failure_count = len(getattr(opt_sp, "validation_failures", []) or [])

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
    patterns ranked by failure count. Deprecated samples (fatal warnings)
    are excluded so they don't pollute pattern groupings.
    """
    from promptpotter.application.optimization.elimination import is_deprecated

    failures = [
        r for r in results if not r.get("hit") and not is_error_result(r) and not is_deprecated(r)
    ]
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
    """Classify queries by hit rate across multiple scoring rounds.

    Deprecated samples (fatal warnings) are skipped so hit-rate
    classification isn't biased by garbage measurements.
    """
    from promptpotter.application.optimization.elimination import is_deprecated

    query_hits: dict[str, list[bool]] = defaultdict(list)
    for round_results in historical_results:
        for r in round_results:
            if is_deprecated(r):
                continue
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
