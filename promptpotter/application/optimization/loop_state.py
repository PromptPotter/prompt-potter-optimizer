"""Optimization loop state — pure optimizer progress tracking.

Mutable state threaded through the feedback cycle round loop.  All
infrastructure handles (stores, scoring env, cycle id, dataset samples)
live on ``LoopEnv`` instead — this module stays layer-clean.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from promptpotter.application.optimization.results import RoundResult
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.search_point import JobSearchPoint

if TYPE_CHECKING:
    from promptpotter.domain.analysis import FailureAnalysis
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.search_point import TaskDecomposition

__all__ = ["EscalationCounters", "LayerCounter", "LoopState"]


@dataclass
class LayerCounter:
    """Per-layer escalation tracking (stall count, round count, entry baseline)."""

    round: int = 0
    stall_count: int = 0
    best_accuracy_at_entry: float = 0.0
    best_composite_at_entry: float = 0.0

    def record_outcome(self, best_composite: float) -> bool:
        """Update stall_count after a round. Returns True if stalled (not improved)."""
        improved = best_composite > self.best_composite_at_entry
        self.stall_count = 0 if improved or self.round == 0 else self.stall_count + 1
        return not improved and self.round > 0

    def record_entry(self, best_accuracy: float, best_composite: float) -> None:
        self.round += 1
        self.best_accuracy_at_entry = best_accuracy
        self.best_composite_at_entry = best_composite


@dataclass
class EscalationCounters:
    """L2/L3 escalation tracking — two nested ``LayerCounter`` instances."""

    l2: LayerCounter = field(default_factory=LayerCounter)
    l3: LayerCounter = field(default_factory=LayerCounter)

    def reset_for_l3(self, best_accuracy: float, best_composite: float) -> None:
        self.l2 = LayerCounter(
            best_accuracy_at_entry=best_accuracy,
            best_composite_at_entry=best_composite,
        )

    def to_checkpoint_dict(self) -> dict[str, int]:
        return {
            "l2_round": self.l2.round,
            "l3_round": self.l3.round,
            "l2_stall_count": self.l2.stall_count,
            "l3_stall_count": self.l3.stall_count,
        }

    @classmethod
    def from_checkpoint_dict(cls, d: dict) -> EscalationCounters:
        return cls(
            l2=LayerCounter(round=d["l2_round"], stall_count=d["l2_stall_count"]),
            l3=LayerCounter(round=d["l3_round"], stall_count=d["l3_stall_count"]),
        )


@dataclass
class LoopState:
    """Mutable state threaded through the feedback cycle round loop.

    Pure optimizer progress — infrastructure handles live on ``LoopEnv``.
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
    l1_stall_count: int = 0

    opt_sp: OptSearchPoint = field(default_factory=OptSearchPoint)

    probe_next_round: bool = False
    failure_analysis: FailureAnalysis | None = None
    search_memory: Any = None
    escalation: EscalationCounters = field(default_factory=EscalationCounters)

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
        round_scorer: Any = None,
    ) -> LoopState:
        """Construct a fresh LoopState from an evaluated baseline.

        Composite is derived from ``baseline_results`` when a schema is
        present; otherwise falls back to ``baseline_accuracy``. Pass
        ``round_scorer`` to use the dataset's configured per-round formula;
        otherwise the registry's default formula is used.
        """
        from promptpotter.application.scoring.metrics import compute_composite_score

        composite = (
            compute_composite_score(
                baseline_results,  # type: ignore[arg-type]
                schema,
                round_scorer=round_scorer,
            )["composite"]
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

    def restore_from_trial(self, trial: dict[str, Any]) -> None:
        """Restore optimizer state from a campaign checkpoint dict (in-place)."""
        self.opt_sp = OptSearchPoint(**trial["opt_search_point"])
        self.escalation = EscalationCounters.from_checkpoint_dict(trial)
        self.l1_stall_count = trial["l1_stall_count"]

    def adopt_transition(
        self,
        new_opt: OptSearchPoint,
        pipeline_params: dict | None,
        *,
        schema: PipelineSchema | None,
    ) -> None:
        """Adopt a new OptSearchPoint, preserving accumulated memory."""
        new_opt.memory = self.opt_sp.memory.model_copy(deep=True)
        self.opt_sp = new_opt
        assert self.current_sp is not None
        self.current_sp = self.opt_sp.to_job_search_point(
            base_pipeline_params=pipeline_params or self.current_sp.pipeline_params,
            schema=schema,
        )

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
