"""CycleConfig — configuration for the optimization loop.

Also defines ``CampaignConfig`` TypedDict — the typed schema for the
``campaign_config`` dict that flows from notebooks / CLI through to
services.
"""

from __future__ import annotations

from typing import TypedDict

from pydantic import BaseModel, Field

from promptpotter.models.pipeline_schema import PipelineSchema
from promptpotter.services.search.scan_results import ScanContext

__all__ = ["CampaignConfig", "CycleConfig"]


# ---------------------------------------------------------------------------
# CampaignConfig TypedDict — typed schema for the campaign_config dict
# ---------------------------------------------------------------------------


class OptimizationConfig(TypedDict, total=False):
    """Optimization loop parameters (``campaign_config["optimization"]``)."""

    patience: int
    max_rounds: int | None
    n_variants: int
    creativity: float
    improvement_threshold: float
    seed: int
    max_failures: int
    degradation_threshold: float
    backend_warning_threshold: int
    enable_l2: bool
    enable_l3: bool
    l2_patience: int | None
    l3_patience: int | None
    l2_temperature: float
    l3_temperature: float
    enable_critique: bool
    pause_before_eval: bool
    stale_data_load_protocol: list[str]


class EvalLLMConfig(TypedDict, total=False):
    """LLM provider settings (``campaign_config["eval_llm"]``)."""

    model: str
    provider: str
    temperature: float
    max_tokens: int


class SmartSearchConfig(TypedDict, total=False):
    """Sensitivity scan parameters (``campaign_config["smart_search"]``)."""

    n_diagnostic: int
    max_rounds: int
    stop_threshold: float
    seed: int


class CampaignConfig(TypedDict, total=False):
    """Top-level campaign configuration.

    All keys are optional — a minimal connector profile (e.g. just
    ``exclude_nodes``) is valid input.  The dict is mutated in place by
    ``configure_pipeline()`` (sets ``pipeline_params``) and the CLI
    (sets ``optimization.pause_before_eval``).
    """

    sample_size: int
    exploration_sample_size: int
    exploration_rate: float
    exclude_nodes: list[str]
    pipeline_overrides: dict
    pipeline_params: dict | None
    optimization: OptimizationConfig
    eval_llm: EvalLLMConfig
    smart_search: SmartSearchConfig


# ---------------------------------------------------------------------------
# CycleConfig — Pydantic model for the optimization loop
# ---------------------------------------------------------------------------


class CycleConfig(BaseModel):
    """Configuration for feedback cycling."""

    model_config = {"arbitrary_types_allowed": True}

    max_rounds: int | None = Field(10, description="Maximum optimization rounds (None = unlimited)")
    l1_patience: int = Field(3, description="Stop after N consecutive non-improving L1 rounds")
    n_variants: int = Field(5, description="Candidates per round")
    creativity: float = Field(0.7, description="Temperature for candidate generation")
    improvement_threshold: float = Field(0.01, description="Min accuracy delta")
    model: str | None = Field(None, description="LLM model identifier")
    backend_url: str = Field(..., description="Backend URL for evaluation")
    backend_id: str = Field("", description="Backend identifier for caching")
    project_root: str = Field("", description="Project root for store")
    pipeline_params: dict | None = Field(None, description="Pipeline parameter overrides")
    active_steps: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Immutable active node sequence — authoritative source of pipeline composition",
    )
    session_terms: list[str] | None = Field(None, description="Backend session terms")
    session_id: str = Field("", description="Session ID for persistence emitter")
    sample_size: int = Field(0, description="Subsample size (0 = use all)")
    seed: int = Field(42, description="Random seed for subsampling")

    pipeline_schema: PipelineSchema | None = Field(None, description="Pipeline schema for eval")
    prompt_node: str = Field("", description="Prompt-bearing node name (from schema.prompt_node_names())")

    # Critique-guided generation
    enable_critique: bool = Field(True, description="Enable critique agent between rounds")

    # Scan-aware optimization
    scan_context: ScanContext | None = Field(None, description="Scan analytics context for candidate gen")

    # Structured domain context (from TASK_DESCRIPTION decomposition)
    task_context: dict | None = Field(
        None,
        description="Structured domain context for L1 gen and L2 refinement",
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
        15,
        description="Max failure examples fed to LLM candidate generation",
    )

    # Eval robustness
    max_consecutive_errors: int = Field(
        3,
        description="Abort eval batch after N consecutive backend errors",
    )
    hard_cap: int = Field(
        100,
        description="Safety cap on total rounds (incl. probe rounds)",
    )

    # Stale data handling
    stale_data_load_protocol: list[str] = Field(
        default=["rerun", "samplescan", "sampleswitch"],
        description="Ordered fallback ladder for degraded cached samples (l1_evaluate config)",
    )

    # Critique thresholds
    critique_degradation_threshold: float = Field(
        0.4,
        description="Degradation rate above which critique flags an anomaly",
    )
    critique_near_miss_ratio: float = Field(
        0.3,
        description="Near-miss/miss ratio above which critique flags ranking issues",
    )

    # HITL mode
    pause_before_eval: bool = Field(
        False,
        description="Stop after L1 generate (before eval) for human/AI review",
    )

    @classmethod
    def from_campaign_config(
        cls,
        campaign_config: CampaignConfig,
        *,
        backend_url: str = "",
        backend_id: str = "",
        project_root: str = "",
        pipeline_params: dict | None = None,
        session_terms: list[str] | None = None,
        session_id: str = "",
        pipeline_schema: PipelineSchema | None = None,
        scan_context: ScanContext | None = None,
        task_context: dict | None = None,
    ) -> CycleConfig:
        """Build from the notebook's ``campaign_config`` dict.

        Uses CycleConfig field defaults for any missing keys — a minimal
        connector profile (e.g. just ``exclude_nodes``) is valid input.
        """
        opt = campaign_config.get("optimization", {})
        eval_llm = campaign_config.get("eval_llm", {})
        return cls(
            max_rounds=opt.get("max_rounds", 10),
            l1_patience=opt.get("patience", 3),
            n_variants=opt.get("n_variants", 5),
            creativity=opt.get("creativity", 0.7),
            improvement_threshold=opt.get("improvement_threshold", 0.01),
            model=eval_llm.get("model"),
            backend_url=backend_url,
            backend_id=backend_id,
            project_root=project_root,
            pipeline_params=pipeline_params,
            active_steps=tuple(pipeline_params.get("steps", [])) if pipeline_params else (),
            session_terms=session_terms,
            session_id=session_id,
            sample_size=campaign_config.get("sample_size", 0),
            seed=opt.get("seed", 42),
            pipeline_schema=pipeline_schema,
            prompt_node=pipeline_schema.prompt_node_names()[0] if pipeline_schema and pipeline_schema.prompt_node_names() else "",
            scan_context=scan_context,
            task_context=task_context,
            enable_l2=opt.get("enable_l2", True),
            enable_l3=opt.get("enable_l3", True),
            l2_patience=opt.get("l2_patience", 2),
            l3_patience=opt.get("l3_patience", 1),
            l2_temperature=opt.get("l2_temperature", 0.3),
            l3_temperature=opt.get("l3_temperature", 0.5),
            enable_critique=opt.get("enable_critique", True),
            degradation_threshold=opt.get("degradation_threshold", 0.4),
            backend_warning_threshold=opt.get("backend_warning_threshold", 2),
            max_failures=opt.get("max_failures", 15),
            stale_data_load_protocol=opt.get("stale_data_load_protocol", ["rerun", "samplescan", "sampleswitch"]),
            pause_before_eval=opt.get("pause_before_eval", False),
        )
