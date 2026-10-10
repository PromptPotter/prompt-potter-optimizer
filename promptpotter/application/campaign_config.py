from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import Field

from promptpotter.domain.bench import BenchTrigger, DatasetSplit
from promptpotter.domain.campaign import ArmBudget, HeadToHeadRecord
from promptpotter.domain.paired_reading import LiftReference
from promptpotter.domain.pipeline_schema import ManifestNodeOverlay, NodeSearchNarrowing
from promptpotter.domain.results import DisplayMetric, HardSampleOrder
from promptpotter.domain.spend import SpendCeilings
from promptpotter.domain.strict_model import StrictModel
from promptpotter.judges.protocol import JudgeSpec

if TYPE_CHECKING:
    from promptpotter.domain.run_records import ConfigOverrides, CycleSeed

__all__ = [
    "DEFAULT_ORIGIN_BUDGET",
    "CampaignConfig",
    "DeterminismClamp",
    "Estimand",
    "Knob",
    "LivesConfig",
    "OptimizationConfig",
    "OriginGateMode",
    "PanelGateMode",
    "Scope",
    "apply_config_overrides",
    "apply_cycle_seed",
    "estimand_doc",
    "knob_label",
    "load_campaign_config",
    "merge_config_layers",
    "merge_node_overlays",
    "under_record",
]


DEFAULT_ORIGIN_BUDGET: int = 40

OriginGateMode = Literal["strict", "critical_only", "off"]
PanelGateMode = Literal["strict", "off"]


class Scope(StrEnum):
    POLICY = "policy"
    DATA = "data"
    IDENTITY = "identity"


class Estimand(StrEnum):
    """The statistical quantity a knob moves. **Declaration order IS presentation order** — the
    CLI config map and the webapp panel iterate this enum; never copy members into an ordering tuple."""

    SELECTION = "selection"
    DIFFICULTY = "difficulty"
    DISCRIMINATION = "discrimination"
    ABILITY = "ability"
    GATE = "gate"
    STOPPING = "stopping"
    CONTROLLER = "controller"
    SEARCH = "search"
    SPEND = "spend"
    DISPLAY = "display"


_ESTIMAND_DOC: dict[Estimand, str] = {
    Estimand.SELECTION: "Which samples get scored each round — the subset the fitness is measured over.",
    Estimand.DIFFICULTY: "The per-sample difficulty ruler δ (1PL Rasch) used to difficulty-adjust scores.",
    Estimand.DISCRIMINATION: "The per-sample discrimination aₛ (2PL) — how sharply a sample separates able from unable candidates; only estimated where a data-rich dataset graduates.",
    Estimand.ABILITY: "The candidate ability θ — difficulty-adjusted skill, the metric the gate compares.",
    Estimand.GATE: "The round-promotion / improvement gate — what counts as 'better' and is kept.",
    Estimand.STOPPING: "What stops measuring before the budget is spent — an arm before its panel (elimination), the run before its round cap (lives, convergence).",
    Estimand.CONTROLLER: "An optimizer's controller — when it changes strategy between rounds.",
    Estimand.SEARCH: "The optimizer search space + data binding the loop explores.",
    Estimand.SPEND: "The budget ceilings (USD / tokens) that halt the cycle.",
    Estimand.DISPLAY: "What number the operator reads — no effect on the data or the decision.",
}


def estimand_doc(estimand: Estimand) -> str:
    return _ESTIMAND_DOC[estimand]


def knob_label(path: str) -> str:
    short = path.removeprefix("optimization.")
    if short.startswith("nodes."):
        node, _, knob = short.removeprefix("nodes.").partition(".config.")
        return f"{node}.{knob}"
    return short


@dataclass(frozen=True, init=False)
class Knob:
    """``Annotated`` metadata declaring a knob; a field carrying one is a LEAF whatever its shape."""

    scope: Scope
    estimands: tuple[Estimand, ...]

    def __init__(self, scope: Scope, *estimands: Estimand) -> None:
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "estimands", estimands)


class DeterminismClamp(StrictModel):
    """What a campaign PINS on every optimizer call — how the draw is made and which host makes
    it. Applied last, so it beats the node's file config and any per-call override alike."""

    temperature: Annotated[float | None, Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        None,
        ge=0.0,
        le=2.0,
        description=(
            "Sampling temperature EVERY optimizer node runs at, overriding its own — including "
            "`l1_generate`'s `temperature`, the dominant run-to-run noise source. "
            "`None` (default) leaves each node the value its pipeline file declares."
        ),
    )
    seed: Annotated[int | None, Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        None,
        description=(
            "Sampling seed every optimizer node sends. Temperature 0 pins the distribution and "
            "not the draw, so without a seed the provider is still free to sample differently "
            "on identical input. It rides `hash_call`, so two campaigns differing only here "
            "bank separate replies rather than serving one campaign's answer under the other's "
            "name. `None` (default) sends none."
        ),
    )
    route_order: Annotated[list[str] | None, Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        None,
        description=(
            "The upstream HOSTS behind the gateway, in order, every optimizer call is routed "
            "to (the gateway's own `provider_name`s — read them off `served_by` in the ledger, "
            "never from a catalogue). Hosts of one model disagree systematically, so an "
            "unpinned route makes a measurement whose producer nothing can name. `None` "
            "(default) lets the gateway re-rank per call."
        ),
    )


class LivesConfig(StrictModel):
    """Improvement-banked round budget: banks a life each round that promotes an arm, loses one
    each round that does not. ``max_rounds`` and spend stay the ceilings."""

    start: Annotated[int, Knob(Scope.POLICY, Estimand.STOPPING, Estimand.SPEND)] = Field(
        2,
        ge=1,
        description="Lives a run starts with (a fully-stalling run does exactly this many rounds).",
    )
    cap: Annotated[int, Knob(Scope.POLICY, Estimand.STOPPING, Estimand.SPEND)] = Field(
        4,
        ge=1,
        description="Bank ceiling — lives never exceed this no matter how long the improving streak runs.",
    )

    @property
    def bank(self) -> tuple[int, int]:
        """``(start, cap)``, as ``domain/results.py::stalls_left`` folds it."""
        return (self.start, self.cap)


class OptimizationConfig(StrictModel):
    """The bench's own loop config, plus which optimizer runs and its overlay. An optimizer's knobs
    are its nodes' ``config`` in its manifest, never fields here."""

    optimizer: Annotated[str, Knob(Scope.IDENTITY, Estimand.SEARCH)] = Field(
        "potter",
        min_length=1,
        description=(
            "The optimizer manifest this campaign runs — a directory under "
            "``promptpotter/assets/optimizers/``. Every entry point selects it here; nothing "
            "selects it install-wide."
        ),
    )
    nodes: Annotated[dict[str, ManifestNodeOverlay], Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        default_factory=dict,
        description=(
            "This campaign's overlay on the selected manifest, keyed by node name — "
            "``{node: {config: {...}}}``, the shape a target pipeline's overlay takes. An "
            "optimizer's knobs, a paper's configuration and each llm node's ``config.model`` "
            "all ride here; each node validates its own knobs when the optimizer is selected."
        ),
    )
    max_rounds: Annotated[int | None, Knob(Scope.POLICY, Estimand.SPEND)] = Field(
        50,
        ge=0,
        description=(
            "Max rounds. 0 = measure the origin (round 0) and stop — the "
            "origin-only run; None = unlimited, bounded only by ``HARD_CAP_ARMS``, the arms "
            "raced across the run."
        ),
    )
    lives: LivesConfig | None = Field(
        None,
        description=(
            "Opt-in improvement-banked round budget ('hearts'), under any optimizer: +1 life per "
            "round that promotes an arm, -1 per round that does not, banked up to ``cap``, and "
            "the run stops ``lives_exhausted`` at 0. A round no arm of which reached the election "
            "banks nothing. ``None`` → ``max_rounds`` governs. ``max_rounds`` still caps from "
            "above, so a lives run wanting the full bank sets ``max_rounds: null``."
        ),
    )
    convergence_patience: Annotated[
        int | None, Knob(Scope.POLICY, Estimand.STOPPING, Estimand.SPEND)
    ] = Field(
        None,
        ge=1,
        description=(
            "Consecutive closed rounds without an advance of the best-so-far line on the origin "
            "panel (``RunStanding.rounds_without_advance``) after which the run stops "
            "``converged``, under any optimizer. A promotion that has not separated from the "
            "origin counts toward it; a round whose origin-panel reading was not taken counts "
            "neither way. ``None`` → no such stop."
        ),
    )
    degradation_threshold: Annotated[float, Knob(Scope.POLICY, Estimand.STOPPING)] = Field(...)
    degradation_fatal_fastpath: Annotated[bool, Knob(Scope.POLICY, Estimand.STOPPING)] = Field(
        True,
        description=(
            "End a candidate at the first FATAL sample (empty response, "
            "content-filtered, structurally broken) without spending the rest of "
            "its budget. Active while the degradation check runs "
            "(`degradation_threshold` > 0); the rate-based check stays governed by "
            "that threshold. The bench's, run on every optimizer's arms."
        ),
    )
    elimination_n_min: Annotated[
        int, Knob(Scope.POLICY, Estimand.STOPPING, Estimand.ABILITY, Estimand.DIFFICULTY)
    ] = Field(
        6,
        description="Minimum distinct samples before θ is read as ability: the δ ruler stays "
        "flat below it, and no eliminator cuts an arm (nor a selector crowns one) on fewer — "
        "the floor on n for a posterior to be meaningful.",
    )

    ceiling: Annotated[SpendCeilings, Knob(Scope.POLICY, Estimand.SPEND)] = Field(
        SpendCeilings(usd=0.025),
        description=(
            "Halt this cycle when its cumulative spend (optimizer + backend) reaches "
            "``usd`` dollars or ``tokens`` tokens (input + output), whichever trips first, "
            "at the next round boundary. The campaign's own layer of the run's budget: a "
            "fork seed, a standing ``set-limits`` and a launch flag (CLI ``--spend-budget`` "
            "/ ``--token-budget``) each SET over it, raise or lower, and the account admits "
            "the result whole or refuses the launch. A null arm is disarmed, and a declared "
            "pair is WHOLE: an arm it leaves out is null, never the default. Default "
            "``usd`` ≈ a 5-round run on the free-backend setup (measured ~$0.019) with "
            "headroom; raise ``max_rounds`` and let this be the binding limit. ``tokens`` "
            "defaults disarmed: a token is worth a different amount on every route, so a "
            "token cap sized for one model silently becomes a different budget on the "
            "next, and dollars are the model-invariant measure every route we run reports. "
            "Set it for the one case USD cannot see — a backend that BILLS but reports no "
            "cost, where spend reads low and ``unpriced_tokens`` on the dashboard is the "
            "tell."
        ),
    )

    panel_gate: Annotated[PanelGateMode, Knob(Scope.POLICY, Estimand.GATE)] = Field(
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

    lift_reference: Annotated[LiftReference, Knob(Scope.POLICY, Estimand.GATE)] = Field(
        "best_so_far",
        description=(
            "What each arm's lift is read against — member `a` of its `vs_reference` reading. "
            "``best_so_far`` (default): the "
            "round's selected best-so-far individual, re-scored on the round's panel and paired "
            "with the arm on the cells both measured. ``parents``: the arm's own `parent_ids`, "
            "each re-measured on exactly the cells the arm measured; a crossover child is read "
            "against the better of its parents there, the bar it must clear to have added "
            "anything over what it recombined. An arm with no parent has no reference."
        ),
    )

    origin_gate: Annotated[OriginGateMode, Knob(Scope.POLICY, Estimand.GATE)] = Field(
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
    determinism: DeterminismClamp | None = Field(
        None,
        description=(
            "Pin the optimizer's decoding and its route so a re-run reproduces this "
            "campaign's trajectory and can name the host that produced each number. `None` "
            "(default) → every node runs at its own file settings and the gateway routes per "
            "call. An L4 inner cell is one caller among the rest: its panel's "
            "`inner_optimizer_temperature` and its cell seed arrive here."
        ),
    )

    @property
    def arm_budget(self) -> ArmBudget:
        """What a head-to-head declares equal per arm, read off these knobs."""
        return ArmBudget(
            usd=self.ceiling.usd,
            max_rounds=self.max_rounds,
            determinism=None
            if self.determinism is None
            else self.determinism.model_dump(mode="json"),
            lives=None if self.lives is None else self.lives.model_dump(mode="json"),
            convergence_patience=self.convergence_patience,
        )


class CampaignConfig(StrictModel):
    dataset_name: Annotated[str, Knob(Scope.DATA, Estimand.SEARCH)] = Field("")
    sp_budget_origin: Annotated[int, Knob(Scope.POLICY, Estimand.SELECTION)] = Field(
        DEFAULT_ORIGIN_BUDGET,
        ge=1,
        description="Origin eval budget — how many bank samples the origin (C0) is "
        "scored on at check-in. The bench's, whatever the optimizer: a round's own panel is "
        "its sampler's knob. Wide because origin breadth is the one breadth that is nearly "
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
    task_framing: Annotated[Literal["committed", "off"], Knob(Scope.DATA, Estimand.SEARCH)] = Field(
        "committed",
        description="Which task framing every target render splices in. `committed` "
        "(default) runs under the dataset's `task_context.yaml`, which the first mint "
        "decomposes from `task_description.md` when none is committed yet. `off` runs "
        "UNFRAMED on purpose — the ablation arm — even where framing exists, and the campaign "
        "manifest keeps the delta, so the result never reads as a framed one.",
    )
    pipeline_overlay: Annotated[dict[str, Any], Knob(Scope.DATA, Estimand.SEARCH)] = Field(
        default_factory=dict
    )
    optimizer_narrowing: Annotated[
        dict[str, NodeSearchNarrowing], Knob(Scope.DATA, Estimand.SEARCH)
    ] = Field(
        default_factory=dict,
        description="Per-node declaration over the dataset's optimizer search space — the "
        "per-campaign param-lock + value-space lever beside `exclude_nodes` (whole node). "
        "`provider`/`route_order` are always locked. **`param_keys` SUBSETS and "
        "`param_allowed_values` REPLACES** (`PipelineSchema.narrow`, applied at pipeline "
        "setup): a campaign may only close axes the dataset opened, but the value space is "
        "its own statement — which is what lets an operator ADD a model or a reasoning rung "
        "no `pipeline.yaml` on disk carries. `param_allowed_values['model']` is also the ONE "
        "permitted model set: what the optimizer may pick, and what a human fork may steer to "
        "without tainting the branch. A steer outside it is a `campaign.babysit` act — warned "
        "+ graded C (`overlay_sets_model_outside_allowed`). Nothing declared for a node = "
        "nothing sanctioned there, the restrictive default.",
    )
    scoring: Annotated[str | dict[str, str] | None, Knob(Scope.DATA, Estimand.GATE)] = Field(None)
    judges: Annotated[dict[str, JudgeSpec], Knob(Scope.DATA, Estimand.GATE)] = Field(
        default_factory=dict,
        description="LLM-as-judge graders for datasets whose answer is free text, where no "
        "deterministic matcher can grade a cell. Each value names a registered judge "
        "(`promptpotter.judges`) and the models to run it on; its verdict is banked as a "
        "per-sample observation the `scoring` formula reads. "
        "**The KEY is the term that formula reads, not the judge's name** — so one cell can "
        "carry several graded terms, which is what a multi-step schema (retrieve -> ground -> "
        "answer) is, and two terms may run the same rubric on different models without one "
        "verdict landing on top of the other. Declaration order is the step order. "
        "`Scope.DATA` because swapping a judge invalidates every verdict taken under the "
        "old one, so a resume must run divergence detection rather than carry them forward. "
        "**Their models are declared here and inherited from nowhere** — not from "
        "the permitted model set, not from the pipeline's node config, not from the optimizer's own "
        "LLMs. A judge is a ruler, and a ruler that moved with whatever the search was last "
        "steered to would not be one.",
    )
    display_metric: Annotated[DisplayMetric, Knob(Scope.POLICY, Estimand.DISPLAY)] = Field(
        "accuracy",
        description="Which fitness number the operator's round and candidate views display "
        "first (lineage node value, Best tile, sidebar). DISPLAY config, not search state — "
        "the optimizer's selector decides on its own objective, and the campaign's headline "
        "is the bench score; this only picks the number the human READS, client-overridable "
        "per session. `ability` shows θ (a logit, jargon) where the selector stamps one — "
        "defaults to `accuracy` so θ is never forced on an operator who didn't ask for it. "
        "Rides the `composite_fitness_formula` serve path to "
        "`dashboard.json::display_metric`; never on `OptSearchPoint`.",
    )
    hard_sample_order: Annotated[HardSampleOrder, Knob(Scope.POLICY, Estimand.DISPLAY)] = Field(
        "info_gain",
        description="Which key ranks the hard-sample leaderboard — the heatmap, the table "
        "and the `log.md` heatmap, which all read ONE served rank. `info_gain` sorts by the "
        "queue mechanism's acquisition score (`pick_value`: decision-information gain + δ "
        "learning gain) read on the cycle's ruler at the frontier's stamped ability, "
        "contested-at-the-frontier samples first; `difficulty` sorts by the ruler's δ_s alone, "
        "hardest first, and is what a scope with no stamped ability (dataset scope) is served "
        "whatever this says. DISPLAY config: it picks the order the human "
        "READS and never what the engine scores, which is `build_round_order` and reaches no "
        "knob. Client-overridable per session, like `display_metric`.",
    )
    accuracy_ceiling: Annotated[float | None, Knob(Scope.POLICY, Estimand.DISPLAY)] = Field(
        None,
        gt=0.0,
        le=1.0,
        description="The accuracy a best-reachable prompt would score on this dataset at this "
        "campaign's model. `index.json::final.rounds_to_ceiling` counts rounds against "
        "`CEILING_FRACTION` of it, and the value is banked beside that count so a reader never "
        "has to guess the denominator. `None` (default) → the clock reports nothing, which is the "
        "honest reading: a ceiling is a joint claim about the dataset AND the model, so only the "
        "dataset owner can declare one (`datasets/{slug}/campaign.yaml::campaign_config`) and a "
        "guessed value makes every campaign on it publish a round count nobody can defend. "
        "DISPLAY config — it moves no gate, no selection and no stop.",
    )
    dataset_split: Annotated[DatasetSplit | None, Knob(Scope.DATA, Estimand.SELECTION)] = Field(
        None,
        description="How run init partitions the bank into the search pool every optimizer draw "
        "reads, the held-out bench set the headline is scored on, and a demo pool. `None` "
        "holds nothing out: the whole bank is the search pool and there is no bench score.",
    )
    bench_trigger: Annotated[BenchTrigger, Knob(Scope.POLICY, Estimand.SPEND)] = Field(
        "manual",
        description="When the line's bench passes are sent — each a cell per bench row, the "
        "ranked `dataset_split.bench` and every bench-only row the bank declares. `manual` "
        "(default): a launch sends none and sets nothing aside for one; the operator asks for "
        "the headline (CLI `bench`, command `grade-bench`), which grades the origin and the "
        "selection as they then stand. `at_end`: the origin's pass at launch, its price set "
        "aside under the ceiling, and the selection's once the run ends. `each_round`: that, "
        "and every round that selects a new individual is graded too, so the trend carries a "
        "held-out series beside the round composite. An arm of a head-to-head is minted "
        "`at_end` where this says `manual`: its comparison is the bench score.",
    )

    optimization: OptimizationConfig

    def frozen(self, *, arm: bool) -> dict[str, Any]:
        """Sole writer of ``campaign.json::config``: the WHOLE config the campaign was minted to
        run, so no later edit to its dataset's files reaches a resume, a fork or a served read of
        it — less, for an *arm*, what its head-to-head's record owns (:func:`under_record`)."""
        return self.model_dump(mode="json", exclude=_RECORD_OWNED if arm else None)


def load_campaign_config(raw: dict[str, Any] | CampaignConfig) -> CampaignConfig:
    if isinstance(raw, CampaignConfig):
        return raw
    return CampaignConfig.model_validate(raw)


def merge_config_layers(base: dict[str, Any], over: Mapping[str, Any]) -> dict[str, Any]:
    """Depth-first; a layer naming another ``optimizer`` drops the base's ``nodes``, which addressed the old one."""
    base_opt = base.get("optimization")
    over_opt = over.get("optimization")
    if isinstance(base_opt, dict) and isinstance(over_opt, Mapping) and "optimizer" in over_opt:
        held = base_opt.get("optimizer", OptimizationConfig.model_fields["optimizer"].default)
        if over_opt["optimizer"] != held:
            base = {**base, "optimization": {k: v for k, v in base_opt.items() if k != "nodes"}}
    return _merge_dicts(base, over)


def _merge_dicts(base: dict[str, Any], over: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in over.items():
        current = out.get(key)
        out[key] = (
            _merge_dicts(current, value)
            if isinstance(current, dict) and isinstance(value, Mapping)
            else value
        )
    return out


def merge_node_overlays(
    base: Mapping[str, ManifestNodeOverlay], over: Mapping[str, ManifestNodeOverlay]
) -> dict[str, ManifestNodeOverlay]:
    out = dict(base)
    for node, overlay in over.items():
        held = out[node].config if node in out else {}
        out[node] = ManifestNodeOverlay(config={**held, **overlay.config})
    return out


_RECORD_OWNED: dict[str, Any] = {
    "dataset_split": True,
    "optimization": {
        "ceiling": {"usd": True},
        "max_rounds": True,
        "determinism": True,
        "lives": True,
        "convergence_patience": True,
    },
}


def under_record(config: CampaignConfig, record: HeadToHeadRecord) -> CampaignConfig:
    budget = record.budget
    optimization = type(config.optimization).model_validate(
        {
            **config.optimization.model_dump(),
            "ceiling": replace(config.optimization.ceiling, usd=budget.usd),
            "max_rounds": budget.max_rounds,
            "determinism": budget.determinism,
            "lives": budget.lives,
            "convergence_patience": budget.convergence_patience,
        }
    )
    return config.model_copy(
        update={"optimization": optimization, "dataset_split": record.bench_set.split}
    )


def _scoring_block(scoring: str | dict[str, str] | None) -> dict[str, str]:
    if isinstance(scoring, dict):
        return dict(scoring)
    return {"per_sample": scoring} if scoring else {}


def apply_config_overrides(config: CampaignConfig, overrides: ConfigOverrides) -> CampaignConfig:
    """ABSOLUTE values; a seed's budget is admitted before launch, not here."""
    opt_updates: dict[str, Any] = (
        {"max_rounds": overrides.max_rounds} if overrides.max_rounds is not None else {}
    )
    if overrides.nodes:
        opt_updates["nodes"] = merge_node_overlays(config.optimization.nodes, overrides.nodes)
    top_updates: dict[str, Any] = {}
    if isinstance(overrides.scoring, dict):
        top_updates["scoring"] = {**_scoring_block(config.scoring), **overrides.scoring}
    elif overrides.scoring:
        top_updates["scoring"] = overrides.scoring
    if not opt_updates and not top_updates:
        return config
    if opt_updates:
        top_updates["optimization"] = config.optimization.model_copy(update=opt_updates)
    return config.model_copy(update=top_updates)


def apply_cycle_seed(config: CampaignConfig, seed: CycleSeed | None) -> CampaignConfig:
    if seed is None:
        return config
    config = apply_config_overrides(config, seed.config_overrides)
    return config.model_copy(
        update={"optimizer_narrowing": {**config.optimizer_narrowing, **seed.optimizer_narrowing}}
    )
