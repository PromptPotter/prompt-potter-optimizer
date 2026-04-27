"""L1 scoring orchestrator: parse → score → select winner → record decision."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from promptpotter.application.optimization.decisions import record_decision
from promptpotter.application.optimization.nodes.l1.measure import (
    parse_population,
    score_population,
)
from promptpotter.application.optimization.results import CandidateProposal, RoundBaseline
from promptpotter.application.scoring.metrics import (
    _compute_accuracy,
    compute_composite_score,
    count_degraded_queries,
)
from promptpotter.domain.analysis import EscalationSignal
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.scoring import QueryResult

if TYPE_CHECKING:
    from promptpotter.application.campaign.runner import RunListener
    from promptpotter.application.optimization.cycle import Cycle


__all__ = ["L1ScoringResult", "l1_score"]


class L1ScoringResult(BaseModel):
    """Structured return value from l1_score() — frozen."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

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
    deprecated: int = 0
    winner_evaluators: dict[str, float] = Field(default_factory=dict)
    decisions: list[dict] = Field(default_factory=list)


async def l1_score(
    cycle: Cycle,
    candidates: list[CandidateProposal],
    dataset: list,
    baseline: RoundBaseline,
    *,
    pipeline_params: dict | None = None,
    improvement_threshold: float = 0.01,
    callbacks: RunListener,
    degradation_checks: list | None = None,
    elimination_n_min: int = 4,
    elimination_alpha: float = 0.2,
    round_num: int = 0,
) -> L1ScoringResult:
    """Evaluate candidates and select the round winner (compares fitness, not composite)."""
    session = cycle.session
    schema = session.pipeline_schema
    assert schema is not None, "l1_score requires pipeline_schema"

    osp_population, merged_pp = parse_population(candidates, pipeline_params, schema)
    decisions: list[dict] = []
    all_candidate_results, candidate_scores, escalation_signal = await score_population(
        cycle,
        osp_population,
        merged_pp,
        candidates,
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
    evaluated = [
        ind
        for ind in osp_population
        if ind.lineage.id in all_candidate_results and ind.lineage.id not in aborted_ids
    ]
    best_acc = baseline.accuracy
    best_comp = baseline.composite
    best_osp: OptSearchPoint = baseline.osp
    best_results: list = list(baseline.results)
    best_label = baseline.label
    best_evals: dict[str, float] = dict(baseline.evaluators)
    winner_idx: int | None = None
    for idx, ind in enumerate(evaluated):
        s = compute_composite_score(
            all_candidate_results[ind.lineage.id],
            schema,
            opt_sp=ind,
            round_scorer=session.round_scorer,
        )
        if s["accuracy"] > best_acc:
            best_acc = s["accuracy"]
            best_comp = s["composite"]
            best_osp = ind
            best_results = list(all_candidate_results[ind.lineage.id])
            best_label = ind.lineage.changes_description or ind.lineage.id[:12]
            best_evals = dict(s.get("evaluators") or {})
            winner_idx = idx

    record_decision(
        decisions,
        "round_winner",
        {
            "candidate_ids": [ind.lineage.id for ind in evaluated],
            "round_num": round_num,
        },
        evaluated[winner_idx].lineage.id if winner_idx is not None and evaluated else "",
        data={"current_best_accuracy_at_record": baseline.accuracy},
    )

    base = _compute_accuracy(best_results)
    return L1ScoringResult(
        label=best_label,
        winner_osp=best_osp,
        winner_prompt_fields={
            **best_osp.prompt_field_dict(),
            "lineage": best_osp.lineage.model_dump(),
        },
        winner_pipeline_params=merged_pp[winner_idx] if winner_idx is not None else pipeline_params,
        winner_accuracy=best_acc,
        winner_composite=best_comp,
        hits=base["hits"],
        total=base["total"],
        improved=best_acc > baseline.accuracy + improvement_threshold,
        candidates_scored=len(evaluated),
        candidate_scores=candidate_scores,
        winner_results=best_results,
        all_candidate_results=all_candidate_results,
        escalation_signal=escalation_signal,
        degraded_queries=count_degraded_queries(best_results),
        deprecated=base["deprecated"],
        winner_evaluators=best_evals,
        decisions=decisions,
    )
