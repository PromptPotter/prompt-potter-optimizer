"""CycleConfig — configuration for the optimization loop."""

from __future__ import annotations

from pydantic import BaseModel, Field

from api.models.pipeline_schema import PipelineSchema

__all__ = ["CycleConfig"]


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
    session_terms: list[str] | None = Field(None, description="Backend session terms")
    sample_size: int = Field(0, description="Subsample size (0 = use all)")
    seed: int = Field(42, description="Random seed for subsampling")

    pipeline_schema: PipelineSchema | None = Field(None, description="Pipeline schema for eval")

    # Critique-guided generation
    enable_critique: bool = Field(True, description="Enable critique agent between rounds")

    # Scan-aware optimization
    scan_context: dict | None = Field(None, description="Scan analytics context for candidate gen")

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

    # Critique thresholds
    critique_degradation_threshold: float = Field(
        0.4,
        description="Degradation rate above which critique flags an anomaly",
    )
    critique_near_miss_ratio: float = Field(
        0.3,
        description="Near-miss/miss ratio above which critique flags ranking issues",
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
            l1_patience=opt["patience"],
            n_variants=opt["n_variants"],
            creativity=opt["creativity"],
            improvement_threshold=opt["improvement_threshold"],
            model=eval_llm["model"],
            backend_url=backend_url,
            backend_id=backend_id,
            project_root=project_root,
            pipeline_params=pipeline_params,
            session_terms=session_terms,
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
            degradation_threshold=opt["degradation_threshold"],
            backend_warning_threshold=opt["backend_warning_threshold"],
            max_failures=opt["max_failures"],
        )
