"""Round and run outcome models — pure pydantic types, no I/O."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from promptpotter.domain.analysis import EscalationSignal

__all__ = ["RoundResult", "RunResult"]


class RoundResult(BaseModel):
    """Result of a single feedback cycle round."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

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
    # Per-candidate scored results — persisted so resume can rescore
    # them under a changed scorer and replay decisions without needing
    # to re-evaluate the pipeline. Keyed by candidate id.
    all_candidate_results: dict[str, list[dict]] = Field(default_factory=dict)
    candidates_scored: int
    candidate_scores: list[dict] = Field(default_factory=list)
    # Decision records produced this round (round_winner, elimination_cut,
    # escalate_l2, …). Consumed by the divergence replay walker in
    # ``application/campaign/decisions.py``.
    decisions: list[dict] = Field(default_factory=list)
    degraded_queries: int = 0
    escalation_signal: EscalationSignal | None = None
    evaluators: dict[str, float] = Field(default_factory=dict)


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
