"""L1 scoring orchestrator: parse → score → select winner → record decision."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from promptpotter.application.optimization.nodes.l1.measure import (
    parse_population,
    score_population,
)
from promptpotter.application.scoring.metrics import compute_composite_score, count_degraded_queries
from promptpotter.domain.analysis import EscalationSignal
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.scoring import QueryResult

if TYPE_CHECKING:
    from promptpotter.application.campaign.callbacks import RunListener
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.domain.pipeline_schema import PipelineSchema


__all__ = ["L1ScoringResult", "l1_score", "select_round_winner"]


def select_round_winner(
    population: list[OptSearchPoint],
    all_candidate_results: dict[str, list[QueryResult]],
    current_best: dict[str, Any],
    improvement_threshold: float,
    pipeline_schema: PipelineSchema | None = None,
    round_scorer: Any = None,
) -> dict[str, Any]:
    """Compare population and select the round winner (on fitness, not composite)."""
    current_fitness = current_best["accuracy"]
    current_composite = current_best.get("composite", current_fitness)

    assert pipeline_schema is not None, "select_round_winner requires pipeline_schema"

    individual_scores = {
        c.lineage.id: compute_composite_score(
            all_candidate_results[c.lineage.id],
            pipeline_schema,
            opt_sp=c,
            round_scorer=round_scorer,
        )
        for c in population
    }

    best_composite = current_composite
    best_fitness = current_fitness
    best_ps: OptSearchPoint = current_best["prompt_fields"]
    best_results = current_best["results"]
    best_label = current_best["label"]
    best_evaluators: dict[str, float] = current_best.get("evaluators") or {}
    winner_idx: int | None = None

    for idx, individual in enumerate(population):
        ind_scores = individual_scores[individual.lineage.id]
        ind_fitness = ind_scores["accuracy"]
        if ind_fitness > best_fitness:
            best_fitness = ind_fitness
            best_composite = ind_scores["composite"]
            best_ps = individual
            best_results = all_candidate_results[individual.lineage.id]
            best_label = individual.lineage.changes_description or individual.lineage.id[:12]
            best_evaluators = dict(ind_scores.get("evaluators") or {})
            winner_idx = idx

    return {
        "label": best_label,
        "prompt_fields": best_ps,
        "accuracy": best_fitness,
        "composite": best_composite,
        "hits": sum(1 for r in best_results if r.get("hit")),
        "total": len(best_results),
        "results": best_results,
        "candidates_scored": len(population),
        "improved": best_fitness > current_fitness + improvement_threshold,
        "winner_idx": winner_idx,
        "evaluators": best_evaluators,
    }


class L1ScoringResult(BaseModel):
    """Structured return value from l1_score()."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    label: str
    winner_osp: OptSearchPoint
    winner_prompt_fields: dict[str, Any]
    winner_pipeline_params: dict[str, Any] | None
    winner_accuracy: float
    winner_composite: float
    hits: int
    total: int
    improved: bool
    candidates_scored: int
    candidate_scores: list[dict[str, Any]]
    winner_results: list[QueryResult]
    all_candidate_results: dict[str, list[QueryResult]] = Field(default_factory=dict)
    escalation_signal: EscalationSignal | None = None
    degraded_queries: int = 0
    winner_evaluators: dict[str, float] = Field(default_factory=dict)
    decisions: list[dict] = Field(default_factory=list)
    l1_critique_text: str = ""
    thinking_styles: list[str] = Field(default_factory=list)


async def l1_score(
    cycle: Cycle,
    candidates: list[dict],
    dataset: list,
    current_best: dict[str, Any],
    *,
    pipeline_params: dict | None = None,
    improvement_threshold: float = 0.01,
    callbacks: RunListener,
    degradation_checks: list | None = None,
    elimination_n_min: int = 4,
    elimination_alpha: float = 0.2,
    round_num: int = 0,
) -> L1ScoringResult:
    """Evaluate candidates and select the round winner."""
    session = cycle.session
    cb = dict(current_best)
    if isinstance(cb.get("prompt_fields"), dict):
        cb["prompt_fields"] = OptSearchPoint.from_prompt_fields(cb["prompt_fields"])

    osp_population, merged_pp, overrides = parse_population(
        candidates,
        pipeline_params,
        session.pipeline_schema,
    )
    decisions: list[dict] = []
    all_candidate_results, candidate_scores, escalation_signal = await score_population(
        cycle,
        osp_population,
        merged_pp,
        overrides,
        dataset,
        degradation_checks=degradation_checks,
        callbacks=callbacks,
        elimination_n_min=elimination_n_min,
        elimination_alpha=elimination_alpha,
        round_num=round_num,
        decisions=decisions,
    )

    aborted_ids = {
        cs["candidate_id"]
        for cs in candidate_scores
        if cs.get("escalation_aborted") and not cs.get("elimination_stopped")
    }
    evaluated_population = [
        ind
        for ind in osp_population
        if ind.lineage.id in all_candidate_results and ind.lineage.id not in aborted_ids
    ]
    winner_entry = select_round_winner(
        evaluated_population,
        all_candidate_results,
        cb,
        improvement_threshold,
        pipeline_schema=session.pipeline_schema,
        round_scorer=session.round_scorer,
    )

    from promptpotter.application.campaign.decisions import record_decision

    w_idx = winner_entry["winner_idx"]
    winner_id = (
        evaluated_population[w_idx].lineage.id if w_idx is not None and evaluated_population else ""
    )
    record_decision(
        decisions,
        "round_winner",
        {
            "candidate_ids": [ind.lineage.id for ind in evaluated_population],
            "round_num": round_num,
        },
        winner_id,
        data={"current_best_accuracy_at_record": cb["accuracy"]},
    )

    winner_pp = merged_pp[w_idx] if w_idx is not None else pipeline_params
    winner_osp: OptSearchPoint = winner_entry["prompt_fields"]
    winner_results = winner_entry["results"]
    winner_dict = {
        **winner_osp.prompt_field_dict(),
        "lineage": winner_osp.lineage.model_dump(),
    }
    return L1ScoringResult(
        label=winner_entry["label"],
        winner_osp=winner_osp,
        winner_prompt_fields=winner_dict,
        winner_pipeline_params=winner_pp,
        winner_accuracy=winner_entry["accuracy"],
        winner_composite=winner_entry["composite"],
        hits=winner_entry["hits"],
        total=winner_entry["total"],
        improved=winner_entry["improved"],
        candidates_scored=winner_entry["candidates_scored"],
        candidate_scores=candidate_scores,
        winner_results=winner_results,
        all_candidate_results=all_candidate_results,
        escalation_signal=escalation_signal,
        degraded_queries=count_degraded_queries(winner_results),
        winner_evaluators=winner_entry["evaluators"],
        decisions=decisions,
    )
