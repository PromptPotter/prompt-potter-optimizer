"""Campaign types — outcome models, artifact manifest, mutable loop state.

Consolidates:
- StopReason, RoundResult, RunResult (formerly results.py)
- CAMPAIGN_SESSION_ARTIFACTS (formerly artifacts.py)
- EscalationCounters, LoopState, RunBackendSession
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from promptpotter.models.opt_search_point import OptSearchPoint
from promptpotter.models.search_point import JobSearchPoint

if TYPE_CHECKING:
    from promptpotter.models.analysis import FailureAnalysis
    from promptpotter.models.eval_context import EvalContext
    from promptpotter.services.campaign.escalation import DegradationCheck
    from promptpotter.services.campaign.persistence_emitter import CampaignPersistenceEmitter

__all__ = [
    "CAMPAIGN_SESSION_ARTIFACTS",
    "EscalationCounters",
    "LoopState",
    "RoundResult",
    "RunBackendSession",
    "RunResult",
    "StopReason",
]

# ---------------------------------------------------------------------------
# Artifact manifest
# ---------------------------------------------------------------------------

CAMPAIGN_SESSION_ARTIFACTS = {
    "campaign_state.json",
    "campaign_output.log",
    "campaign_log.md",
    "session.json",
}

# ---------------------------------------------------------------------------
# Outcome models
# ---------------------------------------------------------------------------


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

# ---------------------------------------------------------------------------
# Mutable loop state
# ---------------------------------------------------------------------------


@dataclass
class EscalationCounters:
    """L2/L3 escalation tracking — stall counts, round counters, entry baselines.

    Extracted from LoopState to make the three-layer escalation semantics
    explicit.  Accessed primarily by ``escalation.py`` and serialized at
    checkpoint time by ``optimization_loop.py``.
    """

    l2_stall_count: int = 0
    l3_stall_count: int = 0
    l2_round: int = 0
    l3_round: int = 0
    best_accuracy_at_l2_entry: float = 0.0
    best_accuracy_at_l3_entry: float = 0.0
    best_composite_at_l2_entry: float = 0.0
    best_composite_at_l3_entry: float = 0.0


@dataclass
class LoopState:
    """Mutable state threaded through the feedback cycle round loop.

    Optimizer-level state (critique, thinking_styles, task_context,
    escalation_journal, warning_inventory) lives on ``opt_sp``
    — a mutable ``OptSearchPoint`` that is serialized at checkpoint
    time and hydrated on resume.

    **Mutation contract:** All mutations to ``opt_sp`` must occur within
    the sequential round loop body (round_execution, critique, escalation).
    The optimization loop is single-threaded — no concurrent access.
    State transitions should use the named methods below.
    """

    rounds: list[RoundResult] = field(default_factory=list)
    current_sp: JobSearchPoint | None = None
    current_accuracy: float = 0.0
    current_composite: float = 0.0
    current_results: list[dict] = field(default_factory=list)
    best_accuracy: float = 0.0
    best_composite: float = 0.0
    best_round: int = -1
    best_sp: JobSearchPoint | None = None
    stall_count: int = 0
    eval_ctx: EvalContext | None = None

    # Optimizer state — single source of truth for all meta-level fields
    opt_sp: OptSearchPoint = field(default_factory=OptSearchPoint)

    # Probe round flag (set by L2 action="probe", reset after probe round)
    probe_next_round: bool = False

    # Failure analysis from previous round (ephemeral, not checkpointed)
    failure_analysis: FailureAnalysis | None = None

    # SearchMemory instance (M8 Wave 3c — not checkpointed, rebuilt from disk)
    search_memory: Any = None

    # L2/L3 escalation counters
    escalation: EscalationCounters = field(default_factory=EscalationCounters)

    # Checkpoint schema version — incremented when LoopState/OptSearchPoint
    # fields change, so resume can detect stale checkpoints.
    state_version: int = 1

    # -- State transition methods ------------------------------------------

    def record_round_outcome(self, improved: bool) -> None:
        """Update stall count after a round completes."""
        self.stall_count = 0 if improved else self.stall_count + 1

    def reset_stall_count(self) -> None:
        """Reset stall count (e.g. after L2/L3 changes prompt)."""
        self.stall_count = 0

    def enter_probe_mode(self) -> None:
        """Flag next round as a probe round (L2 action="probe")."""
        self.probe_next_round = True

    def exit_probe_mode(self) -> None:
        """Clear probe flag after a probe round completes."""
        self.probe_next_round = False

    def update_current(
        self,
        rr: RoundResult,
        search_point: JobSearchPoint,
        round_num: int,
    ) -> None:
        """Apply a round result to current/best tracking.

        Call after ``update_round_state()`` syncs prompt fields.
        """
        self.current_sp = search_point
        self.current_accuracy = rr.accuracy
        self.current_composite = rr.composite
        self.current_results = list(rr.results)
        if self.current_composite > self.best_composite:
            self.best_composite = self.current_composite
            self.best_accuracy = self.current_accuracy
            self.best_round = round_num
            self.best_sp = self.current_sp


@dataclass
class RunBackendSession:
    """Return type for ``_init_cycle_state()`` — replaces a 7-tuple."""

    state: LoopState
    campaign_store: Any = None
    cycle_id: str | None = None
    obs_campaign_id: str = ""
    round_dataset: list[dict[str, Any]] = field(default_factory=list)
    escalation_checks: list[DegradationCheck] = field(default_factory=list)
    resumed_from_round: int = 0
    search_memory: Any = None
    persistence_emitter: CampaignPersistenceEmitter | None = None
