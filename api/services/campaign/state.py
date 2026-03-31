"""Mutable state types — LoopState, EscalationCounters, CycleInitResult."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from api.models.opt_search_point import OptSearchPoint
from api.models.search_point import JobSearchPoint
from api.services.campaign.results import CycleRoundResult

if TYPE_CHECKING:
    from api.models.analysis import FailureAnalysis
    from api.models.eval_context import EvalContext
    from api.services.campaign.escalation import DegradationCheck

__all__ = ["CycleInitResult", "EscalationCounters", "LoopState"]


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
    """

    rounds: list[CycleRoundResult] = field(default_factory=list)
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


@dataclass
class CycleInitResult:
    """Return type for ``_init_cycle_state()`` — replaces a 7-tuple."""

    state: LoopState
    campaign_store: Any = None
    cycle_id: str | None = None
    obs_campaign_id: str = ""
    round_eval_data: list[dict[str, Any]] = field(default_factory=list)
    escalation_checks: list[DegradationCheck] = field(default_factory=list)
    resumed_from_round: int = 0
    search_memory: Any = None
