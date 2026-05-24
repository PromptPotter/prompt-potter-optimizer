"""Campaign configuration — CampaignConfig Pydantic model, pipeline setup, LLM factory.

Backend-specific experiment-data extraction lives in
:mod:`promptpotter.connectors`; ``bootstrap`` looks up a connector by name
and reads ``connector.extract_experiment(extract)``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from promptpotter.config.settings import POBB_DEFAULT_EPSILON

if TYPE_CHECKING:
    from promptpotter.application.bootstrap.session import Session
    from promptpotter.domain.sample import Sample
    from promptpotter.infrastructure.llm import LLMClientBase

logger = logging.getLogger(__name__)

__all__ = [
    "CampaignConfig",
    "DiffScope",
    "ExplorationConfig",
    "OptimizationConfig",
    "OptimizerLLMConfig",
    "PreflightWarning",
    "configure_and_apply_pipeline",
    "create_llm_client",
    "load_campaign_config",
    "run_preflight_checks",
]


class DiffScope(StrEnum):
    """Resume-time diff classification.

    - ``NONE``: identical configs.
    - ``POLICY_ONLY``: decision knobs differ (PoBB ε/n_min, patience, thresholds, n_variants).
      Past measurements + candidates still valid; new policy governs unevaluated rounds.
    - ``DATA_AFFECTING``: a field that shapes the data trace differs (JobSearchPoint inputs,
      scoring, optimizer LLM). Cached measurements may not apply — resume runs divergence detection.
    """

    NONE = "none"
    POLICY_ONLY = "policy_only"
    DATA_AFFECTING = "data_affecting"


# Subtree entries stop the diff walk at that depth. Unknown paths fall back to DATA_AFFECTING (safe).
_FIELD_SCOPES: dict[tuple[str, ...], Literal["policy", "data"]] = {
    # Top-level
    ("dataset_name",): "data",
    ("sp_budget_ttest",): "policy",
    ("exclude_nodes",): "data",
    ("pipeline_overrides",): "data",
    ("scoring",): "data",
    ("dataset_split",): "policy",  # display-only metadata — no data fork
    # OptimizationConfig
    ("optimization", "max_rounds"): "policy",
    ("optimization", "l1_patience"): "policy",
    ("optimization", "n_variants"): "policy",
    ("optimization", "improvement_threshold"): "policy",
    ("optimization", "l2_patience"): "policy",
    ("optimization", "l3_patience"): "policy",
    ("optimization", "degradation_threshold"): "policy",
    ("optimization", "elimination_n_min"): "policy",
    ("optimization", "pobb_epsilon"): "policy",
    ("optimization", "improvement_significance"): "policy",
    ("optimization", "zero_signal_filter_enabled"): "policy",
    ("optimization", "forbidden_axes_strict"): "policy",
    ("optimization", "exploration"): "policy",  # entire subtree
    # OptimizerLLMConfig — provider/model swap changes the L1/L2/L3 candidate distribution → data.
    ("optimizer_llm", "provider"): "data",
    ("optimizer_llm", "model"): "data",
}


def _diff_paths(
    active: Any,
    frozen: Any,
    prefix: tuple[str, ...] = (),
) -> list[tuple[str, ...]]:
    """Diff paths between *active* and *frozen*; stops at any `_FIELD_SCOPES` entry (subtree-as-unit)."""
    if prefix in _FIELD_SCOPES:
        return [prefix] if active != frozen else []
    if isinstance(active, dict) or isinstance(frozen, dict):
        a = active if isinstance(active, dict) else {}
        f = frozen if isinstance(frozen, dict) else {}
        out: list[tuple[str, ...]] = []
        for key in set(a.keys()) | set(f.keys()):
            out.extend(_diff_paths(a.get(key), f.get(key), (*prefix, key)))
        return out
    return [prefix] if active != frozen else []


class ExplorationConfig(BaseModel):
    """Round-level Rasch IRT — one posterior fit per round drives `select_round_subset` + the heatmap."""

    model_config = ConfigDict(extra="forbid")

    seed_heatmap_from_archive: bool = Field(
        False,
        description=(
            "Round-end hard-sample artifact's Rasch fit folds in archive "
            "observations. δ_s ordering on the heatmap X-axis reflects "
            "cross-cycle evidence."
        ),
    )
    heatmap_show_archive_candidates: bool = Field(
        False,
        description=(
            "When `seed_heatmap_from_archive` is on: include archive candidate "
            "IDs (content_hash[:12]) on the heatmap Y-axis alongside this "
            "cycle's cand_NNN. Off → Y-axis filtered to current cycle only; "
            "archive candidates still contribute to the joint Rasch fit but "
            "stay hidden from display. Ignored when `seed_heatmap_from_archive` "
            "is off."
        ),
    )


class OptimizationConfig(BaseModel):
    """Optimization-loop knobs. `improvement_threshold` + `degradation_threshold` are required (no default)."""

    model_config = ConfigDict(extra="forbid")

    max_rounds: int | None = Field(10, description="Max rounds (None = unlimited)")
    l1_patience: int = Field(3, description="Stop after N consecutive non-improving L1 rounds")
    n_variants: int = Field(5, description="Candidates per round")
    improvement_threshold: float = Field(..., description="Min accuracy delta")

    l2_patience: int | None = Field(2)
    l3_patience: int | None = Field(1)
    degradation_threshold: float = Field(...)

    elimination_n_min: int = Field(
        6,
        description="Minimum queries before PoBB starts firing (floor on n for "
        "the Normal-CLT posterior to be meaningful).",
    )
    pobb_epsilon: float = Field(
        POBB_DEFAULT_EPSILON,
        description="Stop a candidate when its posterior probability of being the "
        "round's best drops below this threshold. Default 5%; smaller → fewer stops.",
    )

    improvement_significance: float = Field(
        1.0,
        description="One-sided proportion-test threshold for declaring a round "
        "IMPROVED. The challenger must beat origin by `improvement_threshold` AND "
        "score at least `elimination_n_min` samples AND yield p < this. Smaller = "
        "stricter. Default 1.0 disables the gate (promote on observed lift only); "
        "set lower (e.g. 0.10) to require statistical significance for ablation runs.",
    )

    zero_signal_filter_enabled: bool = Field(False)

    forbidden_axes_strict: bool = Field(
        True,
        description=(
            "Strict mode: any L1 candidate that proposes a change to "
            "``PARAM_FORBIDDEN_KEYS`` (``model``, ``provider`` — operator-fixed "
            "at the dataset overlay) is rejected at parse time with a "
            "``ValidationFailure(reason='forbidden_axis')``. The candidate "
            "deterministically skips ``score_search_point()`` (no /matches "
            "spend) and lands as ``SKIPPED_VALIDATION`` with synthetic-0 "
            "fitness — same machinery as a malformed L1 output, just gated "
            "by policy rather than schema-shape. Default on while the "
            "operator-fixed axes are still operator-fixed; flip to ``false`` "
            "for ablation experiments that intentionally vary the model. "
            "Soft detection (the ``forbidden_axes_honored`` behavior check) "
            "stays on regardless — strict mode is the spend-saver."
        ),
    )

    exploration: ExplorationConfig = Field(default_factory=ExplorationConfig)


class OptimizerLLMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field("groq")
    model: str | None = Field(None)


class DatasetSplit(BaseModel):
    """Train/test fold sizes — display metadata. `train` is the bank; `test` stays off-bank, on-demand."""

    model_config = ConfigDict(extra="forbid")

    train: int = Field(description="Training-bank fold size — the cache.json row count")
    test: int = Field(description="Held-out test fold size — not in the bank or the table")


class CampaignConfig(BaseModel):
    """Top-level user-authored campaign configuration (``datasets/{name}/campaign.json``)."""

    model_config = ConfigDict(extra="forbid")

    dataset_name: str = Field("")
    sp_budget_ttest: int = Field(
        20,
        description="Per-round eval budget — how many samples each candidate is "
        "scored on per round. The full train split is the bank; each round the "
        "CAT picker (`select_round_subset`) selects this many informative "
        "samples from it. Not the dataset/pool size.",
    )
    exclude_nodes: list[str] = Field(default_factory=list)
    pipeline_overrides: dict[str, Any] = Field(default_factory=dict)
    scoring: str | dict[str, str] | None = Field(None)
    dataset_split: DatasetSplit | None = Field(
        None,
        description="Canonical train/test fold sizes for the dashboard footer. "
        "None when the dataset declares no split.",
    )

    optimization: OptimizationConfig
    optimizer_llm: OptimizerLLMConfig = Field(default_factory=OptimizerLLMConfig)

    def classify_diff_against(self, frozen: dict[str, Any]) -> tuple[DiffScope, list[str]]:
        """Classify diff vs *frozen*; returns `(scope, dotted_paths)`. Unknown paths warn + classify DATA."""
        active = self.model_dump(mode="json")
        diffs = _diff_paths(active, frozen)
        if not diffs:
            return DiffScope.NONE, []
        has_data = False
        diff_strs: list[str] = []
        for path in diffs:
            scope = _FIELD_SCOPES.get(path)
            if scope is None:
                logger.warning(
                    "classify_diff_against: unclassified config path %r — "
                    "treating as DATA_AFFECTING. Add an entry to _FIELD_SCOPES "
                    "in promptpotter/application/config.py to silence this.",
                    ".".join(path),
                )
                has_data = True
            elif scope == "data":
                has_data = True
            diff_strs.append(".".join(path))
        if has_data:
            return DiffScope.DATA_AFFECTING, diff_strs
        return DiffScope.POLICY_ONLY, diff_strs


def load_campaign_config(raw: dict[str, Any] | CampaignConfig) -> CampaignConfig:
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


def _check_sp_budget_vs_dataset(
    config: CampaignConfig, dataset: list[Sample]
) -> PreflightWarning | None:
    n = config.sp_budget_ttest
    m = len(dataset)
    if m > 0 and n > m:
        return PreflightWarning(
            code="sp_budget_exceeds_dataset",
            title=f"per-round eval budget sp_budget_ttest ({n}) exceeds bank size ({m})",
            detail=(
                f"The bank (full train split) has only {m} samples, so each round "
                f"scores on all {m}. Lower sp_budget_ttest to {m} or below, or grow "
                f"the dataset, to give the CAT picker a bank to select from."
            ),
        )
    return None


def run_preflight_checks(config: CampaignConfig, dataset: list[Sample]) -> list[PreflightWarning]:
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
) -> dict[str, Any]:
    """Build pipeline identity, apply filtered schema + overrides onto *session*."""
    from promptpotter.application.datasets import (
        has_dataset_prompts,
        load_dataset_node_overlay,
        load_node_prompt,
    )
    from promptpotter.infrastructure.backend import extract_pipeline_config

    pipeline_schema = session.pipeline_schema
    experiment_extract: dict[str, Any] = session.experiment_extract
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

    valid_overrides: dict[str, dict[str, Any]] = {}
    dataset_name = campaign_config.dataset_name or (session.dataset_name or "")

    # Per-dataset overlay from `datasets/{name}/pipeline.json::nodes.{name}.config` — sparse
    # overrides on backend defaults (e.g. AIME → OpenRouter+Mistral).
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

    # Starting prompts from `datasets/{name}/prompts/[<node>|default].json`, per prompt-bearing node.
    if dataset_name and filtered and has_dataset_prompts(dataset_name):
        prompt_nodes = [n for n in filtered.prompt_node_names() if n in active]
        for pnode in prompt_nodes:
            template = load_node_prompt(dataset_name, pnode, "default")
            valid_overrides.setdefault(pnode, {})["prompt"] = template.render()
            log(f"Starting prompt: {dataset_name}/prompts/[{pnode}|default].json → {pnode}")

    pipeline_params: dict[str, Any] = (
        filtered.to_pipeline_params() if filtered else {"steps": active}
    )
    # Overrides + starting prompt land on the sparse wire payload, never on `current_config`.
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
