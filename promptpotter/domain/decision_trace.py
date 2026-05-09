"""DecisionTrace — per-candidate elimination/promotion record for postmortem critique.

``L1_CRITIQUE`` reasons about the *story* of each candidate — when it
was eliminated, what its leaderboard position looked like at decision
time, which samples it had hit so far — not just round-end aggregate
scores. PoBB writes one ``DecisionTrace`` per promote/eliminate/complete
decision; the dispatch hub renders the slice via the
``decision_trace_summary`` signal into ``L1_CRITIQUE``'s prompt body.

The frozen model is decoupled from :class:`Decision` /
:class:`DecisionKind` (``domain/run_records.py``) on purpose: those
records gate replay-vs-archival semantics for a small fixed set of
divergence-relevant decisions, while :class:`DecisionTrace` is
narrative — produced for every elimination/promotion regardless of
gating, consumed by prompts, optional in the persistence schema.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DecisionTrace(BaseModel):
    """One per-candidate decision context, captured at the moment PoBB
    decided to eliminate, promote, or complete the candidate's scoring.

    Aggregates the evidence ``L1_CRITIQUE`` needs to ground its
    post-round narrative:

    * ``decision_kind`` — what happened (eliminate / promote / complete).
    * ``candidate_id`` — stable handle used by the dispatch hub when
      rendering decision summaries.
    * ``at_sample_index`` — 0-based count of samples scored before the
      decision fired (i.e. how much evidence PoBB had).
    * ``p_best_at_decision`` — posterior P(best) at decision time, or
      ``None`` for non-PoBB-driven decisions.
    * ``leaderboard_at_decision`` — top-K (candidate_id, score) tuples
      visible when the decision fired; lets a prompt say "candidate X
      was eliminated while Y led at score 0.78."
    * ``sample_outcomes_so_far`` — boolean hit/miss trail in score order;
      lets a prompt see which sample patterns broke the candidate.
    * ``target_axis`` — the axis the L1_GENERATE LLM said this candidate
      mutated; lets the critique tie outcomes to mutation strategy.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision_kind: Literal["eliminate", "promote", "complete"]
    candidate_id: str
    at_sample_index: int = Field(ge=0)
    p_best_at_decision: float | None = None
    leaderboard_at_decision: list[tuple[str, float]] = Field(default_factory=list)
    sample_outcomes_so_far: list[bool] = Field(default_factory=list)
    target_axis: str = ""


__all__ = ["DecisionTrace"]
