"""Campaign configuration — CampaignConfig Pydantic model, pipeline setup, LLM factory.

Backend-specific experiment-data extraction lives in
:mod:`promptpotter.connectors`; ``bootstrap`` looks up a connector by name
and reads ``connector.extract_experiment(extract)``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from promptpotter.application.bootstrap import Session
    from promptpotter.infrastructure.llm import LLMClientBase

logger = logging.getLogger(__name__)

__all__ = [
    "CampaignConfig",
    "ExplorationConfig",
    "OptimizationConfig",
    "OptimizerLLMConfig",
    "PreflightWarning",
    "configure_and_apply_pipeline",
    "create_llm_client",
    "load_campaign_config",
    "run_preflight_checks",
]


class ExplorationConfig(BaseModel):
    """Round-level Rasch IRT — fits one posterior per round, used for both
    scoring-set evolution (in-memory swap of understood ↔ high-info samples)
    and the round-end hard-sample heatmap rendered into ``log.md``.

    Replaces the prior split between ``ScoringSetConfig`` and
    ``HardSampleSorterConfig`` — they consumed the same Rasch fit, so they
    share one config block and one posterior object.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        True,
        description="Master switch — true = Rasch + KG scoring-set evolution active. "
        "Independent of ``hard_sample_sorter_enabled``: a disabled exploration loop "
        "still produces the round-end hard-sample heatmap if requested.",
    )
    swap_out_delta_se: float = Field(
        0.7,
        description="Swap-out threshold: SE on delta_s below which a sample is 'understood' "
        "(~95% CI half-width of ~1.4 logits). Sized so the swap fires round 1->2 on the "
        "typical 20-sample / 5-candidate budget.",
    )
    swap_in_kg_threshold: float = Field(
        0.01,
        description="Swap-in threshold: minimum sample KG to be eligible.",
    )
    max_swaps_per_round: int = Field(3, description="Cap on scoring-set churn per round.")
    cold_start_prior_sigma: float = Field(
        1.5,
        description="Sigma on the N(0, sigma²) theta prior when no observations yet.",
    )
    hard_sample_sorter_enabled: bool = Field(
        True,
        description="Render the candidate-by-sample heatmap section inside log.md at "
        "round-end. Independent of ``enabled``.",
    )
    top_k_candidates: int = Field(
        40,
        description="Cap on persisted candidate axis (θ_c desc) in the heatmap.",
    )
    top_k_samples: int = Field(
        40,
        description="Cap on persisted sample axis (δ_s desc) in the heatmap.",
    )


class OptimizationConfig(BaseModel):
    """Optimization-loop knobs.

    Required per-dataset fields (no default — Pydantic raises if missing): every
    ``datasets/*/campaign.json`` must declare ``improvement_threshold``,
    ``max_failures``, ``degradation_threshold``.

    System invariants (defaulted, MUST NOT appear in any ``campaign.json``):
    ``enable_l2=True``, ``enable_l3=True``, and
    ``ExplorationConfig.swap_out_delta_se=0.7``.

    Guard test: ``tests/test_campaign_config_validation.py::test_required_optimization_fields_must_be_explicit``.
    """

    model_config = ConfigDict(extra="forbid")

    max_rounds: int | None = Field(10, description="Max rounds (None = unlimited)")
    l1_patience: int = Field(3, description="Stop after N consecutive non-improving L1 rounds")
    n_variants: int = Field(5, description="Candidates per round")
    creativity: float = Field(0.7, description="Temperature for candidate generation")
    improvement_threshold: float = Field(..., description="Min accuracy delta")
    max_failures: int = Field(..., description="Max failure examples fed to L1")

    enable_l2: bool = Field(True, description="System invariant — L2 is part of the architecture")
    enable_l3: bool = Field(True, description="System invariant — L3 is part of the architecture")
    l2_patience: int | None = Field(2)
    l3_patience: int | None = Field(1)
    l2_temperature: float = Field(0.3)
    l3_temperature: float = Field(0.5)

    degradation_threshold: float = Field(...)

    elimination_n_min: int = Field(
        4,
        description="Minimum queries before PoBB starts firing (floor on n for "
        "the Normal-CLT posterior to be meaningful).",
    )
    pobb_epsilon: float = Field(
        0.05,
        description="Stop a candidate when its posterior probability of being the "
        "round's best drops below this threshold. Default 5%; smaller → fewer stops.",
    )
    pobb_lock_in: float = Field(
        0.95,
        description="Lock in the current candidate as round leader and terminate "
        "the round when its posterior P(best) reaches this threshold. Default 95%; "
        "1.0 disables lock-in. Saves compute when the leader is already confirmed.",
    )
    pobb_lock_in_n_min: int = Field(
        8,
        description="Minimum queries before PoBB lock-in can fire. Higher than "
        "elimination_n_min because locker-in commits the round-winner — needs more "
        "samples for posterior stability than the loser-elimination floor.",
    )

    max_consecutive_errors: int = Field(3)
    hard_cap: int = Field(100)

    stale_data_load_protocol: list[str] = Field(
        default_factory=lambda: ["rerun", "samplescan", "sampleswitch"]
    )

    zero_signal_filter_enabled: bool = Field(False)
    zero_signal_filter_min_observations: int = Field(5)

    exploration: ExplorationConfig = Field(default_factory=ExplorationConfig)


class OptimizerLLMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field("groq")
    model: str | None = Field(None)
    temperature: float = Field(0.4)
    max_tokens: int = Field(2000)


class CampaignConfig(BaseModel):
    """Top-level user-authored campaign configuration (``datasets/{name}/campaign.json``)."""

    model_config = ConfigDict(extra="forbid")

    dataset_name: str = Field("")
    starting_prompt: str = Field("default")
    sp_budget_ttest: int = Field(20)
    exclude_nodes: list[str] = Field(default_factory=list)
    pipeline_overrides: dict = Field(default_factory=dict)
    scoring: str | dict[str, str] | None = Field(None)

    optimization: OptimizationConfig
    optimizer_llm: OptimizerLLMConfig = Field(default_factory=OptimizerLLMConfig)


def load_campaign_config(raw: dict | CampaignConfig) -> CampaignConfig:
    """Normalize raw dict / Pydantic input into a validated ``CampaignConfig``."""
    if isinstance(raw, CampaignConfig):
        return raw
    return CampaignConfig.model_validate(raw)


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


def configure_and_apply_pipeline(
    session: Session,
    campaign_config: CampaignConfig,
    *,
    log: Callable[[str], None] = logger.info,
) -> dict:
    """Build pipeline identity, apply filtered schema + overrides onto *session*."""
    from promptpotter.application.datasets.datasets import (
        has_dataset_prompts,
        load_dataset_node_overlay,
        load_node_prompt,
    )
    from promptpotter.infrastructure.backend import extract_pipeline_config

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
    dataset_name = campaign_config.dataset_name or (session.dataset_name or "")

    # Per-dataset operator overlay from ``datasets/{name}/pipeline.json::
    # nodes.{name}.config`` — sparse overrides on top of backend defaults
    # (e.g. AIME runs through OpenRouter on Mistral instead of Groq+gpt-oss).
    if dataset_name:
        for node, cfg in load_dataset_node_overlay(dataset_name).items():
            if node in active:
                valid_overrides.setdefault(node, {}).update(cfg)

    if overrides:
        for key, value in overrides.items():
            if isinstance(value, dict) and key in active:
                valid_overrides.setdefault(key, {}).update(value)
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
    starting_name = campaign_config.starting_prompt or "default"
    if dataset_name and filtered and has_dataset_prompts(dataset_name):
        prompt_nodes = [n for n in filtered.prompt_node_names() if n in active]
        for pnode in prompt_nodes:
            template = load_node_prompt(dataset_name, pnode, starting_name)
            valid_overrides.setdefault(pnode, {})["prompt"] = template.render()
            log(f"Starting prompt: {dataset_name}/prompts/[{pnode}|{starting_name}].json → {pnode}")

    pipeline_params: dict[str, Any] = (
        filtered.to_pipeline_params() if filtered else {"steps": active}
    )
    # Operator's ``pipeline_overrides`` + the resolved starting prompt land
    # directly on the sparse wire payload — never into ``current_config``,
    # which stays the backend's source of truth.
    for node, cfg in valid_overrides.items():
        pipeline_params.setdefault(node, {}).update(cfg)

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
    from promptpotter.infrastructure.llm import get_llm_client

    llm = campaign_config.optimizer_llm
    return get_llm_client(llm.provider), llm.model or ""
