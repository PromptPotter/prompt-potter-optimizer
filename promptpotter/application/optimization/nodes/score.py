"""L1 scoring orchestrator: parse → score → select winner → record decision."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from promptpotter.application.optimization.nodes.candidate_measurement import (
    parse_candidates,
    score_candidates,
)
from promptpotter.application.optimization.nodes.winner_selection import select_round_winner
from promptpotter.application.scoring.metrics import count_degraded_queries
from promptpotter.domain.analysis import EscalationSignal
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.scoring import QueryResult

if TYPE_CHECKING:
    from promptpotter.application.campaign.callbacks import RunListener
    from promptpotter.domain.scoring import ScoringEnv


__all__ = ["L1ScoringResult", "l1_score"]


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
    critique_text: str = ""
    thinking_styles: list[str] = Field(default_factory=list)


async def l1_score(
    candidates: list[dict],
    dataset: list,
    current_best: dict[str, Any],
    ctx: ScoringEnv,
    *,
    pipeline_params: dict | None = None,
    improvement_threshold: float = 0.01,
    callbacks: RunListener,
    degradation_checks: list | None = None,
    elimination_n_min: int = 4,
    elimination_alpha: float = 0.2,
    obs_campaign_id: str = "",
    round_num: int = 0,
) -> L1ScoringResult:
    """Evaluate candidates and select the round winner."""
    cb = dict(current_best)
    if isinstance(cb.get("prompt_fields"), dict):
        cb["prompt_fields"] = OptSearchPoint.from_prompt_fields(cb["prompt_fields"])

    osp_candidates, merged_pp, overrides = parse_candidates(
        candidates,
        pipeline_params,
        ctx.pipeline_schema,
    )
    decisions: list[dict] = []
    all_candidate_results, candidate_scores, escalation_signal = await score_candidates(
        osp_candidates,
        merged_pp,
        overrides,
        dataset,
        ctx,
        degradation_checks=degradation_checks,
        callbacks=callbacks,
        elimination_n_min=elimination_n_min,
        elimination_alpha=elimination_alpha,
        obs_campaign_id=obs_campaign_id,
        round_num=round_num,
        decisions=decisions,
    )

    aborted_ids = {
        cs["candidate_id"]
        for cs in candidate_scores
        if cs.get("escalation_aborted") and not cs.get("elimination_stopped")
    }
    evaluated_candidates = [
        c for c in osp_candidates if c.id in all_candidate_results and c.id not in aborted_ids
    ]
    winner_entry = select_round_winner(
        evaluated_candidates,
        all_candidate_results,
        cb,
        improvement_threshold,
        pipeline_schema=ctx.pipeline_schema,
        round_scorer=ctx.round_scorer,
    )

    from promptpotter.application.campaign.decisions import record_decision

    w_idx = winner_entry["winner_idx"]
    winner_id = evaluated_candidates[w_idx].id if w_idx is not None and evaluated_candidates else ""
    record_decision(
        decisions,
        "round_winner",
        {
            "candidate_ids": [c.id for c in evaluated_candidates],
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
        "id": winner_osp.id,
        "parent_id": winner_osp.parent_id,
        "changes_description": winner_osp.changes_description,
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
