"""
Feedback cycle data models.

Configuration, round results, final results, and internal loop state for the
iterative prompt optimization cycle.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from api.models.pipeline_schema import PipelineSchema
from api.models.search_point import SearchPoint

if TYPE_CHECKING:
    from api.services.prompt_eval import EvalContext


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
    generate_suggestions: bool = Field(False, description="LLM suggestions each round")
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

    # Configurable temperatures for L2/L3/suggestions
    l2_temperature: float = Field(0.3, description="Temperature for L2 refine_context LLM call")
    l3_temperature: float = Field(0.5, description="Temperature for L3 modify_plan LLM call")
    suggestion_temperature: float = Field(0.0, description="Temperature for suggestion generation")

    # Escalation
    degradation_threshold: float = Field(
        0.4,
        description="Fraction of degraded queries to trigger escalation (0 = disabled)",
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
        """Build from the notebook's ``campaign_config`` dict."""
        opt = campaign_config.get("optimization", {})
        eval_llm = campaign_config.get("eval_llm", {})
        return cls(
            max_rounds=opt.get("max_rounds", 10),
            patience=opt.get("patience", 3),
            n_variants=opt.get("n_variants", 5),
            creativity=opt.get("creativity", 0.7),
            improvement_threshold=opt.get("improvement_threshold", 0.01),
            model=eval_llm.get("model"),
            provider=None,
            backend_url=backend_url,
            backend_id=backend_id,
            project_root=project_root,
            generate_suggestions=False,
            pipeline_params=pipeline_params,
            session_terms=session_terms,
            temperature=eval_llm.get("temperature", 0.0),
            sample_size=campaign_config.get("sample_size", 0),
            seed=42,
            pipeline_schema=pipeline_schema,
            scan_context=scan_context,
            task_context=task_context,
            enable_l2=opt.get("enable_l2", True),
            enable_l3=opt.get("enable_l3", True),
            l2_patience=opt.get("l2_patience", 2),
            l3_patience=opt.get("l3_patience", 1),
            l2_temperature=opt.get("l2_temperature", 0.3),
            l3_temperature=opt.get("l3_temperature", 0.5),
            suggestion_temperature=opt.get("suggestion_temperature", 0.0),
            enable_critique=opt.get("enable_critique", True),
            critique_positive_threshold=opt.get("critique_positive_threshold", 0.7),
            degradation_threshold=opt.get("degradation_threshold", 0.4),
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
    prompt_state: dict
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
    winner_prompt_state: dict
    winner_pipeline_params: dict | None = None
    stop_reason: str
    started_at: str
    finished_at: str
    langfuse_trace_id: str | None = None
    cycle_id: str | None = None
    resumed_from_round: int = 0


@dataclass
class _LoopState:
    """Mutable state threaded through the feedback cycle round loop."""

    rounds: list[CycleRoundResult] = field(default_factory=list)
    current_sp: SearchPoint | None = None
    current_accuracy: float = 0.0
    current_composite: float = 0.0
    current_results: list[dict] = field(default_factory=list)
    best_accuracy: float = 0.0
    best_composite: float = 0.0
    best_round: int = -1
    best_sp: SearchPoint | None = None
    stall_count: int = 0
    eval_ctx: EvalContext | None = None

    # Critique + thinking styles (meta-level, fed forward between rounds)
    critique_text: str = ""
    thinking_styles: list[str] = field(default_factory=list)

    # L2/L3 state
    l2_stall_count: int = 0
    l3_stall_count: int = 0
    l2_round: int = 0
    l3_round: int = 0
    best_accuracy_at_l2_entry: float = 0.0
    best_accuracy_at_l3_entry: float = 0.0
    best_composite_at_l2_entry: float = 0.0
    best_composite_at_l3_entry: float = 0.0

    # Escalation investigation memory (fed to L2 across degradation rounds)
    escalation_journal: list[dict] = field(default_factory=list)

    # Structured domain context (refinable by L2)
    task_context: dict = field(default_factory=dict)
