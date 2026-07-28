"""Campaign configuration — CampaignConfig Pydantic model, pipeline setup, LLM factory.

Backend-specific experiment-data extraction lives in
:mod:`promptpotter.connectors`; ``bootstrap`` looks up a connector by name
and reads ``connector.extract_experiment(extract)``.
"""

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

    from promptpotter.application.bootstrap.session import Session
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
    "run_preflight_checks",
]


class Scope(StrEnum):
    """What a knob shapes — the question resume asks of every config edit.

    - ``POLICY``: a decision knob (patience, thresholds, ε, n_variants). Past
      measurements and candidates stay valid; the new policy governs unevaluated rounds.
    - ``DATA``: the knob shapes the data trace itself (JobSearchPoint inputs, scoring,
      the dataset binding). Cached measurements may not apply — resume runs divergence
      detection.
    """

    POLICY = "policy"
    DATA = "data"


class Estimand(StrEnum):
    """The statistical quantity a knob moves — the axis a statistician groups by.

    A knob may touch more than one. Couplings are *between knobs that share an
    estimand*: that shared quantity is what goes ill-defined when they disagree.

    **Declaration order IS presentation order.** Both the CLI config map and the
    webapp's config panel iterate this enum directly. Never copy these members into
    an ordering tuple: two such copies existed, both silently omitted
    ``DISCRIMINATION``, and its group vanished from both surfaces.
    """

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
    """Plain-language one-liner for an estimand (teach, don't dump)."""
    return _ESTIMAND_DOC[estimand]


def knob_label(path: str) -> str:
    """Short display name for a knob — drops the ``optimization.`` /
    ``optimization.mechanisms.<group>.`` prefix. Shared by the CLI diagnostic and
    the webapp config-map so both name a knob identically."""
    short = path.removeprefix("optimization.")
    if short.startswith("mechanisms."):
        short = short.split(".", 2)[-1]
    return short


@dataclass(frozen=True, init=False)
class Knob:
    """A config field's self-description — what it shapes (:class:`Scope`) and which
    statistical quantities it moves (:class:`Estimand`). Rides the field as
    ``Annotated`` metadata, so a knob is declared exactly ONCE, where it is defined::

        pobb_epsilon: Annotated[float, Knob(Scope.POLICY, Estimand.STOPPING)] = Field(...)

    A field carrying a ``Knob`` is a **leaf** of the config tree whatever its shape —
    ``dataset_split`` is one knob, not two. A field without one whose annotation is a
    nested model is descended into; a field without one that is *not* a model is an
    undeclared knob and fails the walk in :mod:`promptpotter.application.knobs`.

    Metadata on the field cannot go stale against the field.
    """

    scope: Scope
    estimands: tuple[Estimand, ...]

    def __init__(self, scope: Scope, *estimands: Estimand) -> None:
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "estimands", estimands)


class SelectionMechanisms(StrictModel):
    """Hard-sample sorting & selection — how each round's scoring subset is chosen
    and ordered. Turn BOTH off to freeze the sample basis at campaign start: one
    fixed subset, fixed order, identical for every round and candidate."""

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
    """Early-abort / candidate-elimination rules that stop measuring a candidate —
    or gate round promotion — before the full sample budget is spent. Off → the
    mechanism never fires; candidates run their full budget. Numeric tuning for an
    enabled mechanism lives on the sibling `OptimizationConfig` fields."""

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
    """Pluggable orchestration mechanisms, grouped by kind. Each toggle turns one
    mechanism on/off; numeric tuning for an enabled mechanism lives on its
    `OptimizationConfig` field (`pobb_epsilon`, `pobb_lock_in`, …). Add a mechanism
    by adding a bool to the right group — it auto-surfaces to the webapp via the
    schema. (Patience-driven L1/L2/L3 escalation is governed separately by
    `l1_patience` / `l2_patience` / `l3_patience`; None disarms L2/L3.)"""

    selection: SelectionMechanisms = Field(default_factory=SelectionMechanisms)
    elimination: EliminationMechanisms = Field(default_factory=EliminationMechanisms)


class LivesConfig(StrictModel):
    """Improvement-banked round budget ("hearts"). Opt-in alternative to the fixed
    ``max_rounds`` calendar boundary: the run starts with ``start`` lives, banks one
    each round that improves and loses one each round that doesn't, and stops when the
    bank hits zero — so a compounding run chains itself more rounds and a stall dies
    fast. Rides the SAME per-round ``improved`` verdict that drives ``l1_stall_count``
    (no new verdict). ``max_rounds``/``HARD_CAP`` and the spend budget stay the absolute
    ceilings. See docs/specs/l4-outer-loop.md."""

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
    """Optimization-loop knobs. `improvement_threshold` + `degradation_threshold` are required (no default)."""

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
            "Which optimizer meta-prompt set this cycle uses. Empty (default) → the "
            "standard `datasets/_optimizer/` task-tuning loop. `meta` → the L4 outer "
            "set `datasets/_optimizer_meta/prompts.yaml`, whose L1 emits per-node "
            "edits to the INNER optimizer's meta-prompts (`pipeline_params_override`) "
            "instead of tuning its own template. Applied per-cycle at the runner seam "
            "through the same per-node override channel the inner runner uses, so an "
            "outer (meta) cycle and the inner (default) cycles it spawns stay isolated "
            "by task. See docs/specs/l4-outer-loop.md § 3."
        ),
    )

    replicate_survivors: Annotated[int, Knob(Scope.POLICY, Estimand.SEARCH, Estimand.SPEND)] = (
        Field(
            0,
            description=(
                "OPT-IN successive-halving replication (0 = off; the distributable default). "
                "When >0, after a round's scoring pass each SURVIVING candidate (reached the "
                "coverage floor, not PoBB-eliminated) is re-measured `replicate_survivors` more "
                "times with `force_fresh` (independent draws, cache bypassed); the estimators "
                "average the replicate rows per cell (`paired_fitness`/`cell_fitness`) and the "
                "Rasch θ fit consumes them natively (more item responses → tighter θ). Kills the "
                "idiosyncratic single-run inner-campaign draw that CRN cannot (the treatment "
                "changes the inner-prompt path, so its search noise is not common) — complementary "
                "to CRN, not a substitute. The origin reference is replicated too (its draws thread "
                "only into the decision estimators, not the display floor). Spends k times the "
                "survivor+origin budget, so it rides the elimination floor (losers already dropped). "
                "A DEV-STAGE tool: off in the distributable to keep it simple. See l4-outer-loop.md."
            ),
        )
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
    """Held-out test fold size — display metadata for the dashboard footer."""

    test: int = Field(description="Held-out test fold size — not in the bank or the table")


class CampaignConfig(StrictModel):
    """Top-level user-authored campaign configuration (``datasets/{name}/campaign.json``)."""

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
        """Resolved origin eval budget — ``sp_budget_origin``, or the per-round budget."""
        return self.sp_budget_origin or self.sp_budget_ttest


def load_campaign_config(raw: dict[str, Any] | CampaignConfig) -> CampaignConfig:
    if isinstance(raw, CampaignConfig):
        return raw
    return CampaignConfig.model_validate(raw)


def freeze_campaign_config(config: CampaignConfig) -> dict[str, Any]:
    """Serialize *config* for the ``campaign.json::config`` snapshot — the **delta** from defaults.

    Sole writer of the snapshot's shape. Only the leaves whose value differs from the code
    default are persisted, so a knob nobody ever set never appears — and renaming or dropping
    such a knob later cannot make the snapshot unloadable. Measured on 156 broken campaigns,
    this alone would have spared 41 of them.

    It is not a rename-proofing device: a field the operator *did* set still lands in the
    snapshot and still breaks under ``extra="forbid"`` if it is later renamed. That case is
    caught in CI by the frozen fixture in ``tests/test_resume.py`` and remedied by re-stamping
    (``scripts/restamp_campaign_configs.py``) — never by ``extra="allow"``, an alias, or a shim.

    The two ``improvement_threshold`` / ``degradation_threshold`` leaves are declared without a
    default, so they are always emitted and the delta always re-validates.
    """
    return config.model_dump(mode="json", exclude_defaults=True)


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
    cycle-level lock edit.

    Reads the two fields it consumes straight off the snapshot dict. Validating the
    *whole* snapshot to recover two of its ~38 leaves made every resume hostage to
    every other leaf: ``CampaignConfig`` is ``extra="forbid"``, so one renamed knob —
    even one nobody ever set — raised ``extra_forbidden`` here and no campaign minted
    before the rename could resume. A snapshot is a record of what was; only the
    fields actually read need to still be a thing."""
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
    """Structured pre-run warning surfaced before a campaign kicks off."""

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
    (``datasets/_optimizer/pipeline.yaml``), not a per-campaign copy."""
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
            "`datasets/_optimizer/pipeline.yaml` to a larger tier."
        ),
    )


def _check_config_couplings(config: CampaignConfig) -> list[PreflightWarning]:
    """Knob-collision warnings — config combinations where one knob makes another
    statistical quantity ill-defined or inert. The declared map lives in ``knobs``
    (also read by the webapp config-map endpoint);
    this is its pre-run CLI leg. Imported lazily to keep the statistical-constant
    imports off ``config``'s module-load path."""
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
    """Pure — no mutation, no I/O.

    ``target_models`` are the resolved per-node target/scoring model ids (from
    ``session.pipeline_params``); empty when the backend owns the model."""
    warnings: list[PreflightWarning] = []
    if (w := _check_sp_budget_vs_dataset(config, dataset)) is not None:
        warnings.append(w)
    if (w := _check_optimizer_below_target(target_models)) is not None:
        warnings.append(w)
    warnings.extend(_check_config_couplings(config))
    return warnings


def check_model_reasoning_floors(
    node_configs: Iterable[tuple[str, Mapping[str, Any]]],
) -> list[str]:
    """HARD-block violations: a node pinning a reasoning model with an EXPLICIT
    ``max_tokens`` below that model's profile floor (``ModelProfile.min_max_tokens``).

    Below the floor a reasoning model can spend its whole output budget thinking and
    emit zero content — a paid-for failure the runtime only ever catches post-hoc
    (``classify_result`` → ``reasoning_budget_exhausted``). Gating it here turns that
    into a refuse-to-start. An **absent** ``max_tokens`` is NOT a violation: that is the
    sanctioned default (provider ceiling, governed via ``reasoning_effort``), so this
    only fires on a config that deliberately set a too-low numeric cap. Pure — the
    per-model facts live in ``infrastructure/llm/registry.py::_MODEL_PROFILES``."""
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
    """The ONE per-node overlay merge → a fresh dict (``base`` untouched).

    For each ``node`` in *overlay*: when both the existing and the incoming value are
    dicts, the incoming keys win over the existing (``{**existing, **incoming}``);
    otherwise the incoming value is assigned as-is — so the reserved non-dict
    top-level ``steps`` list can't be spread and simply replaces. Sequential calls
    layer correctly (later overlay > earlier), which is how the resolution chain
    stacks dataset < campaign-override < cycle-seed.

    **A param declared ``param_types: object`` merges one level deeper**: the incoming
    keys win, the siblings it did not name SURVIVE — a candidate improving one
    ``output_schema_descriptions`` entry cannot revert the entries its parent earned, so
    the description axis accumulates across generations. Depth is bounded by the
    DECLARATION, never by sniffing ``isinstance``: an undeclared param keeps the
    node-level shallow semantics, and ``array`` replaces wholesale because a merged
    ordering is meaningless. This is the same per-slot contract ``resolve_node_layout``
    hand-rolls (named slot replaces, unnamed keeps the floor) — one nesting contract,
    not two.

    ``schema`` supplies those declarations; ``None`` (the schema-less
    backend-default path) declares nothing, so every param stays shallow.

    Shared by the dataset/override resolution here, the cycle-seed overlay
    (``runner/entry.py``) and the candidate-override merge
    (``optimization/l1/population.py::merge_pipeline_params`` — which deep-copies its
    base and drops inactive nodes AROUND this call, and is itself the single merge the
    live loop and the ``verify``/``ab`` replay verbs both ride)."""
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
    schema: PipelineSchema | None,
) -> dict[str, Any]:
    """The dataset→effective node-config merge, pure: the sparse ``{"steps": active}``
    base layered with the per-dataset overlay (``pipeline.yaml::nodes.{name}.config``) and
    the campaign's ``pipeline_overrides``, restricted to *active* nodes. Prompts and
    model validation are NOT here — they ride the ``OptSearchPoint`` / live ``Session``.

    This is the SINGLE definition of which node config a cycle id and measurement key hash.
    Shared by :func:`configure_and_apply_pipeline` (which adds prompts + validation + the
    session apply) and the ``GET /origins`` prospective-origin id, so the two can never
    silently diverge. Takes no live ``Session`` — resolvable from disk."""
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
    """The dataset's connector ``identity_config`` contribution, or ``{}``.

    Reads ``backend_type`` from the dataset's ``pipeline.yaml`` (tolerant — a
    dataset dir without one contributes nothing) and asks the registered
    connector. Import is local to keep ``config.py`` free of a module-level
    connectors dependency."""
    from promptpotter.connectors import CONNECTORS
    from promptpotter.infrastructure.store.dataset_access import dataset_pipeline_path
    from promptpotter.infrastructure.store.io import read_yaml_optional

    raw = read_yaml_optional(dataset_pipeline_path(dataset_dir))
    connector = CONNECTORS.get(str((raw or {}).get("backend_type") or ""))
    if connector is None or connector.identity_config is None:
        return {}
    return connector.identity_config(dataset_dir)


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
    *,
    exclude: list[str],
    narrowing: dict[str, NodeSearchNarrowing],
) -> tuple[list[str], PipelineSchema | None]:
    """Resolve the active node list + the exclude-filtered, campaign-narrowed schema.

    ``steps`` is two shapes under one word. Here it is the BACKEND's
    ``list[dict]`` (each ``{"name": ..., ...}``) out of ``GET /pipeline``. On
    ``pipeline_params`` it is the reserved top-level ``list[str]`` of active node
    names (``RESERVED_PIPELINE_PARAM_KEYS``). Hence the ``s["name"]`` below.
    """

    active = pipeline_schema.active_steps_excluding(exclude) if pipeline_schema else []

    filtered = pipeline_schema
    if pipeline_schema and exclude:
        filtered = pipeline_schema.filter_to_steps(active)
    # Campaign search-space narrowing — the per-node param-lock + allowed-values
    # subset, peer to exclude (above). The dataset declares the max; the campaign
    # snapshot may only narrow it.
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
                f"Declare it in the dataset's pipeline.yaml::nodes.{name}.config.model "
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
    from promptpotter.application.datasets.prompts import has_dataset_prompts

    exclude = list(campaign_config.exclude_nodes)
    active, filtered = _resolve_active_schema(
        session.pipeline_schema,
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
        active, campaign_config.pipeline_overrides, dataset_dir, filtered
    )

    # Starting prompts from `{dataset_dir}/prompts/[<node>|default].yaml`, per prompt-bearing node.
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
