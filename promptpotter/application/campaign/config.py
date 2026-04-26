"""Campaign configuration — CampaignConfig Pydantic model, pipeline setup, LLM factory."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from promptpotter.application.campaign.campaign_setup import Session
    from promptpotter.infrastructure.llm.client import LLMClientBase

logger = logging.getLogger(__name__)

__all__ = [
    "AdaptivePrefixConfig",
    "CampaignConfig",
    "HardSampleSorterConfig",
    "OptimizationConfig",
    "OptimizerLLMConfig",
    "PreflightWarning",
    "compute_preflight_metrics",
    "configure_and_apply_pipeline",
    "create_llm_client",
    "load_campaign_config",
    "run_preflight_checks",
]


class AdaptivePrefixConfig(BaseModel):
    """Round-level adaptive sample-prefix selection (Rasch + KG).

    Off by default — when ``enabled`` is false the prefix is the static
    ``sample_dataset(dataset, sp_budget_ttest)`` it has always been. When
    on, ``AdaptivePrefix.evolve()`` runs after each round, refits Rasch on
    accumulated observations, and swaps low-info samples (narrow CI on
    δ_s) for high-KG samples not currently in the prefix.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(False, description="Master off-switch — false = today's behavior.")
    swap_out_delta_se: float = Field(
        0.25,
        description="Swap-out threshold: SE on δ_s below which a sample is 'understood' "
        "(corresponds to ~95% CI width of 1.0 in logits).",
    )
    swap_in_kg_threshold: float = Field(
        0.01,
        description="Swap-in threshold: minimum sample KG to be eligible.",
    )
    max_swaps_per_round: int = Field(3, description="Cap on prefix churn per round.")
    min_prefix_size: int | None = Field(
        None,
        description="Floor on prefix size (None → derive from elimination_n_min).",
    )
    cold_start_prior_sigma: float = Field(
        1.5,
        description="Sigma on the N(0, sigma²) theta prior when no observations yet.",
    )


class HardSampleSorterConfig(BaseModel):
    """Hard-sample-sorter artifact at campaign finalize.

    Independent of ``AdaptivePrefixConfig`` — even when adaptive-prefix is
    off, the sorter fits its own Rasch posterior on accumulated observations
    and emits ``hard_samples.json`` (θ_c-ranked candidates × δ_s-ranked
    samples × hit/miss/unmeasured). Capped to top-K on disk; the full matrix
    is recomputable on demand via ``build_hard_samples_artifact(rounds,
    top_k_candidates=None, top_k_samples=None)``.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        True,
        description="Master switch — on by default. Off writes a stub artifact.",
    )
    top_k_candidates: int = Field(
        40,
        description="Cap on persisted candidate axis (θ_c desc). None in the builder "
        "signature disables capping for on-demand full retrieval.",
    )
    top_k_samples: int = Field(
        40,
        description="Cap on persisted sample axis (δ_s desc).",
    )


class OptimizationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # L1 loop control
    max_rounds: int | None = Field(10, description="Max rounds (None = unlimited)")
    l1_patience: int = Field(3, description="Stop after N consecutive non-improving L1 rounds")
    n_variants: int = Field(5, description="Candidates per round")
    creativity: float = Field(0.7, description="Temperature for candidate generation")
    improvement_threshold: float = Field(..., description="Min accuracy delta")
    seed: int = Field(..., description="Random seed")
    max_failures: int = Field(..., description="Max failure examples fed to L1")

    # L2/L3 escalation
    enable_l2: bool = Field(...)
    enable_l3: bool = Field(...)
    l2_patience: int | None = Field(2)
    l3_patience: int | None = Field(1)
    l2_temperature: float = Field(0.3)
    l3_temperature: float = Field(0.5)

    # L1 Critique
    l1_critique_degradation_threshold: float = Field(0.4)
    l1_critique_near_miss_ratio: float = Field(0.3)

    # Degradation / escalation
    degradation_threshold: float = Field(...)
    empty_output_threshold: float = Field(0.5)
    backend_warning_threshold: int = Field(2)

    # Sequential elimination
    elimination_n_min: int = Field(4)
    elimination_alpha: float = Field(0.2)

    # Eval robustness
    max_consecutive_errors: int = Field(3)
    hard_cap: int = Field(100)

    # Stale data
    stale_data_load_protocol: list[str] = Field(
        default_factory=lambda: ["rerun", "samplescan", "sampleswitch"]
    )

    # Zero-signal filter
    zero_signal_filter_enabled: bool = Field(False)
    zero_signal_filter_min_observations: int = Field(5)

    # Adaptive sample-prefix selection (Rasch + KG)
    adaptive_prefix: AdaptivePrefixConfig = Field(default_factory=AdaptivePrefixConfig)

    # Hard-sample-sorter artifact at finalize
    hard_sample_sorter: HardSampleSorterConfig = Field(default_factory=HardSampleSorterConfig)


class OptimizerLLMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field("groq")
    model: str | None = Field(None)
    temperature: float = Field(0.4)
    max_tokens: int = Field(2000)


class CampaignConfig(BaseModel):
    """Top-level user-authored campaign configuration (``datasets/{name}/campaign.json``)."""

    model_config = ConfigDict(extra="forbid")

    # Top-level scalars
    dataset_name: str = Field("")
    starting_prompt: str = Field("default")
    sp_budget_ttest: int = Field(20)
    exclude_nodes: list[str] = Field(default_factory=list)
    pipeline_overrides: dict = Field(default_factory=dict)
    scoring: str | dict[str, str] | None = Field(None)

    # Nested
    optimization: OptimizationConfig
    optimizer_llm: OptimizerLLMConfig = Field(default_factory=OptimizerLLMConfig)


def load_campaign_config(raw: dict | CampaignConfig) -> CampaignConfig:
    """Normalize raw dict / Pydantic input into a validated ``CampaignConfig``."""
    if isinstance(raw, CampaignConfig):
        return raw
    return CampaignConfig.model_validate(raw)


@dataclass
class PreflightMetrics:
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
    """Structured pre-run warning surfaced before a campaign kicks off."""

    code: str
    title: str
    detail: str


def _check_sp_budget_vs_dataset(config: CampaignConfig, dataset: list) -> PreflightWarning | None:
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


def run_preflight_checks(config: CampaignConfig, dataset: list) -> list[PreflightWarning]:
    """Run all preflight checks. Pure — no mutation, no I/O."""
    warnings: list[PreflightWarning] = []
    if (w := _check_sp_budget_vs_dataset(config, dataset)) is not None:
        warnings.append(w)
    return warnings


def compute_preflight_metrics(
    config: CampaignConfig,
    session: Session | None,
    dataset_size: int,
    *,
    exclude_nodes: list[str] | None = None,
) -> PreflightMetrics:
    """Derive display-ready metrics from ``CampaignConfig`` + ``Session``. Pure."""
    sp_budget = config.sp_budget_ttest
    eff_queries = min(sp_budget, dataset_size) if dataset_size > 0 else sp_budget
    queries_label = f"{eff_queries} of {dataset_size}"

    schema = session.pipeline_schema if session else None
    active_nodes = list(schema.active_steps) if schema else []
    excluded = exclude_nodes or []
    total_nodes = len(active_nodes) + len(excluded)
    if active_nodes:
        pipeline_label = f"{len(active_nodes)} of {total_nodes} nodes"
        nodes_detail: str | None = ", ".join(active_nodes)
    else:
        pipeline_label = "(default pipeline)"
        nodes_detail = None

    opt = config.optimization
    per_round = eff_queries + (opt.n_variants - 1) * int(eff_queries * 0.6)
    est_calls = opt.max_rounds * per_round if opt.max_rounds is not None else None

    return PreflightMetrics(
        eff_queries=eff_queries,
        queries_label=queries_label,
        pipeline_label=pipeline_label,
        nodes_detail=nodes_detail,
        est_calls=est_calls,
        l2_label=f"enabled, patience={opt.l2_patience}" if opt.enable_l2 else "disabled",
        l3_label=f"enabled, patience={opt.l3_patience}" if opt.enable_l3 else "disabled",
        strategy="FREEFORM",
    )


def configure_and_apply_pipeline(
    session: Session,
    campaign_config: CampaignConfig,
    *,
    log: Callable[[str], None] = logger.info,
) -> dict:
    """Build pipeline identity, apply filtered schema + overrides onto *session*."""
    from promptpotter.application.datasets.prompt_store import (
        has_dataset_prompts,
        load_node_prompt,
    )
    from promptpotter.infrastructure.backend.client import extract_pipeline_config

    pipeline_schema = session.pipeline_schema
    experiment_extract: dict = session.experiment_extract
    exclude = list(campaign_config.exclude_nodes)
    overrides = campaign_config.pipeline_overrides

    if pipeline_schema:
        all_names = list(pipeline_schema.active_steps)
    elif experiment_extract:
        pipeline_config = extract_pipeline_config(experiment_extract)
        all_names = [s["name"] for s in pipeline_config["steps"]]
    else:
        all_names = []

    active = [n for n in all_names if n not in exclude]

    filtered = pipeline_schema
    if pipeline_schema and exclude:
        filtered = pipeline_schema.filter_to_steps(active)

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

    # Starting-point prompts from ``datasets/{name}/prompts/`` injected
    # per prompt-bearing node.
    dataset_name = campaign_config.dataset_name or (session.dataset_name or "")
    starting_name = campaign_config.starting_prompt or "default"
    if dataset_name and filtered and has_dataset_prompts(dataset_name):
        prompt_nodes = [n for n in filtered.prompt_node_names() if n in active]
        for pnode in prompt_nodes:
            template = load_node_prompt(dataset_name, pnode, starting_name)
            valid_overrides.setdefault(pnode, {})["prompt"] = template.render()
            log(f"Starting prompt: {dataset_name}/prompts/[{pnode}|{starting_name}].json → {pnode}")

    if filtered and valid_overrides:
        filtered = filtered.with_overrides(valid_overrides)

    pipeline_params = filtered.to_pipeline_params() if filtered else {"steps": active}

    if filtered is not None:
        session.pipeline_schema = filtered
    session.pipeline_params = pipeline_params

    excluded_nodes = list(exclude) if exclude else []
    nodes_str = ", ".join(active)
    excl_str = f"  Excluded: {', '.join(excluded_nodes)}" if excluded_nodes else ""
    log(f"Active nodes: {nodes_str}{excl_str}")

    return pipeline_params


def create_llm_client(
    campaign_config: CampaignConfig,
) -> tuple[LLMClientBase, str]:
    """Create LLM client + model from ``campaign_config.optimizer_llm``."""
    from promptpotter.infrastructure.llm.client import get_llm_client

    llm = campaign_config.optimizer_llm
    return get_llm_client(llm.provider), llm.model or ""
