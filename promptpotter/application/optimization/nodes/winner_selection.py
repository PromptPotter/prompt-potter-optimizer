"""Round-winner selection — compare candidate scores, pick the round winner."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from promptpotter.application.scoring.metrics import compute_composite_score

if TYPE_CHECKING:
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.scoring import QueryResult


__all__ = ["select_round_winner"]


def select_round_winner(
    candidates: list[OptSearchPoint],
    all_candidate_results: dict[str, list[QueryResult]],
    current_best: dict[str, Any],
    improvement_threshold: float,
    pipeline_schema: PipelineSchema | None = None,
    round_scorer: Any = None,
) -> dict[str, Any]:
    """Compare candidates and select the round winner (on accuracy, not composite)."""
    current_acc = current_best["accuracy"]
    current_composite = current_best.get("composite", current_acc)

    assert pipeline_schema is not None, "select_round_winner requires pipeline_schema"

    candidate_scores = {
        c.id: compute_composite_score(
            all_candidate_results[c.id],
            pipeline_schema,
            opt_sp=c,
            round_scorer=round_scorer,
        )
        for c in candidates
    }

    best_composite = current_composite
    best_acc = current_acc
    best_ps: OptSearchPoint = current_best["prompt_fields"]
    best_results = current_best["results"]
    best_label = current_best["label"]
    best_evaluators: dict[str, float] = current_best.get("evaluators") or {}
    winner_idx: int | None = None

    for idx, candidate in enumerate(candidates):
        c_scores = candidate_scores[candidate.id]
        c_acc = c_scores["accuracy"]
        if c_acc > best_acc:
            best_acc = c_acc
            best_composite = c_scores["composite"]
            best_ps = candidate
            best_results = all_candidate_results[candidate.id]
            best_label = candidate.changes_description or candidate.id[:12]
            best_evaluators = dict(c_scores.get("evaluators") or {})
            winner_idx = idx

    return {
        "label": best_label,
        "prompt_fields": best_ps,
        "accuracy": best_acc,
        "composite": best_composite,
        "hits": sum(1 for r in best_results if r.get("hit")),
        "total": len(best_results),
        "results": best_results,
        "candidates_scored": len(candidates),
        "improved": best_acc > current_acc + improvement_threshold,
        "winner_idx": winner_idx,
        "evaluators": best_evaluators,
    }
