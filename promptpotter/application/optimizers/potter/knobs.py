"""No knob carries a default here: the manifest is the one place a number comes from."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field

from promptpotter.domain.campaign_config import Estimand, Knob, Scope
from promptpotter.domain.pool import Access
from promptpotter.domain.scoring import MAX_PASS_ROWS
from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.hashing import shapes_optimizer_prompt
from promptpotter.shared.statistics import sign_posterior

shapes_optimizer_prompt(__name__)


CellStrata = Literal["none", "dataset"]


class EscalationLadder(StrEnum):
    """The two predicates are the ONE question every fire site asks, so no site re-derives depth from a patience."""

    L1 = "l1"
    L1_L2 = "l1_l2"
    FULL = "full"

    @property
    def fires_l2(self) -> bool:
        return self is not EscalationLadder.L1

    @property
    def fires_l3(self) -> bool:
        return self is EscalationLadder.FULL


class AdaptiveQueueKnobs(StrictModel):
    sp_budget_round: Annotated[int, Knob(Scope.POLICY, Estimand.SELECTION)] = Field(
        ge=1,
        le=MAX_PASS_ROWS,
        description="Per-round eval budget — how many cells each candidate is scored on per "
        "round. The search split is the bank; each round draws this many cells from it at "
        "random. Not the pool's size.",
    )

    per_round_resubset: Annotated[bool, Knob(Scope.POLICY, Estimand.SELECTION, Estimand.GATE)] = (
        Field(
            description=(
                "Draw a fresh seeded scoring subset every round from the search split, "
                "blind to every outcome and ability, so the election reads cells the held-out "
                "bench would. On — safe because "
                "every cross-round comparator (election, PoBB, c0_ok, the stall ladder) "
                "measures on one fixed θ ruler, so a shifting per-round subset stays "
                "comparable — but it is warm-gated: while the δ ruler is still cold (a "
                "fresh dataset's early rounds) the subset stays FROZEN to the campaign-start "
                "selection, so those rounds are comparable AND concentrate measurements to "
                "warm the ruler fastest; it thaws once the ruler locks. Off → "
                "the campaign-start selection (deterministic bank prefix) for every round, "
                "the whole campaign."
            ),
        )
    )

    strata: Annotated[CellStrata, Knob(Scope.POLICY, Estimand.SELECTION)] = Field(
        description="What a round's draw is spread over. `none` draws the budget off the whole "
        "pool; `dataset` gives each dataset its cells measure an equal share — so a mixed pool "
        "never fills a round from one stratum. A stratum short of its share hands the rest to the "
        "others."
    )


class L1GenerateKnobs(StrictModel):
    n_variants: Annotated[int, Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        ge=1, description="Candidates per round"
    )
    k_max: Annotated[int, Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        ge=0,
        description=(
            "The most shots a variant may carry per prompt node in ``shots``, drawn by id from the "
            "demo pool's active subset — its access to that pool. ``0`` is ``off``: it withdraws "
            "the shot slot and its panel, as does a campaign declaring no demo pool."
        ),
    )
    pool_window: Annotated[int, Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        ge=1,
        description=(
            "The most entries one call shows of each pool it reads — per field for the block pool, "
            "earned blocks first — the rest rotating in by round, so a large active subset costs "
            "no context. A ``closed`` block pool shows its subset whole, its wire's enum."
        ),
    )
    recombine: Annotated[int, Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        ge=0,
        description=(
            "The children a host call may name per call by recombination, ``l1_generate`` adding "
            "them beside its variants: from the RECOMBINATION POOL (the campaign's measured "
            "individuals, highest θ first) a main of the population, the line the child continues, "
            "and a donor of any round giving it a minority of loci, every value a parent's. "
            "``0`` is off: it withdraws the panel and the slot, so the prompt is bit-for-bit a "
            "no-recombination run."
        ),
    )
    recombination_host: Annotated[
        Literal["l1_generate", "l2_context"], Knob(Scope.POLICY, Estimand.SEARCH)
    ] = Field(
        description=(
            "Which call writes those children, the pool panel and the reply slot moving with it. "
            "``l1_generate``: the call that writes this round's variants. ``l2_context``: a fire, "
            "its children joining the next round's arms."
        ),
    )
    block_access: Annotated[Access, Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        description=(
            "How ``l1_generate`` takes the ``block_pool`` node's pool — the entries of the block "
            "packs that node names which its ``active`` paths keep, led by the blocks earlier "
            "runs on this answer space earned. ``open`` "
            "shows a per-field window of them as material L1 may reuse verbatim, adapt or ignore. "
            "``closed`` makes a pooled field's value one of the active subset's entry ids, an enum "
            "on the wire listing them all, and withdraws every field the subset holds no entry "
            "for; a subset keeping no library entry is refused before any spend. ``off`` shows "
            "nothing, so the prompt is bit-for-bit a no-pool ablation run."
        ),
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
    """Its first decision point is the bench's ``elimination_n_min``, which the δ ruler's warmth shares: aggression lives HERE."""

    epsilon: Annotated[float, Knob(Scope.POLICY, Estimand.STOPPING)] = Field(
        gt=0.0,
        lt=1.0,
        description="Stop a candidate when its posterior probability of being the "
        "round's best drops below this threshold. Smaller → fewer stops. A stop ends "
        "measurement only; what is promoted is ``theta_election.admission``'s.",
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
    # Off everywhere yet not dead: the `abort:lock_in_off` lens exercises lock-in.
    leader_lock_in: Annotated[bool, Knob(Scope.POLICY, Estimand.STOPPING)] = Field(
        description=(
            "Crown a decisive leader EARLY: stop measuring a candidate as the winner "
            "once its P(best) against every prior reaches ``lock_in``, before it "
            "spends its full budget. Off → only losers are eliminated "
            "early; a leader measures its full budget."
        ),
    )


class ElectionKnobs(StrictModel):
    admission: Annotated[float, Knob(Scope.POLICY, Estimand.GATE)] = Field(
        ge=sign_posterior(1, 0),
        lt=1.0,
        description=(
            "The confidence an arm must exceed to be promoted over its parent: its posterior "
            "P(θ_arm > θ_parent), capped by what its discordant cells support. Never below "
            "what one discordant cell supports (0.75), so one cell never promotes. Higher → "
            "fewer promotions."
        ),
    )


class EscalationKnobs(StrictModel):
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
            "``OPTIMIZER_EXHAUSTED``. ``None`` replans without limit, leaving the round and "
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
            "simply continues until ``budget.rounds`` / ``lives`` / spend binds."
        ),
    )
    rebase_capability: Annotated[bool, Knob(Scope.POLICY, Estimand.CONTROLLER)] = Field(
        description=(
            "L2/L3 fork_proposal emission. When True, the ``rebase_capability`` "
            "injection renders the rare-escape-hatch instruction into L2 + L3 "
            "prompts and the runner auto-mints a sibling cycle on each fired "
            "fork_proposal that changes something, its rounds charged to the line. When "
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
