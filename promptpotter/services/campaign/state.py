"""Campaign types — outcome models, callbacks, mutable loop state.

PhaseEvent, StopReason, RoundResult, RunResult, RunCallbacks,
EscalationCounters, LoopState, and the session artifact manifest.
"""

from __future__ import annotations

import enum
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, TypeAlias

from pydantic import BaseModel, Field

from promptpotter.models.opt_search_point import OptSearchPoint
from promptpotter.models.search_point import JobSearchPoint

if TYPE_CHECKING:
    from promptpotter.models.analysis import FailureAnalysis
    from promptpotter.models.scoring_env import ScoringEnv
    from promptpotter.services.campaign.nodes.escalation import DegradationCheck
    from promptpotter.services.campaign.persistence_emitter import CampaignPersistenceEmitter
    from promptpotter.services.store.campaign_store import CampaignStore

logger = logging.getLogger(__name__)

__all__ = [
    "CAMPAIGN_SESSION_ARTIFACTS",
    "CampaignPhase",
    "EscalationCounters",
    "LoopState",
    "OnCandidateScored",
    "OnCheckpoint",
    "OnPhase",
    "OnRoundComplete",
    "OnSampleScored",
    "PhaseEvent",
    "RoundResult",
    "RunCallbacks",
    "RunResult",
    "StopReason",
    "chain_callbacks",
    "emit_phase",
    "get_obs_trace",
]


class CampaignPhase(enum.StrEnum):
    """Feedback cycle phase names used in PhaseEvent and persistence."""

    INIT = "init"
    L1_GENERATE = "l1_generate"
    L1_SCORE = "l1_score"
    REFINE_STRATEGY = "refine_strategy"
    MODIFY_PLAN = "modify_plan"
    ESCALATION = "escalation"
    BACKEND_WARNING = "backend_warning"


CAMPAIGN_SESSION_ARTIFACTS = {
    "campaign_state.json",
    "campaign_output.log",
    "campaign_log.md",
    "session.json",
}


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
    resumed_from_round: int = 0


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

    def record_l2_outcome(self, best_composite: float) -> bool:
        """Update L2 stall count. Returns True if L2 is stalled."""
        improved = best_composite > self.best_composite_at_l2_entry
        self.l2_stall_count = 0 if improved or self.l2_round == 0 else self.l2_stall_count + 1
        return not improved and self.l2_round > 0

    def record_l3_outcome(self, best_composite: float) -> bool:
        """Update L3 stall count. Returns True if L3 is stalled."""
        improved = best_composite > self.best_composite_at_l3_entry
        self.l3_stall_count = 0 if improved or self.l3_round == 0 else self.l3_stall_count + 1
        return not improved and self.l3_round > 0

    def reset_for_l3(self, best_accuracy: float, best_composite: float) -> None:
        """Reset L2 counters when L3 takes over."""
        self.l2_stall_count = 0
        self.l2_round = 0
        self.best_accuracy_at_l2_entry = best_accuracy
        self.best_composite_at_l2_entry = best_composite

    def record_l2_entry(self, best_accuracy: float, best_composite: float) -> None:
        """Record baseline at L2 transition."""
        self.l2_round += 1
        self.best_accuracy_at_l2_entry = best_accuracy
        self.best_composite_at_l2_entry = best_composite

    def record_l3_entry(self, best_accuracy: float, best_composite: float) -> None:
        """Record baseline at L3 transition."""
        self.l3_round += 1
        self.best_accuracy_at_l3_entry = best_accuracy
        self.best_composite_at_l3_entry = best_composite

    def to_checkpoint_dict(self) -> dict[str, int | float]:
        """Serialize for checkpoint persistence."""
        return {
            "l2_round": self.l2_round,
            "l3_round": self.l3_round,
            "l2_stall_count": self.l2_stall_count,
            "l3_stall_count": self.l3_stall_count,
        }

    @classmethod
    def from_checkpoint_dict(cls, d: dict) -> EscalationCounters:
        """Deserialize from checkpoint."""
        return cls(
            l2_round=d.get("l2_round", 0),
            l3_round=d.get("l3_round", 0),
            l2_stall_count=d.get("l2_stall_count", 0),
            l3_stall_count=d.get("l3_stall_count", 0),
        )


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
    scoring_ctx: ScoringEnv | None = None

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

    # -- Infrastructure (populated by init_cycle_state, threaded through loop) --
    campaign_store: CampaignStore | None = None
    cycle_id: str | None = None
    obs_campaign_id: str = ""
    eval_dataset: list[dict] = field(default_factory=list)
    degradation_checks: list[DegradationCheck] = field(default_factory=list)
    resumed_from_round: int = 0
    persistence_emitter: CampaignPersistenceEmitter | None = None

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


class PhaseEvent(BaseModel):
    """Emitted at phase boundaries during the feedback cycle.

    Phases: init, l1_generate, l1_score, refine_strategy, modify_plan.
    Each phase emits an "enter" and "exit" event with phase-specific data.
    """

    model_config = {"frozen": True}

    phase: str
    event: str
    round: int | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
    )


# (round_result, round_number)
OnRoundComplete: TypeAlias = Callable[["RoundResult", int], None]
# (candidate_index, total_candidates, scores_dict)
OnCandidateScored: TypeAlias = Callable[[int, int, dict], None]
# (candidate_index, total_candidates, query_index, total_queries, result_dict)
OnSampleScored: TypeAlias = Callable[[int, int, int, int, dict], None]
# (phase_event)
OnPhase: TypeAlias = Callable[[PhaseEvent], None]
# (checkpoint_name) -> "pause" | "stop" | None
OnCheckpoint: TypeAlias = Callable[[str], str | None]


@dataclass
class RunCallbacks:
    """Optional progress callbacks for the feedback cycle."""

    on_round_complete: OnRoundComplete | None = None
    on_candidate_scored: OnCandidateScored | None = None
    on_sample_scored: OnSampleScored | None = None
    on_phase: OnPhase | None = None
    on_checkpoint: OnCheckpoint | None = None


def chain_callbacks(a: RunCallbacks, b: RunCallbacks) -> RunCallbacks:
    """Compose two RunCallbacks so both fire on every event.

    ``a`` fires first (typically persistence), ``b`` second (typically display).
    For ``on_checkpoint``, returns the first non-None result.
    """

    def _chain(fn_a: Callable | None, fn_b: Callable | None) -> Callable | None:
        if fn_a and fn_b:
            return lambda *args, **kw: (fn_a(*args, **kw), fn_b(*args, **kw))
        return fn_a or fn_b

    def _chain_checkpoint(
        fn_a: Callable | None,
        fn_b: Callable | None,
    ) -> Callable | None:
        if fn_a and fn_b:

            def _both(*args: object, **kw: object) -> str | None:
                r = fn_a(*args, **kw)
                if r is not None:
                    return r
                return fn_b(*args, **kw)

            return _both
        return fn_a or fn_b

    return RunCallbacks(
        on_round_complete=_chain(a.on_round_complete, b.on_round_complete),
        on_candidate_scored=_chain(a.on_candidate_scored, b.on_candidate_scored),
        on_sample_scored=_chain(a.on_sample_scored, b.on_sample_scored),
        on_phase=_chain(a.on_phase, b.on_phase),
        on_checkpoint=_chain_checkpoint(a.on_checkpoint, b.on_checkpoint),
    )


def emit_phase(
    on_phase: Callable[[PhaseEvent], None] | None,
    phase: str,
    event: str,
    *,
    round: int | None = None,
    **data: Any,
) -> None:
    """Construct a PhaseEvent and call the callback if set."""
    if on_phase is None:
        return
    pe = PhaseEvent(phase=phase, event=event, round=round, data=data)
    on_phase(pe)


def get_obs_trace(
    state: LoopState,
    obs_campaign_id: str,
) -> tuple[Any | None, str | None]:
    """Extract obs logger and trace_id from loop state."""
    obs = state.scoring_ctx.obs if state.scoring_ctx else None
    trace_id = obs.get_file_trace_id(obs_campaign_id) if obs else None
    return obs, trace_id
