"""
Feedback cycle data models.

Configuration, round results, final results, and internal loop state for the
iterative prompt optimization cycle.
"""
from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeAlias

from pydantic import BaseModel, Field

from api.models.opt_search_point import OptSearchPoint
from api.models.phase_event import PhaseEvent
from api.models.pipeline_schema import PipelineSchema
from api.models.search_point import JobSearchPoint

if TYPE_CHECKING:
    from api.services.campaign.escalation import EscalationCheck
    from api.models.eval_context import EvalContext


# -- Callback type aliases (documented parameter semantics) ----------------

# (round_result, round_number)
OnRoundComplete: TypeAlias = Callable[["CycleRoundResult", int], None]
# (candidate_index, total_candidates, scores_dict)
OnCandidateEval: TypeAlias = Callable[[int, int, dict], None]
# (candidate_index, total_candidates, query_index, total_queries, result_dict)
OnQueryEval: TypeAlias = Callable[[int, int, int, int, dict], None]
# (phase_event)
OnPhase: TypeAlias = Callable[[PhaseEvent], None]


@dataclass
class CycleCallbacks:
    """Optional progress callbacks for the feedback cycle."""

    on_round_complete: OnRoundComplete | None = None
    on_candidate_eval: OnCandidateEval | None = None
    on_query_eval: OnQueryEval | None = None
    on_phase: OnPhase | None = None


class StopReason(str, enum.Enum):
    """Feedback cycle termination reasons."""

    PATIENCE = "patience_exhausted"
    PERFECT = "perfect_score"
    MAX_ROUNDS = "max_rounds"
    INTERRUPTED = "interrupted"
    ABORT = "escalation_abort"
    L2_PATIENCE = "l2_patience_exhausted"
    L3_PATIENCE = "l3_patience_exhausted"
    HARD_CAP = "hard_cap_reached"
    NEXT_ACTION = "next_action_stop"


class CycleConfig(BaseModel):
    """Configuration for feedback cycling."""

    model_config = {"arbitrary_types_allowed": True}

    max_rounds: int | None = Field(10, description="Maximum optimization rounds (None = unlimited)")
    patience: int = Field(3, description="Stop after N consecutive non-improvements")
    n_variants: int = Field(5, description="Candidates per round")
    creativity: float = Field(0.7, description="Temperature for candidate generation")
    improvement_threshold: float = Field(0.01, description="Min accuracy delta")
    model: str | None = Field(None, description="LLM model identifier")
    provider: str | None = Field(None, description="LLM provider")
    backend_url: str = Field(..., description="Backend URL for evaluation")
    backend_id: str = Field("", description="Backend identifier for caching")
    project_root: str = Field("", description="Project root for store")
    pipeline_params: dict | None = Field(None, description="Pipeline parameter overrides")
    session_terms: list[str] | None = Field(None, description="Backend session terms")
    temperature: float = Field(0.0, description="Temperature for content hash")
    sample_size: int = Field(0, description="Subsample size (0 = use all)")
    seed: int = Field(42, description="Random seed for subsampling")

    pipeline_schema: PipelineSchema | None = Field(None, description="Pipeline schema for eval")

    # Critique-guided generation
    enable_critique: bool = Field(True, description="Enable critique agent between rounds")
    critique_positive_threshold: float = Field(
        0.7, description="Accuracy threshold for positive vs negative critique",
    )

    # Scan-aware optimization
    scan_context: dict | None = Field(None, description="Scan analytics context for candidate gen")

    # Structured domain context (from TASK_DESCRIPTION decomposition)
    task_context: dict | None = Field(
        None, description="Structured domain context for L1 gen and L2 refinement",
    )

    # L2/L3 escalation
    enable_l2: bool = Field(True, description="Enable L2 refine_context loop")
    enable_l3: bool = Field(True, description="Enable L3 modify_plan loop")
    l2_patience: int | None = Field(2, description="L2 stalls before L3 (None=unlimited)")
    l3_patience: int | None = Field(1, description="L3 stalls before stop (None=unlimited)")

    # Configurable temperatures for L2/L3
    l2_temperature: float = Field(0.3, description="Temperature for L2 refine_context LLM call")
    l3_temperature: float = Field(0.5, description="Temperature for L3 modify_plan LLM call")

    # Escalation
    degradation_threshold: float = Field(
        0.4,
        description="Fraction of degraded queries to trigger escalation (0 = disabled)",
    )
    backend_warning_threshold: int = Field(
        2,
        description="Degradation resets before emitting backend warning (0 = disabled)",
    )
    max_failures: int = Field(
        15, description="Max failure examples fed to LLM candidate generation",
    )

    @classmethod
    def from_campaign_config(
        cls,
        campaign_config: dict,
        *,
        backend_url: str = "",
        backend_id: str = "",
        project_root: str = "",
        pipeline_params: dict | None = None,
        session_terms: list[str] | None = None,
        pipeline_schema: PipelineSchema | None = None,
        scan_context: dict | None = None,
        task_context: dict | None = None,
    ) -> CycleConfig:
        """Build from the notebook's ``campaign_config`` dict.

        All experiment knobs must be explicitly set in the notebook — no
        hidden defaults.  Missing keys raise ``KeyError`` immediately so
        the researcher sees what's missing.
        """
        opt = campaign_config["optimization"]
        eval_llm = campaign_config["eval_llm"]
        return cls(
            max_rounds=opt["max_rounds"],
            patience=opt["patience"],
            n_variants=opt["n_variants"],
            creativity=opt["creativity"],
            improvement_threshold=opt["improvement_threshold"],
            model=eval_llm["model"],
            provider=None,
            backend_url=backend_url,
            backend_id=backend_id,
            project_root=project_root,
            pipeline_params=pipeline_params,
            session_terms=session_terms,
            temperature=eval_llm["temperature"],
            sample_size=campaign_config["sample_size"],
            seed=opt["seed"],
            pipeline_schema=pipeline_schema,
            scan_context=scan_context,
            task_context=task_context,
            enable_l2=opt["enable_l2"],
            enable_l3=opt["enable_l3"],
            l2_patience=opt["l2_patience"],
            l3_patience=opt["l3_patience"],
            l2_temperature=opt["l2_temperature"],
            l3_temperature=opt["l3_temperature"],
            enable_critique=opt["enable_critique"],
            critique_positive_threshold=opt["critique_positive_threshold"],
            degradation_threshold=opt["degradation_threshold"],
            backend_warning_threshold=opt["backend_warning_threshold"],
            max_failures=opt["max_failures"],
        )


class CycleRoundResult(BaseModel):
    """Result of a single feedback cycle round."""

    round: int
    label: str
    accuracy: float
    composite: float = 0.0
    hits: int
    total: int
    improved: bool
    next_action: str
    prompt_fields: dict
    pipeline_params: dict | None = None
    results: list[dict] = Field(default_factory=list)
    candidates_evaluated: int
    candidate_scores: list[dict] = Field(default_factory=list)
    degraded_queries: int = 0
    escalation_signal: dict | None = None


class CycleResult(BaseModel):
    """Final result of the feedback cycling process."""

    rounds: list[CycleRoundResult]
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


@dataclass
class LoopState:
    """Mutable state threaded through the feedback cycle round loop.

    Optimizer-level state (critique, thinking_styles, task_context,
    escalation_journal, warning_inventory) lives on ``opt_sp``
    — a mutable ``OptSearchPoint`` that is serialized at checkpoint
    time and hydrated on resume.
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

    # L2/L3 escalation counters
    escalation: EscalationCounters = field(default_factory=EscalationCounters)


@dataclass
class CycleInitResult:
    """Return type for ``_init_cycle_state()`` — replaces a 7-tuple."""

    state: LoopState
    campaign_store: Any = None
    cycle_id: str | None = None
    obs_campaign_id: str = ""
    round_eval_data: list[dict[str, Any]] = field(default_factory=list)
    escalation_checks: "list[EscalationCheck]" = field(default_factory=list)
    resumed_from_round: int = 0
