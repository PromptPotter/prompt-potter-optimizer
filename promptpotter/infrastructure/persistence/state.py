"""Campaign types — outcome models, callbacks, mutable loop state.

PhaseEvent, StopReason, StopLoop, RoundResult, RunResult, RunCallbacks,
EscalationCounters, LoopState.
"""

from __future__ import annotations

import enum
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, TypeAlias

from pydantic import BaseModel, Field

from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.search_point import JobSearchPoint

if TYPE_CHECKING:
    from promptpotter.application.optimization.nodes.escalation import DegradationCheck
    from promptpotter.domain.analysis import FailureAnalysis
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.scoring import ScoringEnv
    from promptpotter.domain.search_point import TaskDecomposition
    from promptpotter.infrastructure.store.campaign_store import CampaignStore

logger = logging.getLogger(__name__)

__all__ = [
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
    "StopLoop",
    "StopReason",
    "emit_phase",
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


class StopLoop(Exception):  # noqa: N818 — control-flow signal, not an error
    """Internal signal to exit the optimization round loop with a reason.

    Raised by helpers that decide the loop must stop — caught exactly once
    at the top of ``_run_round_loop``.  Lets helpers stay ``-> None`` and
    eliminates the ``Optional[StopReason]`` bubble-up pattern.
    """

    def __init__(self, reason: StopReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


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


# ---------------------------------------------------------------------------
# Escalation counters — L2/L3 stall tracking
# ---------------------------------------------------------------------------


@dataclass
class EscalationCounters:
    """L2/L3 escalation tracking — stall counts, round counters, entry baselines."""

    l2_stall_count: int = 0
    l3_stall_count: int = 0
    l2_round: int = 0
    l3_round: int = 0
    best_accuracy_at_l2_entry: float = 0.0
    best_accuracy_at_l3_entry: float = 0.0
    best_composite_at_l2_entry: float = 0.0
    best_composite_at_l3_entry: float = 0.0

    def record_l2_outcome(self, best_composite: float) -> bool:
        improved = best_composite > self.best_composite_at_l2_entry
        self.l2_stall_count = 0 if improved or self.l2_round == 0 else self.l2_stall_count + 1
        return not improved and self.l2_round > 0

    def record_l3_outcome(self, best_composite: float) -> bool:
        improved = best_composite > self.best_composite_at_l3_entry
        self.l3_stall_count = 0 if improved or self.l3_round == 0 else self.l3_stall_count + 1
        return not improved and self.l3_round > 0

    def reset_for_l3(self, best_accuracy: float, best_composite: float) -> None:
        self.l2_stall_count = 0
        self.l2_round = 0
        self.best_accuracy_at_l2_entry = best_accuracy
        self.best_composite_at_l2_entry = best_composite

    def record_l2_entry(self, best_accuracy: float, best_composite: float) -> None:
        self.l2_round += 1
        self.best_accuracy_at_l2_entry = best_accuracy
        self.best_composite_at_l2_entry = best_composite

    def record_l3_entry(self, best_accuracy: float, best_composite: float) -> None:
        self.l3_round += 1
        self.best_accuracy_at_l3_entry = best_accuracy
        self.best_composite_at_l3_entry = best_composite

    def to_checkpoint_dict(self) -> dict[str, int | float]:
        return {
            "l2_round": self.l2_round,
            "l3_round": self.l3_round,
            "l2_stall_count": self.l2_stall_count,
            "l3_stall_count": self.l3_stall_count,
        }

    @classmethod
    def from_checkpoint_dict(cls, d: dict) -> EscalationCounters:
        return cls(
            l2_round=d.get("l2_round", 0),
            l3_round=d.get("l3_round", 0),
            l2_stall_count=d.get("l2_stall_count", 0),
            l3_stall_count=d.get("l3_stall_count", 0),
        )


# ---------------------------------------------------------------------------
# Loop state
# ---------------------------------------------------------------------------


@dataclass
class LoopState:
    """Mutable state threaded through the feedback cycle round loop.

    Optimizer-level state (critique, thinking_styles, task_context,
    escalation_journal, warning_inventory) lives on ``opt_sp``.
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

    opt_sp: OptSearchPoint = field(default_factory=OptSearchPoint)

    probe_next_round: bool = False
    failure_analysis: FailureAnalysis | None = None
    search_memory: Any = None
    escalation: EscalationCounters = field(default_factory=EscalationCounters)

    # -- Infrastructure (populated by init, threaded through loop) --
    campaign_store: CampaignStore | None = None
    cycle_id: str | None = None
    obs_campaign_id: str = ""
    scoring_dataset: list[dict] = field(default_factory=list)
    degradation_checks: list[DegradationCheck] = field(default_factory=list)
    resumed_from_round: int = 0

    state_version: int = 1

    # -- Construction / restore ------------------------------------------------

    @classmethod
    def from_baseline(
        cls,
        baseline_osp: OptSearchPoint,
        baseline_accuracy: float,
        *,
        task_context: TaskDecomposition,
        schema: PipelineSchema | None,
        baseline_results: list[dict] | None = None,
    ) -> LoopState:
        """Construct a fresh LoopState from an evaluated baseline.

        Composite is derived from ``baseline_results`` when a schema is
        present; otherwise falls back to ``baseline_accuracy``.
        """
        from promptpotter.application.scoring.metrics import compute_composite_score

        composite = (
            compute_composite_score(baseline_results, schema)["composite"]  # type: ignore[arg-type]
            if baseline_results and schema is not None
            else baseline_accuracy
        )
        opt_sp = baseline_osp.model_copy(
            update={
                "task_context": task_context,
                "optimizer_params": dict(baseline_osp.optimizer_params),
            }
        )
        sp = opt_sp.to_job_search_point(
            base_pipeline_params=schema.to_pipeline_params() if schema else None,
            schema=schema,
        )
        return cls(
            current_sp=sp,
            current_accuracy=baseline_accuracy,
            current_composite=composite,
            current_results=baseline_results or [],
            best_accuracy=baseline_accuracy,
            best_composite=composite,
            best_sp=sp,
            opt_sp=opt_sp,
        )

    def build_trial_entry(self, round_result: RoundResult, round_num: int) -> dict[str, Any]:
        """Build the trial checkpoint dict for ``campaign_store.add_trial``."""
        return {
            "trial_id": f"round_{round_num}",
            "round": round_num,
            "label": round_result.label,
            "accuracy": round_result.accuracy,
            "composite": round_result.composite,
            "hits": round_result.hits,
            "total": round_result.total,
            "improved": round_result.improved,
            "prompt_fields": round_result.prompt_fields,
            "results": round_result.results,
            "candidates_scored": round_result.candidates_scored,
            "candidate_scores": list(round_result.candidate_scores),
            "stall_count": self.stall_count,
            **self.escalation.to_checkpoint_dict(),
            "opt_search_point": self.opt_sp.model_dump(),
        }

    def restore_from_trial(self, trial: dict[str, Any]) -> None:
        """Restore optimizer state from a campaign checkpoint dict (in-place)."""
        self.opt_sp = OptSearchPoint(**trial["opt_search_point"])
        self.escalation = EscalationCounters.from_checkpoint_dict(trial)
        self.stall_count = trial["stall_count"]

    # -- State transition methods ----------------------------------------------

    def record_round_outcome(self, improved: bool) -> None:
        """Update stall count after a round completes."""
        self.stall_count = 0 if improved else self.stall_count + 1

    def reset_stall_count(self) -> None:
        """Reset stall count (e.g. after L2/L3 changes prompt)."""
        self.stall_count = 0

    def update_current(
        self,
        rr: RoundResult,
        search_point: JobSearchPoint,
        round_num: int,
    ) -> None:
        """Apply a round result to current/best tracking."""
        self.current_sp = search_point
        self.current_accuracy = rr.accuracy
        self.current_composite = rr.composite
        self.current_results = list(rr.results)
        if self.current_composite > self.best_composite:
            self.best_composite = self.current_composite
            self.best_accuracy = self.current_accuracy
            self.best_round = round_num
            self.best_sp = self.current_sp

    def eval_data_for_round(
        self,
        full_dataset: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[DegradationCheck] | None]:
        """Return (eval_data, degradation_checks) for the next round.

        Probe rounds score only warned queries and skip degradation checks;
        normal rounds use the scoring dataset and the configured checks.
        """
        if not self.probe_next_round:
            return self.scoring_dataset, self.degradation_checks
        warned = {q for q, e in self.opt_sp.warning_inventory.items() if e.get("warnings")}
        return [d for d in full_dataset if d.get("query") in warned], None


# ---------------------------------------------------------------------------
# Phase events + callbacks
# ---------------------------------------------------------------------------


class PhaseEvent(BaseModel):
    """Emitted at phase boundaries during the feedback cycle."""

    model_config = {"frozen": True}

    phase: str
    event: str
    round: int | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


OnRoundComplete: TypeAlias = Callable[["RoundResult", int], None]
OnCandidateScored: TypeAlias = Callable[[int, int, dict], None]
OnSampleScored: TypeAlias = Callable[[int, int, int, int, dict], None]
OnPhase: TypeAlias = Callable[[PhaseEvent], None]
OnCheckpoint: TypeAlias = Callable[[str], str | None]


class RunCallbacks:
    """Multicast progress callbacks for the feedback cycle.

    Each channel is a list of listeners; ``merge()`` concatenates two
    callback bundles.  Dispatch methods are always safe to call — no
    None guards needed at callsites.  ``on_checkpoint`` short-circuits
    on the first non-None result.
    """

    def __init__(
        self,
        *,
        on_round_complete: OnRoundComplete | None = None,
        on_candidate_scored: OnCandidateScored | None = None,
        on_sample_scored: OnSampleScored | None = None,
        on_phase: OnPhase | None = None,
        on_checkpoint: OnCheckpoint | None = None,
    ) -> None:
        def _wrap(fn: Any) -> list:
            return [fn] if fn else []

        self._round = _wrap(on_round_complete)
        self._candidate = _wrap(on_candidate_scored)
        self._sample = _wrap(on_sample_scored)
        self._phase = _wrap(on_phase)
        self._checkpoint = _wrap(on_checkpoint)

    def merge(self, other: RunCallbacks) -> RunCallbacks:
        """Return a new RunCallbacks with self's listeners first, then other's."""
        merged = RunCallbacks()
        merged._round = self._round + other._round
        merged._candidate = self._candidate + other._candidate
        merged._sample = self._sample + other._sample
        merged._phase = self._phase + other._phase
        merged._checkpoint = self._checkpoint + other._checkpoint
        return merged

    def on_round_complete(self, rr: RoundResult, stall_count: int) -> None:
        for fn in self._round:
            fn(rr, stall_count)

    def on_candidate_scored(self, idx: int, total: int, scores: dict) -> None:
        for fn in self._candidate:
            fn(idx, total, scores)

    def on_sample_scored(self, ci: int, ct: int, qi: int, qt: int, result: dict) -> None:
        for fn in self._sample:
            fn(ci, ct, qi, qt, result)

    def on_phase(self, event: PhaseEvent) -> None:
        for fn in self._phase:
            fn(event)

    def on_checkpoint(self, name: str) -> str | None:
        for fn in self._checkpoint:
            result = fn(name)
            if result:
                return result
        return None


def emit_phase(
    on_phase: Callable[[PhaseEvent], None] | None,
    phase: str,
    event: str,
    *,
    round: int | None = None,
    **data: Any,
) -> None:
    """Construct a PhaseEvent and call the callback if set.

    Accepts a plain callable (e.g. ``cb.on_phase`` — a bound dispatcher
    method from ``RunCallbacks``) or ``None``.
    """
    if on_phase is None:
        return
    on_phase(PhaseEvent(phase=phase, event=event, round=round, data=data))
