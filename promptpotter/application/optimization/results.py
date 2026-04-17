"""Round and run outcome models — pure pydantic types, no I/O."""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = ["RoundResult", "RunResult"]


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
    candidates_scored: int
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
    session_id: str | None = None
    resumed_from_round: int = 0
