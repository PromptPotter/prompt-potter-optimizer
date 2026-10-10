"""Reporting, not scoring; diagnostics print the 0-based POSITION off the 1-based ``find_rank``."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Any

from promptpotter.domain.pipeline_schema import NodeRole
from promptpotter.domain.results_health import is_degraded
from promptpotter.domain.scoring import PipelineData, extract_item_label, is_verifier_graded
from promptpotter.shared import text_list_items, text_list_rank
from promptpotter.shared.hashing import shapes_optimizer_prompt

shapes_optimizer_prompt(__name__)

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineNode, PipelineSchema
    from promptpotter.domain.scoring import GradedCell, MeasuredCell

__all__ = [
    "cell_feedback",
    "count_degraded_samples",
    "extract_sample_diagnostics",
    "find_rank",
    "judge_readings",
    "rank_ground_truth",
]


def rank_ground_truth(
    ranked: Sequence[Any], predicted: str, ground_truth: str
) -> tuple[int | None, int]:
    # A node emitting its whole list as ONE text blob (``llm_only``): ``predicted`` IS that list.
    if len(ranked) == 1 and "\n" in predicted:
        return text_list_rank(predicted, ground_truth), len(text_list_items(predicted))
    return find_rank(list(ranked), ground_truth), len(ranked)


def find_rank(items: list[Any], ground_truth: str) -> int | None:
    if not items or not ground_truth:
        return None
    for i, c in enumerate(items):
        if extract_item_label(c) == ground_truth:
            return i + 1
    return None


def count_degraded_samples(results: Iterable[GradedCell]) -> int:
    return sum(1 for cell in results if is_degraded(cell.facts))


def judge_readings(facts: MeasuredCell) -> list[tuple[str, str, str]]:
    return [
        (term, reading.label or "NOT GRADED", reading.why)
        for term, reading in facts.pipeline.judge_readings.items()
        if reading.why
    ]


def cell_feedback(cell: GradedCell) -> str:
    facts, grade = cell.facts, cell.grade
    if facts.errored:
        return f"The run failed ({facts.error_category}): {facts.error}"
    if grade.unscored is not None:
        return f"Not graded: {grade.unscored}"
    if grade.fitness is None or grade.objective is None:
        raise KeyError(f"cell_feedback: the cell at slot {facts.sample_id} carries no grade")
    lines = [f"Score: {grade.objective:.3f}"]
    if grade.fitness != grade.objective:
        lines.append(f"Correctness: {grade.fitness:.3f}")
    if not facts.verifier_graded:
        lines.append(f"Expected answer: {facts.ground_truth}")
    lines += [f"Judge ({term}): {label} — {why}" for term, label, why in judge_readings(facts)]
    return "\n".join(lines)


def extract_sample_diagnostics(
    facts: MeasuredCell,
    pipeline_schema: PipelineSchema,
) -> dict[str, float | bool | int | str | None]:
    pd = facts.pipeline
    gt = facts.ground_truth
    diag: dict[str, float | bool | int | str | None] = {}
    if pd == PipelineData():
        return diag

    role_counts = Counter(s.role for s in pipeline_schema.nodes)
    for step in pipeline_schema.nodes:
        extracted = _extract_node_diagnostics(step, pd, gt)
        if extracted is None:
            continue
        prefix = f"{step.name}_" if role_counts[step.role] > 1 else ""
        for k, v in extracted.items():
            diag[f"{prefix}{k}"] = v
    return diag


def _diag_candidate_source(
    node: PipelineNode, pd: PipelineData, gt: str
) -> dict[str, float | bool | int | str | None]:
    candidates = node.ranking_in(pd.observations) or []
    rank = find_rank(candidates, gt)
    pos = rank - 1 if rank is not None else None
    # ``gt_in_source`` is ABSENT rather than ``False`` where there is no label.
    return {
        "gt_in_source": None if is_verifier_graded(gt) else pos is not None,
        "n_source_candidates": len(candidates),
        "gt_source_rank": pos,
    }


def _diag_ranker(
    node: PipelineNode, pd: PipelineData, gt: str
) -> dict[str, float | bool | int | str | None]:
    candidates = node.ranking_in(pd.observations) or []
    rank = find_rank(candidates, gt)
    pos = rank - 1 if rank is not None else None
    top_score_gap: float | None = None
    if len(candidates) >= 2:
        # An item without a score contributes none: a gap against an invented 0.0 is not a gap.
        scores = [
            float(c["relevance_score"])
            for c in candidates[:2]
            if isinstance(c, dict) and isinstance(c.get("relevance_score"), (int, float))
        ]
        if len(scores) == 2:
            top_score_gap = scores[0] - scores[1]
    # A width-1 ranking and a labelless cell both leave the fact ABSENT rather than False.
    ranked = len(candidates) >= 2 and not is_verifier_graded(gt)
    return {
        "gt_in_ranked": (pos is not None) if ranked else None,
        "n_final_ranking": len(candidates),
        "gt_rank": pos if ranked else None,
        "top_score_gap": top_score_gap,
    }


def _diag_enricher(
    node: PipelineNode, pd: PipelineData, _gt: str
) -> dict[str, float | bool | int | str | None]:
    n = sum(1 for m in node.observation_mappings if pd.observations.get(m.pipeline_key) is not None)
    return {"n_enriched_fields": n}


def _diag_cache(
    node: PipelineNode, pd: PipelineData, _gt: str
) -> dict[str, float | bool | int | str | None]:
    return {"cache_hit": node.name in pd.step_timings}


def _extract_node_diagnostics(
    node: PipelineNode, pd: PipelineData, gt: str
) -> dict[str, float | bool | int | str | None] | None:
    match node.role:
        case NodeRole.CANDIDATE_SOURCE:
            return _diag_candidate_source(node, pd, gt)
        case NodeRole.RANKER:
            return _diag_ranker(node, pd, gt)
        case NodeRole.ENRICHER:
            return _diag_enricher(node, pd, gt)
        case NodeRole.CACHE:
            return _diag_cache(node, pd, gt)
        case _:
            return None
