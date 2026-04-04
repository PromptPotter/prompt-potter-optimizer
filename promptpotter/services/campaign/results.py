"""Outcome types — RoundResult, RunResult, and StopReason."""
from __future__ import annotations

import enum

from pydantic import BaseModel, Field

__all__ = ["RoundResult", "RunResult", "StopReason"]


class StopReason(enum.StrEnum):
    """Feedback cycle termination reasons."""

    PATIENCE = "patience_exhausted"
    PERFECT = "perfect_score"
    MAX_ROUNDS = "max_rounds"
    INTERRUPTED = "interrupted"
    ABORT = "escalation_abort"
    L2_PATIENCE = "l2_patience_exhausted"
    L3_PATIENCE = "l3_patience_exhausted"
    HARD_CAP = "hard_cap_reached"
    PAUSED_FOR_REVIEW = "paused_for_review"
    USER_PAUSED = "user_paused"
    USER_STOPPED = "user_stopped"


class RoundResult(BaseModel):
    """Result of a single feedback cycle round."""

    round: int
    label: str
    accuracy: float
    composite: float = 0.0
    hits: int
    total: int
    improved: bool
    prompt_fields: dict
    pipeline_params: dict | None = None
    results: list[dict] = Field(default_factory=list)
    candidates_evaluated: int
    candidate_scores: list[dict] = Field(default_factory=list)
    degraded_queries: int = 0
    escalation_signal: dict | None = None


class RunResult(BaseModel):
    """Final result of the feedback cycling process."""

    rounds: list[RoundResult]
    n_rounds: int
    best_accuracy: float
    best_round: int
    baseline_accuracy: float
    winner_prompt_fields: dict
    winner_pipeline_params: dict | None = None
    stop_reason: str
    started_at: str
    finished_at: str
    langfuse_trace_id: str | None = None
    cycle_id: str | None = None
    resumed_from_round: int = 0
