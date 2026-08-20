from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import Field

from promptpotter.config.settings import (
    DEFAULT_ORIGIN_BUDGET,
    POBB_DEFAULT_EPSILON,
)
from promptpotter.domain.pipeline_schema import NodeSearchNarrowing
from promptpotter.domain.results import HardSampleOrder, HeadlineMetric
from promptpotter.domain.strict_model import StrictModel

if TYPE_CHECKING:
    from promptpotter.domain.run_records import CycleSeed

__all__ = [
    "CampaignConfig",
    "Estimand",
    "Knob",
    "LivesConfig",
    "OptimizationConfig",
    "Scope",
    "apply_inherited_overlay",
    "estimand_doc",
    "freeze_campaign_config",
    "knob_label",
    "load_campaign_config",
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
    pobb_epsilon_floor: Annotated[float, Knob(Scope.POLICY, Estimand.STOPPING)] = Field(
        POBB_DEFAULT_EPSILON,
        description=(
            "The ε applied at exactly ``elimination_n_min``, ramping linearly up to "
            "``pobb_epsilon`` by twice that depth. Equal to ``pobb_epsilon`` — the "
            "default — leaves the bar flat and elimination unchanged, so it grades "
            "only where ε was deliberately raised above it. At the floor a single "
            "discordant sample already puts P(best) near 0.2, so one scalar ε is "
            "either too eager there or too permissive deep; grading keeps a raised "
            "ε's aggression at depth while giving a one-sample-behind arm a few more "
            "cells to recover. Aggression belongs here and never in "
            "``elimination_n_min``, which also gates the δ ruler's warmth. Set ABOVE "
            "``pobb_epsilon`` and the bar goes flat at ``pobb_epsilon`` instead — the "
            "``epsilon_floor_inverted`` coupling reports it."
        ),
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
        None,
        description=(
            "Halt this cycle when cumulative tokens (optimizer + backend, input + "
            "output) ≥ this value. ``None`` — the default — disarms it, leaving "
            "``spend_budget_usd`` as the single ceiling: a token is worth a different "
            "amount on every route, so a token cap sized for one model silently "
            "becomes a different budget on the next, and it was the cap that halted "
            "runs far below their USD ceiling. Dollars are the model-invariant "
            "measure and every route we run reports a wire cost. Set an integer for "
            "the one case USD cannot see — a backend that BILLS but reports no cost, "
            "where spend reads low and ``unpriced_tokens`` on the dashboard is the "
            "tell. Whichever of the two ceilings trips first halts the cycle."
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
            "instruction into L2 + L3 prompts; a terminate_proposal NAMING A "
            "REASON raises ``StopReason.ABORT`` and the cycle finalizes HALTED "
            "on the current cycle_id (no fork), while a blank one is ignored "
            "like any volunteered field. The intended user is an unrecoverable "
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
    hard_sample_order: Annotated[HardSampleOrder, Knob(Scope.POLICY, Estimand.DISPLAY)] = Field(
        "info_gain",
        description="Which key ranks the hard-sample leaderboard — the heatmap, the table "
        "and the `log.md` heatmap, which all read ONE served rank. `info_gain` sorts by the "
        "queue mechanism's acquisition score (`pick_value`: decision-information gain + δ "
        "learning gain), contested-at-the-leader samples first; `difficulty` sorts by the "
        "Rasch ruler δ_s alone, hardest first. DISPLAY config: it picks the order the human "
        "READS and never what the engine scores, which is `build_round_order` and reaches no "
        "knob. Client-overridable per session, like `headline_metric`.",
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
