"""Campaign configuration — CampaignConfig Pydantic model, pipeline setup, LLM factory.

Backend-specific experiment-data extraction lives in
:mod:`promptpotter.connectors`; ``bootstrap`` looks up a connector by name
and reads ``connector.extract_experiment(extract)``.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from promptpotter.config.settings import POBB_DEFAULT_EPSILON, PROMPT_STRING_FIELDS
from promptpotter.domain.pipeline_schema import NodeSearchNarrowing
from promptpotter.shared.errors import PayloadInvalidError

if TYPE_CHECKING:
    from pathlib import Path

    from promptpotter.application.bootstrap.session import Session
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.run_records import CycleSeed
    from promptpotter.domain.sample import Sample

logger = logging.getLogger(__name__)

__all__ = [
    "CampaignConfig",
    "ExplorationConfig",
    "OptimizationConfig",
    "PreflightWarning",
    "apply_inherited_overlay",
    "apply_node_overlay",
    "configure_and_apply_pipeline",
    "load_campaign_config",
    "resolve_pipeline_config_params",
    "run_preflight_checks",
]


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
    enable_2pl_graduation: bool = Field(
        True,
        description=(
            "Allow the per-cycle difficulty ruler to graduate from 1PL (difficulty "
            "δ only) to 2PL (per-sample discrimination aₛ too) when a data-rich, "
            "genuinely-discriminating dataset wins held-out cross-validation. The "
            "switch is gated — cold/non-discriminating datasets stay 1PL — so this "
            "only ever changes the ruler where 2PL provably fits better out-of-sample; "
            "it can never regress a dataset. Off → always 1PL (the slice-2 behaviour)."
        ),
    )


class SelectionMechanisms(BaseModel):
    """Hard-sample sorting & selection — how each round's scoring subset is chosen
    and ordered. Turn BOTH off to freeze the sample basis at campaign start: one
    fixed subset, fixed order, identical for every round and candidate."""

    model_config = ConfigDict(extra="forbid")

    per_round_resubset: bool = Field(
        True,
        description=(
            "Re-pick the most-informative scoring subset every round from the "
            "train bank (adaptive Rasch selection). On (default) — safe because "
            "every cross-round comparator (election, PoBB, c0_ok, the stall ladder) "
            "measures on one fixed θ ruler, so a shifting per-round subset stays "
            "comparable — but it is warm-gated: while the δ ruler is still cold (a "
            "fresh dataset's early rounds) the subset stays FROZEN to the campaign-start "
            "selection, so those rounds are comparable AND concentrate measurements to "
            "warm the ruler fastest; it thaws to adaptive once the ruler locks. Off → "
            "the campaign-start selection (deterministic bank prefix) for every round, "
            "the whole campaign."
        ),
    )
    online_reorder: bool = Field(
        True,
        description=(
            "Within a round, re-rank the unscored samples after every measurement "
            "(online 1PL-Rasch adaptive queue) and stream the live order to the "
            "dashboard. Off → each candidate measures the subset in fixed "
            "insertion order, identical across candidates, no live re-sort emitted."
        ),
    )


class EliminationMechanisms(BaseModel):
    """Early-abort / candidate-elimination rules that stop measuring a candidate —
    or gate round promotion — before the full sample budget is spent. Off → the
    mechanism never fires; candidates run their full budget. Numeric tuning for an
    enabled mechanism lives on the sibling `OptimizationConfig` fields."""

    model_config = ConfigDict(extra="forbid")

    epsilon_elimination: bool = Field(
        True,
        description=(
            "PoBB ε-stop: drop a candidate once its posterior probability of being "
            "the round's best falls below `pobb_epsilon`. The main loser-elimination rule."
        ),
    )
    deterministic_dominance: bool = Field(
        True,
        description=(
            "Abort a candidate when its best POSSIBLE final hit count (current hits "
            "+ remaining budget) is already below the incumbent's — it cannot "
            "mathematically catch up. Fires before the ε math."
        ),
    )
    equivalence_elimination: bool = Field(
        True,
        description=(
            "Practical-equivalence (futility) stop: abort a candidate once it is "
            "improbable (< `pobb_epsilon`, at the candidate's OWN observed hit rate) "
            "that it will clear the round's ADOPTION bar — the seed's hits plus "
            "`improvement_threshold`. Kills 'moderately the same' candidates that a "
            "loser-only gate lets ride to the full budget: a tie can never be adopted, "
            "so confirming it on the whole panel is wasted spend. The probabilistic "
            "sibling of `deterministic_dominance` (same paired-hit arithmetic on the "
            "shared sample universe, observed rate instead of the optimistic rate=1 "
            "corner, adoption bar instead of the bare seed)."
        ),
    )
    degradation_fatal_fastpath: bool = Field(
        True,
        description=(
            "End a candidate at the first FATAL sample (empty response, "
            "content-filtered, structurally broken) without spending the rest of "
            "its budget. Active while the degradation check runs "
            "(`degradation_threshold` > 0); the rate-based check stays governed by "
            "that threshold."
        ),
    )
    leader_lock_in: bool = Field(
        False,
        description=(
            "Crown a decisive leader EARLY: stop measuring a candidate as the winner "
            "once its P(best) against every prior reaches `pobb_lock_in`, before it "
            "spends its full budget. Off (default) → only losers are eliminated "
            "early; a leader measures its full budget."
        ),
    )


class MechanismConfig(BaseModel):
    """Pluggable orchestration mechanisms, grouped by kind. Each toggle turns one
    mechanism on/off; numeric tuning for an enabled mechanism lives on its
    `OptimizationConfig` field (`pobb_epsilon`, `pobb_lock_in`, …). Add a mechanism
    by adding a bool to the right group — it auto-surfaces to the webapp via the
    schema. (Patience-driven L1/L2/L3 escalation is governed separately by
    `l1_patience` / `l2_patience` / `l3_patience`; None disarms L2/L3.)"""

    model_config = ConfigDict(extra="forbid")

    selection: SelectionMechanisms = Field(default_factory=SelectionMechanisms)
    elimination: EliminationMechanisms = Field(default_factory=EliminationMechanisms)


class OptimizationConfig(BaseModel):
    """Optimization-loop knobs. `improvement_threshold` + `degradation_threshold` are required (no default)."""

    model_config = ConfigDict(extra="forbid")

    max_rounds: int | None = Field(50, description="Max rounds (None = unlimited)")
    l1_patience: int = Field(3, description="Stop after N consecutive non-improving L1 rounds")
    n_variants: int = Field(5, description="Candidates per round")
    improvement_threshold: float = Field(..., description="Min accuracy delta")

    optimizer_set: str = Field(
        "",
        description=(
            "Which optimizer meta-prompt set this cycle uses. Empty (default) → the "
            "standard `datasets/_optimizer/` task-tuning loop. `meta` → the L4 outer "
            "set `datasets/_optimizer_meta/prompts.json`, whose L1 emits per-node "
            "edits to the INNER optimizer's meta-prompts (`pipeline_params_override`) "
            "instead of tuning its own template. Applied per-cycle at the runner seam "
            "through the same per-node override channel the inner runner uses, so an "
            "outer (meta) cycle and the inner (default) cycles it spawns stay isolated "
            "by task. See docs/specs/l4-outer-loop.md § 3."
        ),
    )

    noop_probe: bool = Field(
        False,
        description=(
            "Inject one deliberate NO-OP probe candidate (origin-identical, empty mutation) "
            "into round 1. Its measured delta vs origin IS the backend's run-to-run noise "
            "floor — the yardstick a real candidate's delta must clear before it means "
            "anything. Only worth an arm on a stochastic backend (the L4 inner recursion is "
            "the canonical user); the probe is exempt from the no_op_variant nuke and scored "
            "force_fresh, since a cache replay of an origin-identical config reads a floor of "
            "exactly 0 by construction."
        ),
    )

    l2_patience: int | None = Field(2)
    l3_patience: int | None = Field(1)
    degradation_threshold: float = Field(...)

    elimination_n_min: int = Field(
        6,
        description="Minimum samples before PoBB starts firing (floor on n for "
        "the Normal-CLT posterior to be meaningful).",
    )
    pobb_epsilon: float = Field(
        POBB_DEFAULT_EPSILON,
        description="Stop a candidate when its posterior probability of being the "
        "round's best drops below this threshold. Default 5%; smaller → fewer stops.",
    )
    pobb_lock_in: float = Field(
        0.95,
        description="Leader lock-in threshold — the P(best) at which a leading "
        "candidate is crowned early and stops measuring. Applies only when "
        "`mechanisms.elimination.leader_lock_in` is on (that bool owns the on/off); "
        "this is purely the threshold. Lower = lock in sooner on less evidence.",
    )
    pobb_lock_in_n_min: int = Field(
        8,
        description="Samples-floor for lock-in — a leader can only lock in after at "
        "least this many measurements. Applies only when "
        "`mechanisms.elimination.leader_lock_in` is on.",
    )

    spend_budget_usd: float | None = Field(
        0.025,
        description=(
            "Halt this cycle when cumulative spend (optimizer + backend) ≥ this "
            "value in USD. CLI ``--spend-budget`` overrides the config value when "
            "both are supplied. Default ≈ a 5-round run on the free-backend setup "
            "(measured ~$0.019) with headroom; raise ``max_rounds`` and let this be "
            "the binding limit. ``None`` disarms the USD ceiling. Tenant-wide "
            "enforcement is M12 / JobRegistry work; this gate halts the current "
            "cycle at the next round boundary."
        ),
    )

    token_budget: int | None = Field(
        210_000,
        description=(
            "Halt this cycle when cumulative tokens (optimizer + backend, input + "
            "output) ≥ this value. The model-portable twin of ``spend_budget_usd``: "
            "a free backend reports $0 so the USD ceiling sees only optimizer cost, "
            "while this counts backend work too. Default ≈ a 5-round run (measured "
            "~158k tokens) with headroom. Whichever of the two ceilings trips first "
            "halts the cycle. ``None`` disarms the token ceiling."
        ),
    )

    origin_gate: Literal["strict", "critical_only", "off"] = Field(
        "strict",
        description=(
            "Halt after round 0 when the origin's degradation verdict is "
            "non-healthy, instead of optimizing against a broken floor — the "
            "common failure while bringing up a new connector. ``strict`` "
            "(default) halts on ``critical`` or ``degraded``; ``critical_only`` "
            "halts only on a structurally-broken origin; ``off`` disarms the "
            "gate. The operator overrides knowingly with a plain ``resume`` — "
            "round 0 is already on disk, so the loop skips the gate and goes "
            "straight to L1."
        ),
    )

    forbidden_axes_strict: bool = Field(
        True,
        description=(
            "The single model-optimizability bit. When on (default), the "
            "``model`` axis is ABSENT from the optimizer surface — "
            "``PipelineSchema.node_param_keys`` drops ``PARAM_FORBIDDEN_KEYS`` "
            "(``model``, ``provider``), so the param catalogue never advertises "
            "them and ``build_l1_output_schema`` never declares them; the LLM "
            "cannot emit a key the schema omits. ``validate_overrides`` is the "
            "lone deterministic backstop for a provider that leaks the key past "
            "its own schema — it rejects the candidate with "
            "``ValidationFailure(reason='forbidden_axis')`` (synthetic-0, no "
            "/matches spend). The dataset still OWNS the model value via "
            "``nodes.{node}.config.model``; this flag governs only whether the "
            "OPTIMIZER may search it. Flip to ``false`` for ablation runs that "
            "sweep model identity — the ``model`` axis is then synthesized onto "
            "each LLM node with ``available_models`` as its value space."
        ),
    )

    rebase_capability: bool = Field(
        True,
        description=(
            "L2/L3 fork_proposal emission. When True, the ``rebase_capability`` "
            "injection renders the rare-escape-hatch instruction into L2 + L3 "
            "prompts and the runner auto-mints a sibling cycle on each fired "
            "fork_proposal (capped at ``MAX_AUTO_REBASES`` per session). When "
            "False, the injection renders empty — L2/L3 prompts contain no "
            "fork_proposal guidance, the LLM never emits one, the runner's "
            "rebase loop never fires. Flip to ``false`` for ablation runs "
            "that need a fixed-trajectory baseline without the rebase prompt "
            "text distorting the input. The schema field itself is invariant "
            "(default None) so on-disk audit shape doesn't drift between modes."
        ),
    )

    terminate_capability: bool = Field(
        True,
        description=(
            "L2/L3 terminate_proposal emission. When True, the "
            "``terminate_capability`` injection renders the stop-the-cycle "
            "instruction into L2 + L3 prompts; an emitted terminate_proposal "
            "raises ``StopReason.ABORT`` and the cycle finalizes HALTED on the "
            "current cycle_id (no fork). The intended user is an unrecoverable "
            "upstream fault — e.g. an evidence-starved enricher (backend quota "
            "exhausted) — that no framing refinement or replan can fix. When "
            "False, the injection renders empty — L2/L3 prompts contain no "
            "terminate guidance and the LLM never emits one. Flip to ``false`` "
            "for an ablation run whose input distribution must match a "
            "no-terminate baseline. The schema field is invariant (default "
            "None) so on-disk audit shape doesn't drift between modes."
        ),
    )

    exploration: ExplorationConfig = Field(default_factory=ExplorationConfig)
    mechanisms: MechanismConfig = Field(default_factory=MechanismConfig)


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
        "adaptive queue mechanism (`select_round_subset`) selects this many "
        "informative samples from it. Not the dataset/pool size.",
    )
    exclude_nodes: list[str] = Field(default_factory=list)
    pipeline_overrides: dict[str, Any] = Field(default_factory=dict)
    optimizer_narrowing: dict[str, NodeSearchNarrowing] = Field(
        default_factory=dict,
        description="Per-node narrowing of the dataset-declared optimizer search "
        "space — the per-campaign param-lock + allowed-values lever beside "
        "`exclude_nodes` (whole node) and `optimization.forbidden_axes_strict` "
        "(model/provider). Subsets only; applied onto the schema by "
        "`PipelineSchema.narrow` at pipeline setup.",
    )
    scoring: str | dict[str, str] | None = Field(None)
    headline_metric: Literal["accuracy", "composite", "ability"] = Field(
        "accuracy",
        description="Which fitness number headlines the operator's text surfaces "
        "(lineage node value, Best tile, sidebar) by default. DISPLAY config, not "
        "search state — the gate is always difficulty-adjusted ability θ; this only "
        "picks the number the human READS, client-overridable per session. `ability` "
        "shows θ (a logit, jargon) — defaults to `accuracy` so θ is never forced on "
        "an operator who didn't ask for it. Rides the `composite_fitness_formula` "
        "serve path to `dashboard.json::headline_metric`; never on `OptSearchPoint`.",
    )
    dataset_split: DatasetSplit | None = Field(
        None,
        description="Canonical train/test fold sizes for the dashboard footer. "
        "None when the dataset declares no split.",
    )

    optimization: OptimizationConfig


def load_campaign_config(raw: dict[str, Any] | CampaignConfig) -> CampaignConfig:
    """Normalize raw dict / Pydantic input into a validated ``CampaignConfig``."""
    if isinstance(raw, CampaignConfig):
        return raw
    return CampaignConfig.model_validate(raw)


def apply_inherited_overlay(
    config: CampaignConfig,
    frozen_config: dict[str, Any],
    seed: CycleSeed | None,
) -> CampaignConfig:
    """Re-apply the per-campaign overlay onto a config rebuilt from the live dataset.

    Resume/fork rebuild ``config`` from the *live* dataset ``campaign.json`` so
    declaration edits stay drift-detected — but that file never holds the
    per-campaign ``pipeline_overrides`` (origin-floor values) or
    ``optimizer_narrowing`` (param locks), which live only on the frozen
    ``Campaign.config`` snapshot. Without this they silently revert to the dataset
    defaults on every resume/fork (the lock-drop bug). A steered-fork *seed*'s
    ``optimizer_narrowing`` overrides the campaign-wide narrowing per node — the
    cycle-level lock edit. Idempotent when ``frozen_config`` already equals
    ``config`` (dataset-file-gone fallback)."""
    frozen = load_campaign_config(frozen_config)
    narrowing = {**config.optimizer_narrowing, **frozen.optimizer_narrowing}
    if seed is not None:
        narrowing.update(seed.optimizer_narrowing)
    return config.model_copy(
        update={
            "pipeline_overrides": {**config.pipeline_overrides, **frozen.pipeline_overrides},
            "optimizer_narrowing": narrowing,
        }
    )


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
                f"the dataset, to give the adaptive queue mechanism a bank to select from."
            ),
        )
    return None


_PARAMS_B_RE = re.compile(r"(\d+(?:\.\d+)?)\s*b\b", re.IGNORECASE)


def _model_params_b(model_id: str) -> float | None:
    """Best-effort parameter count (billions) parsed from a model id's last path
    segment — ``openai/gpt-oss-120b`` → 120.0, ``mistral-small-3.2-24b`` → 24.0.
    ``None`` when no ``<N>b`` token is present (e.g. ``gpt-4o``), so the inversion
    check below stays silent rather than guessing."""
    segment = model_id.rsplit("/", 1)[-1]
    match = _PARAMS_B_RE.search(segment)
    return float(match.group(1)) if match else None


def _check_optimizer_below_target(target_models: tuple[str, ...]) -> PreflightWarning | None:
    """Warn when the optimizer LLM is *smaller* than a target it optimizes.

    The optimizer is meant to be the strong model that improves the pipeline;
    running it on a model smaller than the target it optimizes is almost always an
    accidental inversion — the bug class behind the email-tagging 20b optimizer
    pin. Both sides must parse a ``<N>b`` size or the check is skipped (no guess).
    The optimizer model is read from the install-global optimizer node config
    (``datasets/_optimizer/pipeline.json``), not a per-campaign copy."""
    from promptpotter.application.optimization.dispatch.llm_call import optimizer_model

    opt_model = optimizer_model()
    opt_b = _model_params_b(opt_model)
    if opt_b is None:
        return None
    bigger = sorted(
        {m for m in target_models if (b := _model_params_b(m)) is not None and b > opt_b}
    )
    if not bigger:
        return None
    return PreflightWarning(
        code="optimizer_below_target",
        title=f"optimizer LLM ({opt_model}) is smaller than the target ({', '.join(bigger)})",
        detail=(
            "The optimizer is the strong model that improves the pipeline; running "
            "it on a model smaller than the target it optimizes is usually an "
            "accidental inversion. Raise the optimizer node `model` in "
            "`datasets/_optimizer/pipeline.json` to a larger tier."
        ),
    )


def _check_config_couplings(config: CampaignConfig) -> list[PreflightWarning]:
    """Knob-collision warnings — config combinations where one knob makes another
    statistical quantity ill-defined or inert. The declared map lives in
    ``config_coupling`` (the single source of truth, also read by the
    ``config_map`` diagnostic + the webapp config-map endpoint); this is its
    pre-run CLI leg. Imported lazily to keep the statistical-constant imports off
    ``config``'s module-load path."""
    from promptpotter.application.config_coupling import check_couplings

    return [
        PreflightWarning(
            code=f"config_coupling.{c.name}",
            title=f"config coupling [{c.severity}]: {', '.join(c.knobs)}",
            detail=f"{c.relation} {c.consequence}",
        )
        for c in check_couplings(config)
    ]


def run_preflight_checks(
    config: CampaignConfig,
    dataset: list[Sample],
    target_models: tuple[str, ...] = (),
) -> list[PreflightWarning]:
    """Run all preflight checks. Pure — no mutation, no I/O.

    ``target_models`` are the resolved per-node target/scoring model ids (from
    ``session.pipeline_params``); empty when the backend owns the model."""
    warnings: list[PreflightWarning] = []
    if (w := _check_sp_budget_vs_dataset(config, dataset)) is not None:
        warnings.append(w)
    if (w := _check_optimizer_below_target(target_models)) is not None:
        warnings.append(w)
    warnings.extend(_check_config_couplings(config))
    return warnings


def apply_node_overlay(base: dict[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    """The ONE shallow per-node overlay merge → a fresh dict (``base`` untouched).

    For each ``node`` in *overlay*: when both the existing and the incoming value are
    dicts, the incoming keys win over the existing (``{**existing, **incoming}``);
    otherwise the incoming value is assigned as-is — so the reserved non-dict
    top-level ``steps`` list can't be spread and simply replaces. Sequential calls
    layer correctly (later overlay > earlier), which is how the resolution chain
    stacks dataset < campaign-override < cycle-seed.

    Shared by the dataset/override resolution here, the cycle-seed overlay
    (``runner/entry.py``) and the L1 candidate override (``optimization/l1
    /population.py`` — which deep-copies its base and drops inactive nodes AROUND
    this call). A RECURSIVE merge (``cli/commands/verify.py::_deep_merge``) is a
    different operation and stays separate."""
    merged = dict(base)
    for node, cfg in overlay.items():
        existing = merged.get(node)
        merged[node] = (
            {**existing, **cfg} if isinstance(existing, dict) and isinstance(cfg, dict) else cfg
        )
    return merged


def resolve_pipeline_config_params(
    active: list[str],
    pipeline_overrides: Mapping[str, Any],
    dataset_dir: Path | None,
) -> dict[str, Any]:
    """The dataset→effective node-config merge, pure: the sparse ``{"steps": active}``
    base layered with the per-dataset overlay (``pipeline.json::nodes.{name}.config``) and
    the campaign's ``pipeline_overrides``, restricted to *active* nodes. Prompts and
    model validation are NOT here — they ride the ``OptSearchPoint`` / live ``Session``.

    This is the SINGLE definition of which node config a cycle id and measurement key hash.
    Shared by :func:`configure_and_apply_pipeline` (which adds prompts + validation + the
    session apply) and the ``GET /origins`` prospective-origin id, so the two can never
    silently diverge. Takes no live ``Session`` — resolvable from disk."""
    from promptpotter.application.datasets import load_dataset_node_overlay

    pipeline_params: dict[str, Any] = {"steps": list(active)}
    if dataset_dir is not None:
        # Per-dataset overlay — sparse overrides on backend defaults (e.g. AIME →
        # OpenRouter+Mistral). `dataset_dir` is tenant-first, so ingested datasets honor it.
        dataset_overlay = {
            node: cfg
            for node, cfg in load_dataset_node_overlay(dataset_dir).items()
            if node in active
        }
        pipeline_params = apply_node_overlay(pipeline_params, dataset_overlay)
    # Campaign overrides layer on top (override > dataset); non-dict / inactive-node
    # entries are dropped here with an operator-visible log, then the survivors merge.
    valid_overrides: dict[str, Any] = {}
    for key, value in pipeline_overrides.items():
        if isinstance(value, dict) and key in active:
            valid_overrides[key] = value
        elif isinstance(value, dict):
            logger.debug(
                "resolve_pipeline_config_params: skipping override for inactive node %r", key
            )
        else:
            logger.warning(
                "resolve_pipeline_config_params: ignoring non-nested override %r=%r "
                '(use {"node_name": {"param": value}} format)',
                key,
                value,
            )
    pipeline_params = apply_node_overlay(pipeline_params, valid_overrides)
    # Connector identity contribution — LAST, never overridable: per-node entries a
    # connector declares as part of measurement identity (Connector.identity_config,
    # e.g. the promptpotter connector's inner-baseline fingerprint). Resolved from
    # the dataset dir's own backend_type so this stays pure-over-disk and both
    # callers (live setup + prospective-origin id) agree by construction.
    if dataset_dir is not None:
        identity = {
            node: cfg
            for node, cfg in _connector_identity_config(dataset_dir).items()
            if node in active
        }
        pipeline_params = apply_node_overlay(pipeline_params, identity)
    return pipeline_params


def _connector_identity_config(dataset_dir: Path) -> dict[str, dict[str, Any]]:
    """The dataset's connector ``identity_config`` contribution, or ``{}``.

    Reads ``backend_type`` from the dataset's ``pipeline.json`` (tolerant — a
    dataset dir without one contributes nothing) and asks the registered
    connector. Import is local to keep ``config.py`` free of a module-level
    connectors dependency."""
    from promptpotter.connectors import CONNECTORS
    from promptpotter.infrastructure.store.io import read_json_tolerant

    raw = read_json_tolerant(dataset_dir / "pipeline.json")
    connector = CONNECTORS.get(str((raw or {}).get("backend_type") or ""))
    if connector is None or connector.identity_config is None:
        return {}
    return connector.identity_config()


def missing_template_vars(rendered: str, declared: list[str]) -> list[str]:
    """Declared `{{vars}}` the backend injects by literal substitution but the rendered prompt drops.

    A prompt-bearing node declares the `{{name}}` placeholders the backend fills
    (query / research evidence / output-schema). If the rendered prompt omits one,
    that injection silently no-ops and the model never sees it. The six-field
    decomposition names (`PROMPT_STRING_FIELDS`) are excluded — some nodes declare
    THOSE as template_variables, but `render()` assembles them; they are never
    `{{substituted}}`. The SINGLE definition of "required placeholder", shared by
    the mint-time setup check and the in-loop L1 candidate guard.
    """
    return [
        v for v in declared if v not in PROMPT_STRING_FIELDS and "{{" + v + "}}" not in rendered
    ]


def _resolve_active_schema(
    pipeline_schema: PipelineSchema | None,
    experiment_extract: dict[str, Any],
    *,
    exclude: list[str],
    narrowing: dict[str, NodeSearchNarrowing],
) -> tuple[list[str], PipelineSchema | None]:
    """Resolve the active node list + the exclude-filtered, campaign-narrowed schema."""
    from promptpotter.infrastructure.backend import extract_pipeline_config

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
    # Campaign search-space narrowing — the per-node param-lock + allowed-values
    # subset, peer to exclude (above) and forbidden_axes_strict. The dataset
    # declares the max; the campaign snapshot may only narrow it.
    if filtered and narrowing:
        filtered = filtered.narrow(narrowing)
    return active, filtered


def _apply_starting_prompts(
    pipeline_params: dict[str, Any],
    *,
    filtered: PipelineSchema,
    active: list[str],
    dataset_dir: Path,
    dataset_name: str,
    log: Callable[[str], None],
) -> None:
    """Load each prompt-bearing node's starting prompt onto the wire payload.

    Fails loud (``PayloadInvalidError``) when a rendered prompt omits a
    ``{{var}}`` the node declares; warns when the dataset ships prompts but no
    active node can carry one. Assumes the caller checked ``has_dataset_prompts``.
    """
    from promptpotter.application.datasets import load_node_prompt

    prompt_nodes = [n for n in filtered.prompt_node_names() if n in active]
    if not prompt_nodes:
        # The dataset ships starting prompts but no active node declares
        # `prompt_info` — so the rendered prompt has nowhere to land and is
        # dropped before the wire. Silent here = every backend call runs with
        # an empty system prompt (the bug that made an ingested dataset score
        # 0% on email-replies). Fail loud: a generation node must advertise
        # `prompt_info` in GET /pipeline (or the dataset overlay).
        logger.warning(
            "configure_pipeline: dataset %r has starting prompts but NO "
            "prompt-bearing node in the active pipeline %s — the prompt will "
            "NOT reach the backend. A generation node must declare `prompt_info`.",
            dataset_name,
            active,
        )
    prompt_info_by_node = {n.name: n.prompt_info for n in filtered.nodes}
    for pnode in prompt_nodes:
        template = load_node_prompt(dataset_dir, pnode, "default")
        rendered = template.render()
        # A prompt-bearing node declares the `{{vars}}` the backend injects by
        # literal substitution (query / research / output-schema). If the rendered
        # prompt omits one, that injection silently no-ops and the model never sees
        # it — the bug that made entity_profiling emit term-not-JSON → NO_RESULT.
        # Fail loud at setup, before a single degraded backend call. Exclude the
        # six-field decomposition names (PROMPT_STRING_FIELDS): some nodes (e.g. the
        # promptpotter-self L4 connector) declare THOSE as template_variables, but
        # `render()` ASSEMBLES them — they are never `{{substituted}}`.
        pinfo = prompt_info_by_node.get(pnode)
        declared = pinfo.template_variables if pinfo else []
        missing = missing_template_vars(rendered, declared)
        if missing:
            raise PayloadInvalidError(
                f"Dataset {dataset_name!r} prompt for node {pnode!r} is missing required "
                f"template variables {missing} — the backend injects these by literal "
                f"{{{{name}}}} substitution, so without them the query / research / output "
                f"schema never reach the model. Add the placeholders to "
                f"datasets/{dataset_name}/prompts/[{pnode}|default].json "
                f"(node declares: {declared}).",
                code="pipeline_config_invalid",
            )
        # Starting prompt lands on the sparse wire payload, on top of the merged
        # config above — never on `current_config`.
        pipeline_params.setdefault(pnode, {})["prompt"] = rendered
        log(f"Starting prompt: {dataset_name}/prompts/[{pnode}|default].json → {pnode}")


def _validate_model_ownership(
    pipeline_params: dict[str, Any],
    *,
    filtered: PipelineSchema | None,
    active: list[str],
    dataset_name: str,
) -> None:
    """LOUD invariant: every active LLM node must carry an owned ``model``.

    The dataset OWNS its task model, sourced from its own `nodes.{node}.config`
    overlay. A missing model is a setup bug, surfaced loudly here: the prior
    silent fall-through let the backend's hidden GET /pipeline default decide
    (TermNorm ships groq/120b), so a fresh drop ran the wrong model unnoticed.
    In-process meta-prompt nodes (L4) are not `is_llm` and are exempt — their
    model is the install-global optimizer config.
    """
    if filtered is None:
        return
    for name in active:
        node_obj = filtered.get_node(name)
        if node_obj and node_obj.is_llm and not pipeline_params.get(name, {}).get("model"):
            raise PayloadInvalidError(
                f"dataset {dataset_name!r}: LLM node {name!r} has no owned model. "
                f"Declare it in the dataset's pipeline.json::nodes.{name}.config.model "
                f"— the dataset owns its task model, never the backend default.",
                code="pipeline_config_invalid",
            )


def configure_and_apply_pipeline(
    session: Session,
    campaign_config: CampaignConfig,
    *,
    log: Callable[[str], None] = logger.info,
) -> dict[str, Any]:
    """Build pipeline identity, apply filtered schema + overrides onto *session*."""
    from promptpotter.application.datasets import has_dataset_prompts

    exclude = list(campaign_config.exclude_nodes)
    active, filtered = _resolve_active_schema(
        session.pipeline_schema,
        session.experiment_extract,
        exclude=exclude,
        narrowing=campaign_config.optimizer_narrowing,
    )

    dataset_name = campaign_config.dataset_name or session.dataset_name or ""
    dataset_dir = session.dataset_config_dir

    # The dataset→effective node-config merge (sparse `{steps}` base + dataset overlay +
    # campaign overrides) is the shared resolver — the SAME definition the prospective-origin
    # id uses, so a fresh run and `GET /origins` agree on which config the cycle id hashes.
    # This is where the connector `model`/config enters BOTH the measurement identity
    # (`content_hash`/`node_configs` over `session.pipeline_params`) AND the origin cycle id
    # (`build_origin_cycle_id` hashes these merged params). Starting prompts land on top below.
    pipeline_params = resolve_pipeline_config_params(
        active, campaign_config.pipeline_overrides, dataset_dir
    )

    # Starting prompts from `{dataset_dir}/prompts/[<node>|default].json`, per prompt-bearing node.
    if dataset_dir is not None and filtered and has_dataset_prompts(dataset_dir):
        _apply_starting_prompts(
            pipeline_params,
            filtered=filtered,
            active=active,
            dataset_dir=dataset_dir,
            dataset_name=dataset_name,
            log=log,
        )

    _validate_model_ownership(
        pipeline_params, filtered=filtered, active=active, dataset_name=dataset_name
    )

    if filtered is not None:
        session.pipeline_schema = filtered
    session.pipeline_params = pipeline_params

    nodes_str = ", ".join(active)
    excl_str = f"  Excluded: {', '.join(exclude)}" if exclude else ""
    log(f"Active nodes: {nodes_str}{excl_str}")

    return pipeline_params
