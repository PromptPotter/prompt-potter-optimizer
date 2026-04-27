"""Round and run outcome models — pure pydantic types, no I/O."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from promptpotter.domain.analysis import EscalationSignal
from promptpotter.domain.opt_search_point import OptSearchPoint

__all__ = ["CandidateProposal", "RoundBaseline", "RoundResult", "RunResult"]


class CandidateProposal(BaseModel):
    """One LLM-proposed individual: an OptSearchPoint plus its node-param overrides.

    Persisted between generate and score so resume can replay a round without
    re-querying the LLM. The ``osp`` carries the prompt-field mutations and
    lineage; ``node_overrides`` carries the LLM-proposed changes to non-prompt
    pipeline params (deep-merged into the base ``pipeline_params`` at score time).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    osp: OptSearchPoint
    node_overrides: dict[str, dict] = Field(default_factory=dict)


class RoundBaseline(BaseModel):
    """The challenger candidates compete against — built once per round.

    Carries the OSP directly (no dict→OSP roundtrip). On probe rounds the
    accuracy/composite/results reflect the baseline's score *over the probe
    subset*, not the full prior dataset.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    accuracy: float
    composite: float
    osp: OptSearchPoint
    results: list[dict] = Field(default_factory=list)
    label: str
    evaluators: dict[str, float] = Field(default_factory=dict)


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
    # Count of samples discarded as deprecated (fatal warnings) on the
    # round-winner's evaluation; excluded from hits/total/accuracy and
    # surfaced in round summary for operator transparency.
    deprecated: int = 0
    escalation_signal: EscalationSignal | None = None
    evaluators: dict[str, float] = Field(default_factory=dict)
    # Scoring-set evolution events emitted during this round (only populated
    # when ``scoring_set.enabled``; empty otherwise). Persisted to the
    # trial JSON so post-hoc renderers can walk the trial list and
    # reconstruct the full event log.
    scoring_set_events: list[dict] = Field(default_factory=list)


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
