"""Per-sample diagnostics — what the round-analysis panel and the terminal MISS line read off one
measured row. Reporting, not scoring: nothing here feeds ``composite_fitness``, and the rank it
computes is the 0-based *position* those surfaces print, off the 1-based ``find_rank`` walk.

Split from ``metrics.py``, which had grown a bag name over three concerns with near-disjoint
callers: this one is read by ``round_analysis``, ``sample_measurement`` and the live phase view."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from promptpotter.application.scoring.formula import extract_item_label
from promptpotter.domain.pipeline_schema import NodeType
from promptpotter.shared.errors import has_pipeline_warnings, is_error_result

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineNode, PipelineSchema

__all__ = [
    "count_degraded_samples",
    "extract_sample_diagnostics",
    "find_rank",
]


def find_rank(items: list[Any], ground_truth: str) -> int | None:
    """1-based rank, or ``None`` — diagnostics report the position 0-based."""
    if not items or not ground_truth:
        return None
    for i, c in enumerate(items):
        if extract_item_label(c) == ground_truth:
            return i + 1
    return None


def count_degraded_samples(results: Sequence[Mapping[str, Any]]) -> int:
    return sum(1 for r in results if has_pipeline_warnings(r))


# ---------------------------------------------------------------------------
# Per-sample diagnostics — typed mixed values (bool/int/str/None), keyed off
# ``PipelineNode.node_type``.
# ---------------------------------------------------------------------------


def extract_sample_diagnostics(
    result: Mapping[str, Any],
    pipeline_schema: PipelineSchema,
) -> dict[str, float | bool | int | str | None]:
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


def _diag_ranking(
    pd: Mapping[str, Any],
    gt: str,
    *,
    key: str,
    label: str,
) -> dict[str, float | bool | int | str | None]:
    """Diagnostics report the ground-truth position 0-based; :func:`find_rank` is the canonical
    1-based walk."""
    candidates = pd.get(key, [])
    rank = find_rank(candidates, gt)
    pos = rank - 1 if rank is not None else None
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
    rank = find_rank(candidates, gt)
    pos = rank - 1 if rank is not None else None
    top_score_gap: float | None = None
    if len(candidates) >= 2:
        # Two shapes reach here, both across the highway contract: TermNorm emits scored dicts
        # keyed `relevance_score` (its fuzzy arm converts its own `(term, score)` tuples before
        # they leave), and `llm_only` emits bare answer strings, which carry no score. Nothing
        # emits a `similarity` key or a bare tuple. An item without a score contributes none —
        # a gap between a real score and an invented 0.0 is not a gap.
        scores = [
            float(c["relevance_score"])
            for c in candidates[:2]
            if isinstance(c, dict) and isinstance(c.get("relevance_score"), (int, float))
        ]
        if len(scores) == 2:
            top_score_gap = scores[0] - scores[1]
    # A width-1 ranking has no ranking to report, and the panel prints these on MISS lines
    # only — so `gt_in_ranked` was False on every line it could ever appear on. `None` where
    # the fact is absent, which the panel's `is not None` guard already reads.
    ranked = len(candidates) >= 2
    return {
        "gt_in_ranked": (pos is not None) if ranked else None,
        "n_final_ranking": len(candidates),
        "gt_rank": pos if ranked else None,
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
    """Explicit match rather than a string-keyed table, so grepping a diagnostic's name lands on
    its call site."""
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
