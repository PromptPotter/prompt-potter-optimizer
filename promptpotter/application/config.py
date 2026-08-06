from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import Field

from promptpotter.config.settings import (
    DEFAULT_ORIGIN_BUDGET,
    POBB_DEFAULT_EPSILON,
    PROMPT_STRING_FIELDS,
)
from promptpotter.domain.pipeline_schema import NodeSearchNarrowing
from promptpotter.domain.results import HeadlineMetric
from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.errors import PayloadInvalidError

if TYPE_CHECKING:
    from pathlib import Path

    from promptpotter.application.initialization.session import Session
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.run_records import CycleSeed
    from promptpotter.domain.sample import Sample

logger = logging.getLogger(__name__)

__all__ = [
    "CampaignConfig",
    "Estimand",
    "Knob",
    "OptimizationConfig",
    "PreflightWarning",
    "Scope",
    "apply_inherited_overlay",
    "apply_node_overlay",
    "configure_and_apply_pipeline",
    "estimand_doc",
    "freeze_campaign_config",
    "knob_label",
    "load_campaign_config",
    "resolve_pipeline_config_params",
    "resolved_dataset_name",
    "run_preflight_checks",
]


class Scope(StrEnum):
    """What a knob shapes — ``POLICY`` leaves past measurements valid and governs unevaluated
    rounds; ``DATA`` shapes the trace itself, so resume runs divergence detection."""

    POLICY = "policy"
    DATA = "data"


class Estimand(StrEnum):
    """The statistical quantity a knob moves. **Declaration order IS presentation order** — the
    CLI config map and the webapp panel iterate this enum; never copy members into an ordering tuple."""

    SELECTION = "selection"
    DIFFICULTY = "difficulty"
    DISCRIMINATION = "discrimination"
    ABILITY = "ability"
    GATE = "gate"
    STOPPING = "stopping"
    ESCALATION = "escalation"
    SEARCH = "search"
    SPEND = "spend"
    DISPLAY = "display"


_ESTIMAND_DOC: dict[Estimand, str] = {
    Estimand.SELECTION: "Which samples get scored each round — the subset the fitness is measured over.",
    Estimand.DIFFICULTY: "The per-sample difficulty ruler δ (1PL Rasch) used to difficulty-adjust scores.",
    Estimand.DISCRIMINATION: "The per-sample discrimination aₛ (2PL) — how sharply a sample separates able from unable candidates; only estimated where a data-rich dataset graduates.",
    Estimand.ABILITY: "The candidate ability θ — difficulty-adjusted skill, the metric the gate compares.",
    Estimand.GATE: "The round-promotion / improvement gate — what counts as 'better' and is kept.",
    Estimand.STOPPING: "The early-abort / elimination rules that stop measuring a candidate before budget.",
    Estimand.ESCALATION: "The L1/L2/L3 patience ladder — when the loop escalates strategy or halts.",
    Estimand.SEARCH: "The optimizer search space + data binding the loop explores.",
    Estimand.SPEND: "The budget ceilings (USD / tokens) that halt the cycle.",
    Estimand.DISPLAY: "What number the operator reads — no effect on the data or the decision.",
}


def estimand_doc(estimand: Estimand) -> str:
    return _ESTIMAND_DOC[estimand]


def knob_label(path: str) -> str:
    """Shared by the CLI diagnostic and the webapp config-map, so both name a knob identically."""
    short = path.removeprefix("optimization.")
    if short.startswith("mechanisms."):
        short = short.split(".", 2)[-1]
    return short


@dataclass(frozen=True, init=False)
class Knob:
    """Rides the field as ``Annotated`` metadata, so a knob is declared exactly ONCE. A field
    carrying one is a LEAF whatever its shape; one without that is not a model fails ``knobs``'s walk."""

    scope: Scope
    estimands: tuple[Estimand, ...]

    def __init__(self, scope: Scope, *estimands: Estimand) -> None:
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "estimands", estimands)


class SelectionMechanisms(StrictModel):
    """Turn BOTH off to freeze the sample basis at campaign start: one fixed subset, fixed order,
    identical for every round and candidate."""

    per_round_resubset: Annotated[bool, Knob(Scope.POLICY, Estimand.SELECTION, Estimand.GATE)] = (
        Field(
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
    )


class EliminationMechanisms(StrictModel):
    """Off → the mechanism never fires and candidates run their full budget; numeric tuning for an
    enabled one lives on the sibling ``OptimizationConfig`` fields."""

    epsilon_elimination: Annotated[bool, Knob(Scope.POLICY, Estimand.STOPPING)] = Field(
        True,
        description=(
            "PoBB ε-stop: drop a candidate once its posterior probability of being "
            "the round's best falls below `pobb_epsilon`. The main loser-elimination rule."
        ),
    )
    degradation_fatal_fastpath: Annotated[bool, Knob(Scope.POLICY, Estimand.STOPPING)] = Field(
        True,
        description=(
            "End a candidate at the first FATAL sample (empty response, "
            "content-filtered, structurally broken) without spending the rest of "
            "its budget. Active while the degradation check runs "
            "(`degradation_threshold` > 0); the rate-based check stays governed by "
            "that threshold."
        ),
    )
    leader_lock_in: Annotated[bool, Knob(Scope.POLICY, Estimand.STOPPING)] = Field(
        False,
        description=(
            "Crown a decisive leader EARLY: stop measuring a candidate as the winner "
            "once its P(best) against every prior reaches `pobb_lock_in`, before it "
            "spends its full budget. Off (default) → only losers are eliminated "
            "early; a leader measures its full budget."
        ),
    )


class MechanismConfig(StrictModel):
    """Add a mechanism by adding a bool to the right group — it auto-surfaces to the webapp via
    the schema. Patience-driven L1/L2/L3 escalation is governed separately (``None`` disarms L2/L3)."""

    selection: SelectionMechanisms = Field(default_factory=SelectionMechanisms)
    elimination: EliminationMechanisms = Field(default_factory=EliminationMechanisms)


class LivesConfig(StrictModel):
    """Improvement-banked round budget: banks a life each round that improves, loses one each round
    that doesn't, on the SAME ``improved`` verdict. ``max_rounds`` and spend stay the ceilings."""

    start: Annotated[int, Knob(Scope.POLICY, Estimand.ESCALATION, Estimand.SPEND)] = Field(
        2,
        ge=1,
        description="Lives a run starts with (a fully-stalling run does exactly this many L1 rounds).",
    )
    cap: Annotated[int, Knob(Scope.POLICY, Estimand.ESCALATION, Estimand.SPEND)] = Field(
        4,
        ge=1,
        description="Bank ceiling — lives never exceed this no matter how long the improving streak runs.",
    )


# The prompt-block-library modes — named once so the draft override
# (``OptimizationOverrides``) references the same closed set instead of
# re-spelling it (a 4th mode added there-but-not-here silently never
# reached check-in, and vice versa).
PromptBlockCatalogue = Literal["guidance", "restrict", "off"]


class OptimizationConfig(StrictModel):
    max_rounds: Annotated[int | None, Knob(Scope.POLICY, Estimand.ESCALATION, Estimand.SPEND)] = (
        Field(
            50,
            ge=0,
            description=(
                "Max L1 rounds. 0 = measure the origin (round 0) and stop — the "
                "origin-only run; None = unlimited, bounded only by ``HARD_CAP``."
            ),
        )
    )
    # No `Knob` — the walk descends into LivesConfig, so `start` + `cap` are the knobs.
    lives: LivesConfig | None = Field(
        None,
        description=(
            "Opt-in improvement-banked round budget ('hearts'). When set, replaces the "
            "fixed ``max_rounds`` boundary: +1 life per improving round, -1 per "
            "non-improving one, stop at 0, banked up to ``cap``. ``None`` (default) → "
            "``max_rounds`` governs, behaviour unchanged. ``max_rounds`` still caps from "
            "above, so a lives run wanting the full bank sets ``max_rounds: null``."
        ),
    )
    l1_patience: Annotated[int, Knob(Scope.POLICY, Estimand.ESCALATION)] = Field(
        3, description="Stop after N consecutive non-improving L1 rounds"
    )
    n_variants: Annotated[int, Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        5, description="Candidates per round"
    )
    improvement_threshold: Annotated[float, Knob(Scope.POLICY, Estimand.GATE)] = Field(
        ..., description="Min accuracy delta"
    )

    optimizer_set: Annotated[str, Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        "",
        description=(
            "Which optimizer prompt set this cycle uses. Empty (default) → the "
            "standard `promptpotter/assets/optimizer/` task-tuning loop. `self_optimizing` → the "
            "L4 outer set `promptpotter/assets/optimizer/sets/self_optimizing.yaml`, whose L1 emits per-node "
            "edits to the INNER optimizer's own prompts (`pipeline_params_override`) "
            "instead of tuning its own template. Applied per-cycle at the runner seam "
            "through the same per-node override channel the inner runner uses, so an "
            "outer cycle and the inner (default) cycles it spawns stay isolated "
            "by task. See docs/specs/l4-outer-loop.md § 3."
        ),
    )

    l2_patience: Annotated[int | None, Knob(Scope.POLICY, Estimand.ESCALATION)] = Field(2)
    l3_patience: Annotated[int | None, Knob(Scope.POLICY, Estimand.ESCALATION)] = Field(1)
    degradation_threshold: Annotated[float, Knob(Scope.POLICY, Estimand.STOPPING)] = Field(...)

    elimination_n_min: Annotated[
        int, Knob(Scope.POLICY, Estimand.STOPPING, Estimand.ABILITY, Estimand.DIFFICULTY)
    ] = Field(
        6,
        description="Minimum samples before PoBB starts firing (floor on n for "
        "the Normal-CLT posterior to be meaningful).",
    )
    pobb_epsilon: Annotated[float, Knob(Scope.POLICY, Estimand.STOPPING)] = Field(
        POBB_DEFAULT_EPSILON,
        description="Stop a candidate when its posterior probability of being the "
        "round's best drops below this threshold. Default 15%; smaller → fewer stops.",
    )
    pobb_lock_in: Annotated[float, Knob(Scope.POLICY, Estimand.STOPPING)] = Field(
        0.95,
        description="Leader lock-in threshold — the P(best) at which a leading "
        "candidate is crowned early and stops measuring. Applies only when "
        "`mechanisms.elimination.leader_lock_in` is on (that bool owns the on/off); "
        "this is purely the threshold. Lower = lock in sooner on less evidence.",
    )
    pobb_lock_in_n_min: Annotated[int, Knob(Scope.POLICY, Estimand.STOPPING)] = Field(
        8,
        description="Samples-floor for lock-in — a leader can only lock in after at "
        "least this many measurements. Applies only when "
        "`mechanisms.elimination.leader_lock_in` is on.",
    )

    spend_budget_usd: Annotated[float | None, Knob(Scope.POLICY, Estimand.SPEND)] = Field(
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

    token_budget: Annotated[int | None, Knob(Scope.POLICY, Estimand.SPEND)] = Field(
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

    panel_gate: Annotated[Literal["strict", "off"], Knob(Scope.POLICY, Estimand.GATE)] = Field(
        "strict",
        description=(
            "Halt at the end of a round whose electable candidates carry HOLES — cells "
            "the loop attempted and got no measurement back from — instead of electing a "
            "winner from a comparison whose arms ran different cell sets. ``strict`` "
            "(default) halts on any hole; ``off`` disarms. The halt is resumable and the "
            "round is not persisted, so a plain ``resume`` re-runs it: the cached "
            "candidates replay, the missing cells are re-measured, and the round decides "
            "on a complete panel. A PoBB elimination is NOT a hole (those cells were "
            "never attempted) and neither is a classifier-deprecated sample."
        ),
    )

    origin_gate: Annotated[
        Literal["strict", "critical_only", "off"], Knob(Scope.POLICY, Estimand.GATE)
    ] = Field(
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

    prompt_block_catalogue: Annotated[PromptBlockCatalogue, Knob(Scope.POLICY, Estimand.SEARCH)] = (
        Field(
            "guidance",
            description=(
                "How the prompt building-block library (``promptpotter/config/"
                "prompt_variants.json`` — reusable ``persona`` / ``task_intent`` / "
                "``thinking_style`` / ``answer_format`` values adopted from PromptWizard "
                "and PromptPotter's own runs) is offered to ``l1_generate``. ``guidance`` "
                "(default) shows the blocks adopted from this project's own runs — the "
                "value space stays open, so L1 may reuse one verbatim, adapt one, or write "
                "its own, and the imported Self-Discover tail would only be menu. "
                "``restrict`` narrows the field's value space to the *whole* library (which "
                "it therefore renders in full) — an off-library value is a forbidden value, "
                "rejected by "
                "``validate_overrides`` exactly as a forbidden axis is (synthetic-0, no "
                "backend spend, healed via the L2 wound). ``off`` renders nothing, so the "
                "prompt is bit-for-bit identical to a no-library ablation run."
            ),
        )
    )

    schema_field_rename: Annotated[bool, Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        False,
        description=(
            "Whether THIS campaign's L1 may PROPOSE renaming a field on the inner "
            "``l1_generate``'s output schema (``L1Variant``). Off by default: "
            "``build_l1_response_schema`` never grafts ``output_schema_field_names``, so the "
            "LLM cannot emit a key the schema omits — the same structural lock "
            "the model/provider axes use, not a per-round rejection. A field NAME is the "
            "wire contract; a ``description`` is not, which is why descriptions are always "
            "free and names are not. It gates the PROPOSAL only: an inner cycle honours a "
            "rename it is handed unconditionally (it loads its own ``campaign.json``, so "
            "gating there would silently drop every rename the outer emits). The rename is "
            "a presentation transform — ``build_l1_response_model`` aliases the wire key "
            "back onto the real field, so no downstream reader observes it, and a rename the "
            "model fails to honour makes the round unparseable, scoring it "
            "``problem_rate = 1.0``. Unlocking changes the search space: it is ``policy`` "
            "and bound to ``Estimand.SEARCH``, so it must ride a fork, never a resume."
        ),
    )

    rebase_capability: Annotated[bool, Knob(Scope.POLICY, Estimand.ESCALATION)] = Field(
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

    terminate_capability: Annotated[bool, Knob(Scope.POLICY, Estimand.ESCALATION)] = Field(
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

    # Round-level Rasch IRT — one posterior fit per round drives `select_round_subset`
    # + the heatmap.
    seed_heatmap_from_archive: Annotated[bool, Knob(Scope.POLICY, Estimand.DIFFICULTY)] = Field(
        False,
        description=(
            "Round-end hard-sample artifact's Rasch fit folds in archive "
            "observations. δ_s ordering on the heatmap X-axis reflects "
            "cross-cycle evidence."
        ),
    )
    enable_2pl_graduation: Annotated[
        bool,
        Knob(Scope.POLICY, Estimand.DISCRIMINATION, Estimand.DIFFICULTY, Estimand.ABILITY),
    ] = Field(
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
    mechanisms: MechanismConfig = Field(default_factory=MechanismConfig)


class DatasetSplit(StrictModel):
    test: int = Field(description="Held-out test fold size — not in the bank or the table")


class CampaignConfig(StrictModel):
    dataset_name: Annotated[str, Knob(Scope.DATA, Estimand.SEARCH)] = Field("")
    sp_budget_ttest: Annotated[int, Knob(Scope.POLICY, Estimand.SELECTION)] = Field(
        20,
        description="Per-round eval budget — how many samples each candidate is "
        "scored on per round. The full train split is the bank; each round the "
        "adaptive queue mechanism (`select_round_subset`) selects this many "
        "informative samples from it. Not the dataset/pool size.",
    )
    sp_budget_origin: Annotated[int | None, Knob(Scope.POLICY, Estimand.SELECTION)] = Field(
        DEFAULT_ORIGIN_BUDGET,
        ge=1,
        description="Origin eval budget — how many bank samples the origin (C0) is "
        "scored on at check-in. Explicit `null` ⇒ `sp_budget_ttest`. Defaults ABOVE "
        "`sp_budget_ttest` because origin breadth is the one breadth that is nearly "
        "free: θ_origin is the term EVERY delta subtracts, and its rows are "
        "content-addressed cache replayed into every candidate arm, fork and resume — "
        "paid once per config. Candidate breadth is paid per candidate, per round. "
        "Every comparison downstream is matched by sample_id or θ-space, so an origin "
        "scored on a superset stays like-for-like. A bank smaller than this scores on "
        "the whole bank (`sample_dataset` is a prefix slice) — not an error.",
    )
    exclude_nodes: Annotated[list[str], Knob(Scope.DATA, Estimand.SEARCH)] = Field(
        default_factory=list
    )
    pipeline_overrides: Annotated[dict[str, Any], Knob(Scope.DATA, Estimand.SEARCH)] = Field(
        default_factory=dict
    )
    optimizer_narrowing: Annotated[
        dict[str, NodeSearchNarrowing], Knob(Scope.DATA, Estimand.SEARCH)
    ] = Field(
        default_factory=dict,
        description="Per-node narrowing of the dataset-declared optimizer search "
        "space — the per-campaign param-lock + allowed-values lever beside "
        "`exclude_nodes` (whole node). model/provider are always locked. Subsets "
        "only; applied onto the schema by `PipelineSchema.narrow` at pipeline setup.",
    )
    allowed_models: Annotated[list[str], Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        default_factory=list,
        description="The origin's permitted model set — the allow-list a human fork "
        "may steer the inner-optimizer model to WITHOUT tainting the branch. Distinct "
        "from `PipelineSchema.available_models` (the backend's whole catalogue, the menu "
        "this is picked from). Governs only the direct human steer; the optimizer never "
        "searches model/provider regardless (`PARAM_FORBIDDEN_KEYS` invariant). A steer "
        "to a model NOT in this set is a `campaign.babysit` act — warned + graded C "
        "(`overlay_sets_model_outside_allowed`). EMPTY = no model sanctioned, so any "
        "model steer taints (restrictive default — hard to break out of the origin); a "
        "non-empty set is the operator explicitly sanctioning those models.",
    )
    scoring: Annotated[str | dict[str, str] | None, Knob(Scope.DATA, Estimand.GATE)] = Field(None)
    headline_metric: Annotated[HeadlineMetric, Knob(Scope.POLICY, Estimand.DISPLAY)] = Field(
        "accuracy",
        description="Which fitness number headlines the operator's text surfaces "
        "(lineage node value, Best tile, sidebar) by default. DISPLAY config, not "
        "search state — the gate is always difficulty-adjusted ability θ; this only "
        "picks the number the human READS, client-overridable per session. `ability` "
        "shows θ (a logit, jargon) — defaults to `accuracy` so θ is never forced on "
        "an operator who didn't ask for it. Rides the `composite_fitness_formula` "
        "serve path to `dashboard.json::headline_metric`; never on `OptSearchPoint`.",
    )
    # Carries a `Knob`, so the walk STOPS here: the split is one knob, not two.
    dataset_split: Annotated[DatasetSplit | None, Knob(Scope.POLICY, Estimand.DISPLAY)] = Field(
        None,
        description="Canonical train/test fold sizes for the dashboard footer. "
        "None when the dataset declares no split.",
    )

    # No `Knob` — the walk descends into OptimizationConfig.
    optimization: OptimizationConfig

    def origin_budget(self) -> int:
        return self.sp_budget_origin or self.sp_budget_ttest


def load_campaign_config(raw: dict[str, Any] | CampaignConfig) -> CampaignConfig:
    if isinstance(raw, CampaignConfig):
        return raw
    return CampaignConfig.model_validate(raw)


def freeze_campaign_config(config: CampaignConfig) -> dict[str, Any]:
    """Sole writer of the snapshot's shape, and it persists only the DELTA from defaults, so a knob
    nobody set cannot make it unloadable. A set-then-renamed knob is re-stamped, never shimmed."""
    return config.model_dump(mode="json", exclude_defaults=True)


def apply_inherited_overlay(
    config: CampaignConfig,
    frozen_config: dict[str, Any],
    seed: CycleSeed | None,
) -> CampaignConfig:
    """Resume/fork rebuild from the LIVE dataset, which holds neither ``pipeline_overrides`` nor
    ``optimizer_narrowing`` — read off the snapshot DICT, so one renamed leaf cannot block a resume."""
    frozen_narrowing = {
        node: NodeSearchNarrowing.model_validate(raw)
        for node, raw in (frozen_config.get("optimizer_narrowing") or {}).items()
    }
    narrowing = {**config.optimizer_narrowing, **frozen_narrowing}
    if seed is not None:
        narrowing.update(seed.optimizer_narrowing)
    frozen_overrides: dict[str, Any] = frozen_config.get("pipeline_overrides") or {}
    # `allowed_models` is the campaign's committed model allow-list — the SINGLE source
    # of truth is the frozen snapshot (edited only by the cap-gated `set-allowed-models`
    # command; the live dataset file is a mint-time SEED). Re-apply it unconditionally so
    # the runner's grade-C stamp reads the SAME value the fork-cycle cap-gate reads off
    # `campaign.config` — never the live file, which would let the two disagree. Absent =
    # the restrictive default ([]), same as the snapshot's own default.
    return config.model_copy(
        update={
            "pipeline_overrides": {**config.pipeline_overrides, **frozen_overrides},
            "optimizer_narrowing": narrowing,
            "allowed_models": list(frozen_config.get("allowed_models") or []),
        }
    )


@dataclass(frozen=True)
class PreflightWarning:
    code: str
    title: str
    detail: str


def _check_sp_budget_vs_dataset(
    config: CampaignConfig, dataset: list[Sample]
) -> PreflightWarning | None:
    # Only the PER-ROUND budget is checked against the bank. An origin budget above the
    # bank is not a misconfiguration: `sp_budget_origin` defaults ABOVE `sp_budget_ttest`
    # (DEFAULT_ORIGIN_BUDGET), `sample_dataset` is a prefix slice, and "score the origin
    # on everything there is" is exactly what a wide-origin default wants on a small bank.
    # Warning on it told every small-bank dataset to lower a knob nobody set, on every run.
    # `sp_budget_ttest > bank` IS a real finding — it means the adaptive queue mechanism
    # has no bank to select from and every round re-scores the same full set.
    n = config.sp_budget_ttest
    m = len(dataset)
    if m > 0 and n > m:
        return PreflightWarning(
            code="sp_budget_exceeds_dataset",
            title=f"per-round eval budget ({n}) exceeds bank size ({m})",
            detail=(
                f"The bank (full train split) has only {m} samples, so every round "
                f"scores on all {m} and `select_round_subset` has nothing to select "
                f"from — the adaptive queue mechanism is inert. Lower sp_budget_ttest "
                f"to below {m}, or grow the dataset."
            ),
        )
    return None


_PARAMS_B_RE = re.compile(r"(\d+(?:\.\d+)?)\s*b\b", re.IGNORECASE)


def _model_params_b(model_id: str) -> float | None:
    segment = model_id.rsplit("/", 1)[-1]
    match = _PARAMS_B_RE.search(segment)
    return float(match.group(1)) if match else None


def _check_optimizer_below_target(target_models: tuple[str, ...]) -> PreflightWarning | None:
    """An optimizer smaller than the target it optimizes is almost always an accidental inversion.
    Its model is the install-global optimizer node config, never a per-campaign copy."""
    from promptpotter.application.optimization.dispatch.llm_call.prompts import optimizer_model

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
            "`promptpotter/assets/optimizer/pipeline.yaml` to a larger tier."
        ),
    )


def _check_config_couplings(config: CampaignConfig) -> list[PreflightWarning]:
    """The declared map lives in ``knobs``, which the webapp config-map endpoint also reads; this is
    its pre-run CLI leg."""
    from promptpotter.application.knobs import check_couplings

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
    """``target_models`` are the resolved per-node target/scoring model ids, empty when the backend
    owns the model. Pure — no mutation, no I/O."""
    warnings: list[PreflightWarning] = []
    if (w := _check_sp_budget_vs_dataset(config, dataset)) is not None:
        warnings.append(w)
    if (w := _check_optimizer_below_target(target_models)) is not None:
        warnings.append(w)
    if (w := _check_lives_have_headroom(config)) is not None:
        warnings.append(w)
    warnings.extend(_check_config_couplings(config))
    return warnings


def _check_lives_have_headroom(config: CampaignConfig) -> PreflightWarning | None:
    """``lives`` that cannot run out before ``max_rounds`` is an inert brake — and when the two
    coincide the stop reason no longer says which fact stopped the run."""
    lives = config.optimization.lives
    max_rounds = config.optimization.max_rounds
    # `max_rounds=None` is the unbounded case — there is no calendar for hearts to race.
    if lives is None or max_rounds is None or lives.start < max_rounds:
        return None
    return PreflightWarning(
        code="config.lives_no_headroom",
        title="the stall brake cannot fire before the calendar cap",
        detail=(
            f"optimization.lives.start={lives.start} >= max_rounds="
            f"{max_rounds}, so hearts can never run out first: the run "
            "stops on the calendar and reports `lives_exhausted` for it. Lower lives.start "
            "to brake a stalling run early, or raise max_rounds to give it room — leaving "
            "both equal makes the stop reason unreadable."
        ),
    )


def check_model_reasoning_floors(
    node_configs: Iterable[tuple[str, Mapping[str, Any]]],
) -> list[str]:
    """Below ``ModelProfile.min_max_tokens`` a reasoning model can spend its whole budget thinking
    and emit nothing. An ABSENT ``max_tokens`` is the sanctioned default, never a violation."""
    from promptpotter.infrastructure.llm.registry import model_profile

    violations: list[str] = []
    for node, cfg in node_configs:
        model = cfg.get("model")
        max_tokens = cfg.get("max_tokens")
        if not model or max_tokens is None:
            continue
        profile = model_profile(str(model))
        if profile is None or not profile.is_reasoning:
            continue
        if int(max_tokens) < profile.min_max_tokens:
            violations.append(
                f"node '{node}': reasoning model '{model}' is pinned to max_tokens="
                f"{max_tokens}, below its floor {profile.min_max_tokens}. It will spend the "
                f"budget reasoning and emit zero content (reasoning_budget_exhausted). Raise "
                f"max_tokens to >= {profile.min_max_tokens} and keep reasoning_effort low."
            )
    return violations


def apply_node_overlay(
    base: dict[str, Any],
    overlay: Mapping[str, Any],
    schema: PipelineSchema | None,
) -> dict[str, Any]:
    """The ONE per-node overlay merge. Depth is bounded by the DECLARATION (``param_types: object``
    merges one level deeper, so a sibling entry a parent earned survives), never by sniffing types."""
    merged = dict(base)
    for node, cfg in overlay.items():
        existing = merged.get(node)
        if not (isinstance(existing, dict) and isinstance(cfg, dict)):
            merged[node] = cfg
            continue
        node_obj = schema.get_node(node) if schema else None
        param_types = node_obj.param_types if node_obj else {}
        node_cfg = {**existing, **cfg}
        for param, incoming in cfg.items():
            prior = existing.get(param)
            if (
                param_types.get(param) == "object"
                and isinstance(prior, dict)
                and isinstance(incoming, dict)
            ):
                node_cfg[param] = {**prior, **incoming}
        merged[node] = node_cfg
    return merged


def resolve_pipeline_config_params(
    active: list[str],
    pipeline_overrides: Mapping[str, Any],
    dataset_dir: Path | None,
    schema: PipelineSchema,
) -> dict[str, Any]:
    """The SINGLE definition of which node config a cycle id and a measurement key hash — shared
    with ``GET /origins``, so the prospective origin and the real one cannot diverge."""
    from promptpotter.application.datasets.prompts import load_dataset_node_overlay

    pipeline_params: dict[str, Any] = {"steps": list(active)}
    if dataset_dir is not None:
        # Per-dataset overlay — sparse overrides on backend defaults (e.g. AIME →
        # OpenRouter+Mistral). `dataset_dir` is tenant-first, so ingested datasets honor it.
        dataset_overlay = {
            node: cfg
            for node, cfg in load_dataset_node_overlay(dataset_dir).items()
            if node in active
        }
        pipeline_params = apply_node_overlay(pipeline_params, dataset_overlay, schema)
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
    pipeline_params = apply_node_overlay(pipeline_params, valid_overrides, schema)
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
        pipeline_params = apply_node_overlay(pipeline_params, identity, schema)
    return pipeline_params


def _connector_identity_config(dataset_dir: Path) -> dict[str, dict[str, Any]]:
    from promptpotter.connectors import CONNECTORS
    from promptpotter.infrastructure.store.dataset_access import dataset_pipeline_path
    from promptpotter.infrastructure.store.io import read_yaml_optional

    raw = read_yaml_optional(dataset_pipeline_path(dataset_dir))
    connector = CONNECTORS.get(str((raw or {}).get("backend_type") or ""))
    if connector is None or connector.identity_config is None:
        return {}
    return connector.identity_config(dataset_dir)


def missing_template_vars(rendered: str, declared: list[str]) -> list[str]:
    """The SINGLE definition of a required placeholder, shared by the mint-time setup check and the
    in-loop L1 guard. ``PROMPT_STRING_FIELDS`` are excluded: ``render()`` ASSEMBLES them."""
    return [
        v for v in declared if v not in PROMPT_STRING_FIELDS and "{{" + v + "}}" not in rendered
    ]


def _resolve_active_schema(
    pipeline_schema: PipelineSchema,
    *,
    exclude: list[str],
    narrowing: dict[str, NodeSearchNarrowing],
) -> tuple[list[str], PipelineSchema]:
    """``steps`` is two shapes under one word: here the BACKEND's ``list[dict]`` from ``GET
    /pipeline``, on ``pipeline_params`` the reserved ``list[str]`` of active node names."""

    active = pipeline_schema.active_steps_excluding(exclude)

    filtered = pipeline_schema
    if exclude:
        filtered = pipeline_schema.filter_to_steps(active)
    # Campaign search-space narrowing — the per-node param-lock + allowed-values
    # subset, peer to exclude (above). The dataset declares the max; the campaign
    # snapshot may only narrow it.
    if narrowing:
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
    """Assumes the caller checked ``has_dataset_prompts``."""
    from promptpotter.application.datasets.prompts import load_node_prompt

    prompt_nodes = [n for n in filtered.prompt_node_names() if n in active]
    if not prompt_nodes:
        # The dataset ships starting prompts but no active node declares
        # `prompt_info` — so the rendered prompt has nowhere to land and is
        # dropped before the wire. Silent here = every backend call runs with
        # an empty system prompt (the bug that made an ingested dataset score
        # 0% on email-replies). Fail loud: a generation node must advertise
        # `prompt_info` in GET /pipeline (or the dataset overlay).
        logger.warning(
            "configure_and_apply_pipeline: dataset %r has starting prompts but NO "
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
                f"datasets/{dataset_name}/prompts/[{pnode}|default].yaml "
                f"(node declares: {declared}).",
                code="pipeline_config_invalid",
            )
        # Starting prompt lands on the sparse wire payload, on top of the merged
        # config above — never on `current_config`.
        pipeline_params.setdefault(pnode, {})["prompt"] = rendered
        log(f"Starting prompt: {dataset_name}/prompts/[{pnode}|default].yaml → {pnode}")


def _validate_model_ownership(
    pipeline_params: dict[str, Any],
    *,
    filtered: PipelineSchema,
    active: list[str],
    dataset_name: str,
) -> None:
    """The dataset OWNS its task model; a missing one is a setup bug, because a silent fall-through
    lets the backend's hidden ``GET /pipeline`` default decide. L4 optimizer nodes are exempt."""
    if filtered is None:
        return
    for name in active:
        node_obj = filtered.get_node(name)
        if node_obj and node_obj.is_llm and not pipeline_params.get(name, {}).get("model"):
            raise PayloadInvalidError(
                f"dataset {dataset_name!r}: LLM node {name!r} has no owned model. "
                f"Declare it in the dataset's pipeline.yaml::nodes.{name}.config.model "
                f"— the dataset owns its task model, never the backend default.",
                code="pipeline_config_invalid",
            )


def resolved_dataset_name(session: Session, campaign_config: CampaignConfig) -> str:
    """One rule, one place: this feeds the same identity as the mint seam's ``Campaign.dataset_name``,
    so a divergence renames campaigns and files measurements under a name nothing looks up."""
    return campaign_config.dataset_name or session.dataset_name or ""


def configure_and_apply_pipeline(
    session: Session,
    campaign_config: CampaignConfig,
    *,
    log: Callable[[str], None] = logger.info,
) -> dict[str, Any]:
    from promptpotter.application.datasets.prompts import has_dataset_prompts

    exclude = list(campaign_config.exclude_nodes)
    active, filtered = _resolve_active_schema(
        session.pipeline_schema,
        exclude=exclude,
        narrowing=campaign_config.optimizer_narrowing,
    )

    dataset_name = resolved_dataset_name(session, campaign_config)
    dataset_dir = session.dataset_config_dir

    # The dataset→effective node-config merge (sparse `{steps}` base + dataset overlay +
    # campaign overrides) is the shared resolver — the SAME definition the prospective-origin
    # id uses, so a fresh run and `GET /origins` agree on which config the cycle id hashes.
    # This is where the connector `model`/config enters BOTH the measurement identity
    # (`content_hash`/`node_configs` over `session.pipeline_params`) AND the origin cycle id
    # (`build_origin_cycle_id` hashes these merged params). Starting prompts land on top below.
    pipeline_params = resolve_pipeline_config_params(
        active, campaign_config.pipeline_overrides, dataset_dir, filtered
    )

    # Starting prompts from `{dataset_dir}/prompts/[<node>|default].yaml`, per prompt-bearing node.
    if dataset_dir is not None and has_dataset_prompts(dataset_dir):
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

    session.pipeline_schema = filtered
    session.pipeline_params = pipeline_params

    nodes_str = ", ".join(active)
    excl_str = f"  Excluded: {', '.join(exclude)}" if exclude else ""
    log(f"Active nodes: {nodes_str}{excl_str}")

    return pipeline_params
