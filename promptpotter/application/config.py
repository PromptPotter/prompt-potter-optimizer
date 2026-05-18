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

if TYPE_CHECKING:
    from promptpotter.application.bootstrap.session import Session
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
    """How a CampaignConfig diff should be handled on resume.

    - ``NONE``: no differences.
    - ``POLICY_ONLY``: only decision-policy knobs differ (PoBB ε / boost / δ
      / n_min, patience, thresholds, n_variants). Past per-sample
      measurements and L1 candidates are still valid; past decision records
      stay as the audit of what the policy decided at the time. New policy
      governs unevaluated rounds. Safe to resume in-place — no fork.
    - ``DATA_AFFECTING``: at least one field that influences the data trace
      differs (JobSearchPoint inputs, scoring formula, optimizer LLM). Cached
      measurements may not apply; resume must run divergence detection (and
      fork if the operator opted into ``--fork-on-divergence``).
    """

    NONE = "none"
    POLICY_ONLY = "policy_only"
    DATA_AFFECTING = "data_affecting"


# Classification of every CampaignConfig leaf path. Subtree entries (e.g.
# ``("optimization", "exploration")``) apply to all descendants and stop the
# diff walk at that depth. Unknown paths default to DATA_AFFECTING with a
# warning — a newly-added knob is treated as data-affecting until classified.
_FIELD_SCOPES: dict[tuple[str, ...], Literal["policy", "data"]] = {
    # Top-level
    ("dataset_name",): "data",
    ("sp_budget_ttest",): "policy",
    ("exclude_nodes",): "data",
    ("pipeline_overrides",): "data",
    ("scoring",): "data",
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
    ("optimization", "pobb_predictable_tail_delta"): "policy",
    ("optimization", "pobb_predictable_tail_boost"): "policy",
    ("optimization", "improvement_significance"): "policy",
    ("optimization", "zero_signal_filter_enabled"): "policy",
    ("optimization", "forbidden_axes_strict"): "policy",
    ("optimization", "exploration"): "policy",  # entire subtree
    # OptimizerLLMConfig — changing the optimizer LLM provider or model would
    # produce different L1/L2/L3 candidates for future rounds; treat as
    # data-affecting so the operator gets a fork on lineage.
    ("optimizer_llm", "provider"): "data",
    ("optimizer_llm", "model"): "data",
}


def _diff_paths(
    active: Any,
    frozen: Any,
    prefix: tuple[str, ...] = (),
) -> list[tuple[str, ...]]:
    """Walk active vs frozen and return paths where they differ.

    Stops descending at any path registered in :data:`_FIELD_SCOPES` so that
    subtree entries classify the whole subtree as one unit. ``None`` on
    either side is treated as ``{}`` when the other side is a dict — a new
    config section added since the frozen snapshot was taken should diff at
    the leaf level (where the classifications live), not at the parent.
    """
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
    """Round-level Rasch IRT — fits one posterior per round, used for both
    scoring-set evolution (in-memory swap of understood ↔ high-info samples)
    and the round-end hard-sample heatmap rendered into ``log.md``.
    """

    model_config = ConfigDict(extra="forbid")

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

    seed_initial_scoring_set_from_archive: bool = Field(
        False,
        description=(
            "Round-0 scoring set is picked from archive δ_s (hardest first) "
            "instead of `sample_dataset` dataset order. Falls back to dataset "
            "order when the archive has fewer than `elimination_n_min` "
            "observations for this backend."
        ),
    )
    seed_evolve_from_archive: bool = Field(
        False,
        description=(
            "`evolve_scoring_set` folds archive observations into its per-round "
            "Rasch fit alongside live `build_observations(rounds)`. Affects "
            "swap-out and swap-in decisions from round 1 onward."
        ),
    )
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
    """Optimization-loop knobs.

    Required per-dataset fields (no default — Pydantic raises if missing): every
    ``datasets/*/campaign.json`` must declare ``improvement_threshold`` and
    ``degradation_threshold``.

    Guard test: ``tests/test_campaign_config_validation.py::test_required_optimization_fields_must_be_explicit``.
    """

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
        0.05,
        description="Stop a candidate when its posterior probability of being the "
        "round's best drops below this threshold. Default 5%; smaller → fewer stops.",
    )
    pobb_predictable_tail_delta: float = Field(
        1.0,
        description="Rasch |δ| threshold above which a remaining sample is treated as "
        "predictable (always-hit or always-miss given prior data). The hard-sample "
        "sorter pushes high-|δ| samples to the tail, so the predictable-tail fraction "
        "rises monotonically as evaluation progresses — see ``pobb_predictable_tail_boost``.",
    )
    pobb_predictable_tail_boost: float = Field(
        3.0,
        description="Multiplier on the ``pobb_epsilon`` loser-elimination threshold "
        "when the candidate's remaining tail is all predictable. ε_eff = ε * (1 + boost *"
        "predictable_tail_fraction). With default ε=0.05 and boost=3.0, an all-predictable "
        "tail loosens ε to 0.20 — eliminates candidates with P(best)<20% instead of <5%. "
        "Set 0.0 to disable the δ-aware scaling and use a flat ε.",
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


class CampaignConfig(BaseModel):
    """Top-level user-authored campaign configuration (``datasets/{name}/campaign.json``)."""

    model_config = ConfigDict(extra="forbid")

    dataset_name: str = Field("")
    sp_budget_ttest: int = Field(20)
    exclude_nodes: list[str] = Field(default_factory=list)
    pipeline_overrides: dict = Field(default_factory=dict)
    scoring: str | dict[str, str] | None = Field(None)

    optimization: OptimizationConfig
    optimizer_llm: OptimizerLLMConfig = Field(default_factory=OptimizerLLMConfig)

    def classify_diff_against(self, frozen: dict) -> tuple[DiffScope, list[str]]:
        """Classify the diff between this active config and a frozen snapshot.

        Returns ``(scope, diffed_paths)`` where ``diffed_paths`` are
        dot-strings suitable for logging. See :class:`DiffScope` for
        semantics. Unknown leaf paths trigger a warning and classify as
        DATA_AFFECTING (safe default).
        """
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
    from promptpotter.application.datasets import (
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

    # Starting-point prompts from ``datasets/{name}/prompts/[<node>|default].json``
    # injected per prompt-bearing node.
    if dataset_name and filtered and has_dataset_prompts(dataset_name):
        prompt_nodes = [n for n in filtered.prompt_node_names() if n in active]
        for pnode in prompt_nodes:
            template = load_node_prompt(dataset_name, pnode, "default")
            valid_overrides.setdefault(pnode, {})["prompt"] = template.render()
            log(f"Starting prompt: {dataset_name}/prompts/[{pnode}|default].json → {pnode}")

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
