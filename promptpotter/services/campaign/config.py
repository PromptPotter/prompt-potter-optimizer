"""Campaign configuration — CampaignConfig, LoopConfig, pipeline setup, LLM client factory.

``CampaignConfig`` is the typed schema for the ``campaign_config`` dict
that flows from notebooks / CLI through to services.  ``LoopConfig`` is
the Pydantic model for the optimization loop.  ``configure_pipeline()``
and ``create_llm_client()`` are factory helpers.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypedDict

from pydantic import BaseModel, Field, field_validator

from promptpotter.models.pipeline_schema import PipelineSchema
from promptpotter.models.search_point import TaskDecomposition
from promptpotter.services.search.scan_results import ScanBrief

if TYPE_CHECKING:
    from promptpotter.services.campaign.campaign_setup import SessionEnv
    from promptpotter.services.llm_client import LLMClientBase

logger = logging.getLogger(__name__)

__all__ = [
    "CampaignConfig",
    "LoopConfig",
    "configure_and_apply_pipeline",
    "create_llm_client",
]


class OptimizationConfig(TypedDict, total=False):
    """Optimization loop parameters (``campaign_config["optimization"]``)."""

    l1_patience: int
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
    pause_before_scoring: bool
    stale_data_load_protocol: list[str]
    elimination_n_min: int
    elimination_alpha: float


class OptimizerLLMConfig(TypedDict, total=False):
    """LLM provider settings (``campaign_config["optimizer_llm"]``)."""

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
    ``exclude_nodes``) is valid input.  ``dataset_name`` specifies the
    default DatasetStore split.  The dict is mutated in place by
    ``configure_pipeline()`` (sets ``pipeline_params``) and the CLI
    (sets ``optimization.pause_before_scoring``).
    """

    dataset_name: str
    dataset_type: str
    local_scoring_token: str
    sp_budget_ttest: int
    scan_sample_size: int
    exclude_nodes: list[str]
    pipeline_overrides: dict
    pipeline_params: dict | None
    optimization: OptimizationConfig
    optimizer_llm: OptimizerLLMConfig
    smart_search: SmartSearchConfig
    scoring: str


class LoopConfig(BaseModel):
    """Configuration for feedback cycling."""

    model_config = {"arbitrary_types_allowed": True}

    max_rounds: int | None = Field(10, description="Maximum optimization rounds (None = unlimited)")
    l1_patience: int = Field(3, description="Stop after N consecutive non-improving L1 rounds")
    n_variants: int = Field(5, description="Candidates per round")
    creativity: float = Field(0.7, description="Temperature for candidate generation")
    improvement_threshold: float = Field(0.01, description="Min accuracy delta")
    model: str | None = Field(None, description="LLM model identifier")
    backend_id: str = Field("", description="Backend identifier for caching")
    project_root: str = Field("", description="Project root for store")
    session_id: str = Field("", description="Session ID for persistence emitter")
    sp_budget_ttest: int = Field(20, description="Eval subsample size (must be > 0)")
    seed: int = Field(42, description="Random seed for subsampling")

    pipeline_schema: PipelineSchema | None = Field(
        None,
        description="Filtered pipeline schema with overrides baked in — single source of truth "
        "for active nodes, prompt node, and initial per-node config",
    )

    # Critique-guided generation
    enable_critique: bool = Field(True, description="Enable critique agent between rounds")

    # Scan-aware optimization
    scan_brief: ScanBrief | None = Field(
        None, description="Scan analytics context for candidate gen"
    )

    # Structured domain context (from TASK_DESCRIPTION decomposition)
    task_context: TaskDecomposition | None = Field(
        None,
        description="Structured domain context for L1 gen and L2 refinement",
    )

    @field_validator("task_context", mode="before")
    @classmethod
    def _coerce_task_context(cls, v: object) -> TaskDecomposition | None:
        if v is None:
            return None
        if isinstance(v, TaskDecomposition):
            return v
        if isinstance(v, dict):
            return TaskDecomposition.from_dict(v)
        return None

    # L2/L3 escalation
    enable_l2: bool = Field(True, description="Enable L2 refine_strategy loop")
    enable_l3: bool = Field(True, description="Enable L3 modify_plan loop")
    l2_patience: int | None = Field(2, description="L2 stalls before L3 (None=unlimited)")
    l3_patience: int | None = Field(1, description="L3 stalls before stop (None=unlimited)")

    # Configurable temperatures for L2/L3
    l2_temperature: float = Field(0.3, description="Temperature for L2 refine_strategy LLM call")
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

    # Sequential candidate elimination
    elimination_n_min: int = Field(
        20, description="Min queries before elimination t-test activates"
    )
    elimination_alpha: float = Field(
        0.05, description="Significance level for candidate elimination"
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
        description="Ordered fallback ladder for degraded cached samples (l1_score config)",
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

    # Per-dataset scoring formula (see shared/scoring.py)
    scoring_formula: str | None = Field(
        None,
        description="Python expression evaluated per query result (None = exact match)",
    )

    # HITL mode
    pause_before_scoring: bool = Field(
        False,
        description="Stop after L1 generate (before eval) for human/AI review",
    )

    # Cycle identity mode
    strict_cycle_identity: bool = Field(
        False,
        description="Include loop-control params (max_rounds, patience, "
        "degradation_threshold) in cycle identity hash. Default False = experiment "
        "mode where these can be tweaked freely between runs. Set True for "
        "publication experiments requiring exact reproducibility.",
    )

    @classmethod
    def from_campaign_config(
        cls,
        campaign_config: CampaignConfig,
        *,
        backend_id: str = "",
        project_root: str = "",
        session_id: str = "",
        pipeline_schema: PipelineSchema | None = None,
        scan_brief: ScanBrief | None = None,
        task_context: TaskDecomposition | dict | None = None,
    ) -> LoopConfig:
        """Build from the notebook's ``campaign_config`` dict.

        Uses LoopConfig field defaults for any missing keys — a minimal
        connector profile (e.g. just ``exclude_nodes``) is valid input.
        ``pipeline_schema`` should be the filtered schema with overrides
        already baked in (from ``configure_pipeline()``).
        """
        opt = campaign_config.get("optimization", {})
        optimizer_llm = campaign_config.get("optimizer_llm", {})
        return cls(
            max_rounds=opt.get("max_rounds", 10),
            l1_patience=opt.get("l1_patience", 3),
            n_variants=opt.get("n_variants", 5),
            creativity=opt.get("creativity", 0.7),
            improvement_threshold=opt.get("improvement_threshold", 0.01),
            model=optimizer_llm.get("model"),
            backend_id=backend_id,
            project_root=project_root,
            session_id=session_id,
            sp_budget_ttest=campaign_config.get("sp_budget_ttest", 20),
            seed=opt.get("seed", 42),
            pipeline_schema=pipeline_schema,
            scan_brief=scan_brief,
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
            elimination_n_min=opt.get("elimination_n_min", 20),
            elimination_alpha=opt.get("elimination_alpha", 0.05),
            max_consecutive_errors=opt.get("max_consecutive_errors", 3),
            hard_cap=opt.get("hard_cap", 100),
            critique_degradation_threshold=opt.get("critique_degradation_threshold", 0.4),
            critique_near_miss_ratio=opt.get("critique_near_miss_ratio", 0.3),
            stale_data_load_protocol=opt.get(
                "stale_data_load_protocol", ["rerun", "samplescan", "sampleswitch"]
            ),
            pause_before_scoring=opt.get("pause_before_scoring", False),
            scoring_formula=campaign_config.get("scoring"),
        )


@dataclass
class PreflightMetrics:
    """Computed display metrics for the preflight walkthrough."""

    eff_queries: int
    queries_label: str
    pipeline_label: str
    nodes_detail: str | None
    est_calls: int | None
    l2_label: str
    l3_label: str
    strategy: str


def compute_preflight_metrics(
    config: LoopConfig,
    dataset_size: int,
    exclude_nodes: list[str] | None = None,
    has_scan_brief: bool = False,
) -> PreflightMetrics:
    """Derive display-ready metrics from LoopConfig and dataset size."""
    eff_queries = config.sp_budget_ttest
    queries_label = f"{config.sp_budget_ttest} of {dataset_size}"

    schema = config.pipeline_schema
    active_nodes = list(schema.active_steps) if schema else []
    excluded = exclude_nodes or []
    total_nodes = len(active_nodes) + len(excluded)
    if active_nodes:
        pipeline_label = f"{len(active_nodes)} of {total_nodes} nodes"
        nodes_detail: str | None = ", ".join(active_nodes)
    else:
        pipeline_label = "(default pipeline)"
        nodes_detail = None

    # Sequential elimination: first candidate = full eval, others ~60% on average
    per_round = eff_queries + (config.n_variants - 1) * int(eff_queries * 0.6)
    est_calls = config.max_rounds * per_round if config.max_rounds is not None else None

    return PreflightMetrics(
        eff_queries=eff_queries,
        queries_label=queries_label,
        pipeline_label=pipeline_label,
        nodes_detail=nodes_detail,
        est_calls=est_calls,
        l2_label=f"enabled, patience={config.l2_patience}" if config.enable_l2 else "disabled",
        l3_label=f"enabled, patience={config.l3_patience}" if config.enable_l3 else "disabled",
        strategy="SCAN-AWARE" if has_scan_brief else "FREEFORM",
    )


def configure_and_apply_pipeline(
    session: SessionEnv,
    campaign_config: CampaignConfig,
    *,
    log: Callable[[str], None] = logger.info,
) -> dict:
    """Build pipeline identity, apply filtered schema to *session*, log summary.

    Uses *pipeline_schema* (from ``GET /pipeline``) as the source of
    truth for node names, falling back to *experiment_extract* only when the
    schema is unavailable.  Reads ``exclude_nodes`` and ``pipeline_overrides``
    from *campaign_config*.

    Returns ``pipeline_params`` dict ready for evaluation.
    """
    from promptpotter.services.backend_client import extract_pipeline_config

    pipeline_schema = session.pipeline_schema
    experiment_extract: dict | None = getattr(session, "experiment_extract", None)
    exclude = campaign_config.get("exclude_nodes", [])
    overrides = campaign_config.get("pipeline_overrides")

    if pipeline_schema:
        all_names = list(pipeline_schema.active_steps)
    elif experiment_extract:
        pipeline_config = extract_pipeline_config(experiment_extract)
        all_names = [s["name"] for s in pipeline_config["steps"]]
    else:
        all_names = []

    active = [n for n in all_names if n not in (exclude or [])]

    # Filter schema to active nodes only
    filtered = pipeline_schema
    if pipeline_schema and exclude:
        filtered = pipeline_schema.filter_to_steps(active)

    # Validate and apply overrides — bake into schema's current_config
    valid_overrides: dict[str, dict] = {}
    if overrides:
        for key, value in overrides.items():
            if isinstance(value, dict) and key in active:
                valid_overrides[key] = value
            elif isinstance(value, dict):
                logger.debug("configure_pipeline: skipping override for inactive node %r", key)
            else:
                logger.warning(
                    "configure_pipeline: ignoring non-nested override %r=%r "
                    '(use {"node_name": {"param": value}} format)',
                    key,
                    value,
                )
    if filtered and valid_overrides:
        filtered = filtered.with_overrides(valid_overrides)

    # Derive pipeline_params from the fully resolved schema
    pipeline_params = filtered.to_pipeline_params() if filtered else {"steps": active}
    campaign_config["pipeline_params"] = pipeline_params

    if filtered is not None:
        session.pipeline_schema = filtered

    excluded_nodes = list(exclude) if exclude else []
    nodes_str = ", ".join(active)
    excl_str = f"  Excluded: {', '.join(excluded_nodes)}" if excluded_nodes else ""
    log(f"Active nodes: {nodes_str}{excl_str}")

    return pipeline_params


def create_llm_client(
    campaign_config: CampaignConfig,
) -> tuple[LLMClientBase, str]:
    """Create LLM client + model from campaign_config['optimizer_llm'].

    Returns:
        Tuple of (llm_client, model_name).
    """
    from promptpotter.services.llm_client import get_llm_client

    optimizer_llm = campaign_config["optimizer_llm"]
    return get_llm_client(optimizer_llm["provider"]), optimizer_llm["model"]
