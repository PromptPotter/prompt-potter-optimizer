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

# Sole sanctioned bridge from scan -> optimization: ReconBrief flows through RunConfig into L1.
from promptpotter.application.recon.recon_report import ReconBrief
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.search_point import TaskDecomposition

if TYPE_CHECKING:
    from promptpotter.application.campaign.campaign_setup import SessionEnv
    from promptpotter.infrastructure.llm.client import LLMClientBase

logger = logging.getLogger(__name__)

__all__ = [
    "CampaignConfig",
    "LoopConfig",
    "PreflightWarning",
    "configure_and_apply_pipeline",
    "create_llm_client",
    "run_preflight_checks",
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
    empty_output_threshold: float
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
    """Sensitivity scan parameters (``campaign_config["adaptive_recon"]``)."""

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
    starting_prompt: str
    sp_budget_ttest: int
    recon_sample_size: int
    exclude_nodes: list[str]
    pipeline_overrides: dict
    pipeline_params: dict | None
    optimization: OptimizationConfig
    optimizer_llm: OptimizerLLMConfig
    adaptive_recon: SmartSearchConfig
    # Scoring formula. Either a single string (interpreted as ``per_query``
    # with the default per-round formula) or a dict ``{per_query, per_round}``.
    scoring: str | dict[str, str]


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
    dataset_name: str = Field(
        "",
        description="Name of the active dataset (resolves to "
        "``{backend_id}/datasets/{dataset_name}.json``). Required for "
        "zero-signal filtering.",
    )

    # Zero-signal sample filtering (deterministic, no LLM)
    zero_signal_filter_enabled: bool = Field(
        False,
        description="Prune always-hit and always-miss queries from the active "
        "dataset at round boundaries. Uses SearchMemory.dead_queries(). "
        "Off by default — at small t-test budgets the filter rarely accumulates "
        "enough observations to fire and can shrink the active dataset in ways "
        "that confuse publication-grade benchmark runs. Opt in explicitly.",
    )
    zero_signal_filter_min_observations: int = Field(
        5,
        description="Minimum Bernoulli samples required before a query is "
        "eligible for zero-signal exclusion.",
    )
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
    recon_brief: ReconBrief | None = Field(
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
    empty_output_threshold: float = Field(
        0.5,
        description="Fraction of empty-prediction queries to eliminate a candidate "
        "(0 = disabled). Catches verbose prompts that blow max_tokens mid-reasoning.",
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
    elimination_n_min: int = Field(4, description="Min queries before elimination t-test activates")
    elimination_alpha: float = Field(
        0.2,
        description="Significance level for candidate elimination. Tuned for "
        "online ranking (0.2) rather than publication-strict inference (0.05) "
        "— at small t-test budgets (n<20) the 0.05 threshold almost never "
        "fires for realistic gaps. Holm-Bonferroni correction across priors "
        "still applies on top of this.",
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
    scoring_round_formula: str | None = Field(
        None,
        description="Python expression evaluated per round against the evaluator "
        "registry namespace (None = default weighted sum).",
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
        recon_brief: ReconBrief | None = None,
        task_context: TaskDecomposition | dict | None = None,
        dataset_name: str = "",
    ) -> LoopConfig:
        """Build from the notebook's ``campaign_config`` dict.

        Uses LoopConfig field defaults for any missing keys — defaults live
        on the pydantic fields, not here. ``pipeline_schema`` should be the
        filtered schema with overrides already baked in.
        """
        opt = dict(campaign_config.get("optimization", {}))
        scoring_block = campaign_config.get("scoring")
        if isinstance(scoring_block, dict):
            query_formula = scoring_block.get("per_query")
            round_formula = scoring_block.get("per_round")
        else:
            query_formula = scoring_block
            round_formula = None
        return cls(
            **opt,
            model=campaign_config.get("optimizer_llm", {}).get("model"),
            sp_budget_ttest=campaign_config.get("sp_budget_ttest", 20),
            scoring_formula=query_formula,
            scoring_round_formula=round_formula,
            backend_id=backend_id,
            project_root=project_root,
            session_id=session_id,
            dataset_name=dataset_name or campaign_config.get("dataset_name", ""),
            pipeline_schema=pipeline_schema,
            recon_brief=recon_brief,
            task_context=task_context,
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


@dataclass(frozen=True)
class PreflightWarning:
    """Structured pre-run warning surfaced before a campaign kicks off.

    Rendered in yellow at the top of the init display and mirrored to the
    logger. Pure data — no I/O, no check-specific rendering logic.
    """

    code: str
    title: str
    detail: str


def _check_sp_budget_vs_dataset(config: LoopConfig, dataset: list) -> PreflightWarning | None:
    n = config.sp_budget_ttest
    m = len(dataset)
    if m > 0 and n > m:
        return PreflightWarning(
            code="sp_budget_exceeds_dataset",
            title=f"sp_budget_ttest ({n}) exceeds dataset size ({m})",
            detail=(
                f"Scoring will run on {m} samples (the full dataset). "
                f"Lower sp_budget_ttest to {m} or grow the dataset to silence "
                f"this warning."
            ),
        )
    return None


_PREFLIGHT_CHECKS: list[Callable[[LoopConfig, list], PreflightWarning | None]] = [
    _check_sp_budget_vs_dataset,
]


def run_preflight_checks(config: LoopConfig, dataset: list) -> list[PreflightWarning]:
    """Run all registered preflight checks. Pure — no mutation, no I/O.

    Adding a new check is a one-line append to ``_PREFLIGHT_CHECKS``.
    """
    warnings: list[PreflightWarning] = []
    for check in _PREFLIGHT_CHECKS:
        w = check(config, dataset)
        if w is not None:
            warnings.append(w)
    return warnings


def compute_preflight_metrics(
    config: LoopConfig,
    dataset_size: int,
    exclude_nodes: list[str] | None = None,
    has_recon_brief: bool = False,
) -> PreflightMetrics:
    """Derive display-ready metrics from LoopConfig and dataset size.

    Does not mutate ``config``. If ``sp_budget_ttest`` exceeds ``dataset_size``
    the effective query count is clamped for display only; ``sample_dataset``
    handles runtime truncation. Surface the mismatch via ``run_preflight_checks``.
    """
    eff_queries = (
        min(config.sp_budget_ttest, dataset_size) if dataset_size > 0 else config.sp_budget_ttest
    )
    queries_label = f"{eff_queries} of {dataset_size}"

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
        strategy="RECON-AWARE" if has_recon_brief else "FREEFORM",
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
    from promptpotter.application.datasets.prompt_store import (
        has_dataset_prompts,
        load_node_prompt,
    )
    from promptpotter.infrastructure.backend.client import extract_pipeline_config

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
    # Starting-point prompt templates → inject per prompt-bearing node from the
    # canonical store ``datasets/{name}/prompts/``.  Resolution is per-node: if
    # ``{node_name}.json`` exists it wins; otherwise ``{starting_prompt}.json``
    # (typically ``default.json``) is used — which is the common single-node
    # case.  Authoring a monolithic ``prompt`` string in ``pipeline.json``
    # ``config`` is deprecated (warning emitted by parse_pipeline_response).
    dataset_name = campaign_config.get("dataset_name")
    starting_name = campaign_config.get("starting_prompt", "default")
    if dataset_name and filtered and has_dataset_prompts(dataset_name):
        prompt_nodes = [n for n in filtered.prompt_node_names() if n in active]
        for pnode in prompt_nodes:
            template = load_node_prompt(dataset_name, pnode, starting_name)
            # Use render() — not compile_prompt() — so backend-side ``{{var}}``
            # placeholders (e.g. TermNorm's ``{{query}}``, ``{{entity_profile_json}}``)
            # pass through unchanged. compile_prompt is for optimizer meta-prompts
            # whose variables are substituted in-process before the LLM call.
            valid_overrides.setdefault(pnode, {})["prompt"] = template.render()
            log(f"Starting prompt: {dataset_name}/prompts/[{pnode}|{starting_name}].json → {pnode}")

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
    from promptpotter.infrastructure.llm.client import get_llm_client

    optimizer_llm = campaign_config["optimizer_llm"]
    return get_llm_client(optimizer_llm["provider"]), optimizer_llm["model"]
