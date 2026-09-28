"""Potter's knobs, one typed model per node that owns them. Every value lives in
``assets/optimizers/potter/pipeline.yaml`` and a campaign's ``optimization.nodes`` overlay moves it;
none carries a default here, so the manifest is the one place a number comes from."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Literal, cast

from pydantic import Field

from promptpotter.application.campaign_config import Estimand, Knob, Scope
from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.hashing import shapes_optimizer_prompt

if TYPE_CHECKING:
    from promptpotter.application.optimizer_manifest import SelectedOptimizer

__all__ = [
    "AdaptiveQueueKnobs",
    "EscalationKnobs",
    "EscalationLadder",
    "L1GenerateKnobs",
    "LivesConfig",
    "PoBBKnobs",
    "PotterKnobs",
    "PromptBlockCatalogue",
    "ThetaElectionKnobs",
    "potter_knobs",
]

# The prompt-block-library modes — named once, so every surface offering the knob references this
# closed set instead of re-spelling it.
PromptBlockCatalogue = Literal["guidance", "restrict", "off"]


class EscalationLadder(StrEnum):
    """How far up the L1 → L2 → L3 ladder a cycle may climb. The two predicates are the ONE
    question every fire site asks, so no site re-derives depth from a patience."""

    L1 = "l1"
    L1_L2 = "l1_l2"
    FULL = "full"

    @property
    def fires_l2(self) -> bool:
        return self is not EscalationLadder.L1

    @property
    def fires_l3(self) -> bool:
        return self is EscalationLadder.FULL


class LivesConfig(StrictModel):
    """Improvement-banked round budget: banks a life each round that improves, loses one each round
    that doesn't, on the SAME ``improved`` verdict. ``max_rounds`` and spend stay the ceilings."""

    start: Annotated[int, Knob(Scope.POLICY, Estimand.CONTROLLER, Estimand.SPEND)] = Field(
        2,
        ge=1,
        description="Lives a run starts with (a fully-stalling run does exactly this many L1 rounds).",
    )
    cap: Annotated[int, Knob(Scope.POLICY, Estimand.CONTROLLER, Estimand.SPEND)] = Field(
        4,
        ge=1,
        description="Bank ceiling — lives never exceed this no matter how long the improving streak runs.",
    )


class AdaptiveQueueKnobs(StrictModel):
    """The sampler's. Turn resubset off to freeze the sample basis at campaign start: one fixed
    subset, fixed order, identical for every round and candidate."""

    per_round_resubset: Annotated[bool, Knob(Scope.POLICY, Estimand.SELECTION, Estimand.GATE)] = (
        Field(
            description=(
                "Re-pick the most-informative scoring subset every round from the "
                "search pool (adaptive Rasch selection). On — safe because "
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


class L1GenerateKnobs(StrictModel):
    """The proposer's own knobs, which ride beside its call config in the manifest."""

    n_variants: Annotated[int, Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        ge=1, description="Candidates per round"
    )
    k_max: Annotated[int, Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        ge=0,
        description=(
            "The most shots a variant may carry in ``shot_ids``, drawn from the campaign's demo "
            "pool. ``0`` withdraws the shot slot and its panel, as does a campaign declaring no "
            "demo pool."
        ),
    )
    prompt_block_catalogue: Annotated[PromptBlockCatalogue, Knob(Scope.POLICY, Estimand.SEARCH)] = (
        Field(
            description=(
                "How the prompt block library (``promptpotter/config/"
                "prompt_variants.json`` — reusable ``persona`` / ``task_intent`` / "
                "``thinking_style`` / ``answer_format`` values adopted from PromptWizard "
                "and PromptPotter's own runs) is offered to ``l1_generate``. ``guidance`` "
                "shows the blocks adopted from this project's own runs — the "
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
        description=(
            "Whether THIS campaign's L1 may PROPOSE renaming a field on the inner "
            "``l1_generate``'s output schema (``L1Variant``). Off: "
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


class PoBBKnobs(StrictModel):
    """The eliminator's. Its first decision point is the bench's ``elimination_n_min``, which the
    δ ruler's warmth shares, so aggression lives here and never there."""

    epsilon: Annotated[float, Knob(Scope.POLICY, Estimand.STOPPING)] = Field(
        gt=0.0,
        lt=1.0,
        description="Stop a candidate when its posterior probability of being the "
        "round's best drops below this threshold. Smaller → fewer stops.",
    )
    epsilon_floor: Annotated[float, Knob(Scope.POLICY, Estimand.STOPPING)] = Field(
        gt=0.0,
        lt=1.0,
        description=(
            "The ε applied at exactly ``elimination_n_min``, ramping linearly up to "
            "``epsilon`` by twice that depth and holding it to the panel's last cell. Equal to "
            "``epsilon`` leaves the bar flat and elimination unchanged, so it grades only where "
            "ε was deliberately raised above it: a raised ε then bites as cells accumulate "
            "rather than on the thinnest reading, and an arm clearly behind is still cut "
            "however few cells remain. Set ABOVE ``epsilon`` and the bar goes flat at "
            "``epsilon`` instead — the ``epsilon_floor_inverted`` coupling reports it."
        ),
    )
    lock_in: Annotated[float, Knob(Scope.POLICY, Estimand.STOPPING)] = Field(
        gt=0.0,
        lt=1.0,
        description="Leader lock-in threshold — the P(best) at which a leading "
        "candidate is crowned early and stops measuring. Applies only when "
        "``leader_lock_in`` is on (that bool owns the on/off); this is purely the "
        "threshold. Lower = lock in sooner on less evidence.",
    )
    lock_in_n_min: Annotated[int, Knob(Scope.POLICY, Estimand.STOPPING)] = Field(
        ge=1,
        description="Samples-floor for lock-in — a leader can only lock in after at "
        "least this many measurements. Applies only when ``leader_lock_in`` is on.",
    )
    epsilon_elimination: Annotated[bool, Knob(Scope.POLICY, Estimand.STOPPING)] = Field(
        description=(
            "PoBB ε-stop: drop a candidate once its posterior probability of being "
            "the round's best falls below ``epsilon``. The main loser-elimination rule. "
            "Off → the rule never fires and candidates run their full budget."
        ),
    )
    # Not dead though off everywhere: `LEADER_LOCKED`, the `abort:lock_in_off` lens and
    # `tests/test_numerics.py` exercise lock-in, so deleting it removes a shipped analysis feature.
    leader_lock_in: Annotated[bool, Knob(Scope.POLICY, Estimand.STOPPING)] = Field(
        description=(
            "Crown a decisive leader EARLY: stop measuring a candidate as the winner "
            "once its P(best) against every prior reaches ``lock_in``, before it "
            "spends its full budget. Off → only losers are eliminated "
            "early; a leader measures its full budget."
        ),
    )


class ThetaElectionKnobs(StrictModel):
    """The selector takes none: it crowns the strictly positive θ lift over the parent."""


class EscalationKnobs(StrictModel):
    """The controller's: when potter escalates, how far, and when it stops on its own."""

    l1_patience: Annotated[int, Knob(Scope.POLICY, Estimand.CONTROLLER)] = Field(
        ge=0, description="Consecutive non-improving L1 rounds before L2 fires."
    )
    l2_patience: Annotated[int, Knob(Scope.POLICY, Estimand.CONTROLLER)] = Field(
        ge=0,
        description=(
            "Consecutive non-improving L2 fires before the cycle escalates to L3. "
            "How DEEP the ladder runs is ``escalation_ladder``, never a patience."
        ),
    )
    l3_patience: Annotated[int | None, Knob(Scope.POLICY, Estimand.CONTROLLER)] = Field(
        ge=0,
        description=(
            "Consecutive non-improving L3 fires before the cycle stops on "
            "``CONVERGED``. ``None`` replans without limit, leaving the round and "
            "spend ceilings as the only stops."
        ),
    )
    escalation_ladder: Annotated[EscalationLadder, Knob(Scope.POLICY, Estimand.CONTROLLER)] = Field(
        description=(
            "How far up the L1 → L2 → L3 ladder this campaign may climb — the ablation "
            "switch. ``full`` is the whole ladder. ``l1_l2`` lets L2 re-frame "
            "but never reaches L3, including the post-L2 layout-breach force-trigger. "
            "``l1`` is the L1-only arm: no escalation rule can return a fire, so "
            "``escalate_l2`` is never called and neither the ``l2_context`` nor the "
            "``l3_plan`` prompt is ever composed. A patience PACES a ladder and can "
            "never shorten one, so a large ``l1_patience`` is a deferral bounded by the "
            "round budget rather than a suppression. L1's own prompt is bit-for-bit "
            "identical across all three arms (the property "
            "``rebase_capability`` / ``terminate_capability`` also have), so the arms "
            "differ in what the loop DOES and in nothing it says: a stalled ``l1`` round "
            "simply continues until ``max_rounds`` / ``lives`` / spend binds."
        ),
    )
    # No `Knob` — the walk descends into LivesConfig, so `start` + `cap` are the knobs.
    lives: LivesConfig | None = Field(
        description=(
            "Opt-in improvement-banked round budget ('hearts'): +1 life per improving round, "
            "-1 per non-improving one, stop at 0, banked up to ``cap``. ``None`` → "
            "``max_rounds`` governs. ``max_rounds`` still caps from above, so a lives run "
            "wanting the full bank sets ``max_rounds: null``. Potter's, because the bank moves "
            "on potter's own ``improved`` verdict — its selector's."
        ),
    )
    rebase_capability: Annotated[bool, Knob(Scope.POLICY, Estimand.CONTROLLER)] = Field(
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
    terminate_capability: Annotated[bool, Knob(Scope.POLICY, Estimand.CONTROLLER)] = Field(
        description=(
            "L2/L3 terminate_proposal emission. When True, the "
            "``terminate_capability`` injection renders the stop-the-cycle "
            "instruction into L2 + L3 prompts; a terminate_proposal NAMING A "
            "REASON raises ``StopReason.OPTIMIZER_ABORT`` and the cycle finalizes HALTED "
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


@dataclass(frozen=True)
class PotterKnobs:
    """Every potter knob a campaign runs under, by the node that owns it."""

    adaptive_queue: AdaptiveQueueKnobs
    l1_generate: L1GenerateKnobs
    pobb: PoBBKnobs
    escalation: EscalationKnobs


@shapes_optimizer_prompt
def potter_knobs(selected: SelectedOptimizer) -> PotterKnobs:
    return PotterKnobs(
        adaptive_queue=cast("AdaptiveQueueKnobs", selected.knobs("adaptive_queue")),
        l1_generate=cast("L1GenerateKnobs", selected.knobs("l1_generate")),
        pobb=cast("PoBBKnobs", selected.knobs("pobb")),
        escalation=cast("EscalationKnobs", selected.knobs("escalation")),
    )
