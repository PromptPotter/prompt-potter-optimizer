from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import Field, field_validator

from promptpotter.domain.arm_reading import DisplayMetric
from promptpotter.domain.bench import BenchTrigger, DatasetSplit
from promptpotter.domain.campaign import (
    DeterminismClamp,
    Environment,
    Purpose,
    RunDeclaration,
    StoppingRule,
)
from promptpotter.domain.cells import HardSampleOrder
from promptpotter.domain.judges import JudgeSpec
from promptpotter.domain.paired_reading import LiftReference
from promptpotter.domain.pipeline_schema import ManifestNodeOverlay, NodeSearchNarrowing
from promptpotter.domain.scoring import MAX_PASS_ROWS
from promptpotter.domain.spend import Budget
from promptpotter.domain.strict_model import StrictModel

if TYPE_CHECKING:
    from promptpotter.domain.run_records import ConfigOverrides, CycleSeed


DEFAULT_ORIGIN_BUDGET: int = 40

OriginGateMode = Literal["strict", "critical_only", "off"]
PanelGateMode = Literal["strict", "off"]
ArmGateMode = Literal["off", "review", "author"]


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


class LivesConfig(StrictModel):
    """Improvement-banked round budget: banks a life each round that promotes an arm, loses one
    each round that does not. ``budget.rounds`` and spend stay the ceilings."""

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
        """``(start, cap)``, as ``domain/run_standing.py::stalls_left`` folds it."""
        return (self.start, self.cap)


_DEFAULT_BUDGET = Budget(usd=0.025, rounds=50)


def over_the_default(budget: Budget) -> Budget:
    """The default is the bottom layer: a declaration naming one axis keeps the others."""
    return _DEFAULT_BUDGET.over(budget)


def named_over_the_default(budget: Budget) -> Budget:
    """Only the axes where *budget* differs from the default: a declaration restates none."""
    return budget.only(
        axis
        for axis in budget.named
        if axis not in _DEFAULT_BUDGET.named
        or getattr(budget, axis) != getattr(_DEFAULT_BUDGET, axis)
    )


class OptimizationConfig(StrictModel):
    """The bench's own loop config, plus which optimizer runs and its overlay. An optimizer's knobs
    are its nodes' ``config`` in its manifest, never fields here."""

    @field_validator("budget", mode="before")
    @classmethod
    def _over_the_default(cls, given: object) -> Budget:
        return over_the_default(Budget.model_validate(given))

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
    budget: Annotated[Budget, Knob(Scope.POLICY, Estimand.SPEND)] = Field(
        _DEFAULT_BUDGET,
        description=(
            "The campaign's own layer of the run's budget. ``usd`` / ``tokens`` bound the "
            "CAMPAIGN's cumulative spend (optimizer + backend), shared by its live cycles and "
            "checked at the next boundary; ``rounds`` caps one cycle's closed rounds after the "
            "origin (0 measures the origin and stops; null leaves only ``HARD_CAP_ARMS``); "
            "``wall_clock_s`` bounds one launch, cutting a remote cell still open at its edge. "
            "A fork seed, a standing ``set-limits`` and a launch flag each lay over it axis by "
            "axis, and the account admits the result whole or refuses the launch. A null axis "
            "is unbounded. ``tokens`` is unset by default: a token is worth a different amount "
            "on every route, so set it only where a backend bills but reports no cost "
            "(``unpriced_tokens`` on the dashboard is the tell)."
        ),
    )
    lives: LivesConfig | None = Field(
        None,
        description=(
            "Opt-in improvement-banked round budget ('hearts'), under any optimizer: +1 life per "
            "round that promotes an arm, -1 per round that does not, banked up to ``cap``, and "
            "the run stops ``lives_exhausted`` at 0. A round no arm of which reached the election "
            "banks nothing. ``None`` → ``budget.rounds`` governs. It still caps from above, so a "
            "lives run wanting the full bank sets ``budget: {rounds: null}``."
        ),
    )
    halt_at_accuracy: Annotated[float | None, Knob(Scope.POLICY, Estimand.STOPPING)] = Field(
        None,
        ge=0.0,
        le=1.0,
        description=(
            "Stop ``target_hit`` once the declared pick's own accuracy reaches this. ``None`` "
            "→ no such stop. Refused on an arm of a head-to-head, which stops only on its "
            "record's rule."
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
            "``converged``, under any optimizer. A promotion resets it, separated from the "
            "origin or not; a round whose origin-panel reading was not taken counts neither "
            "way. ``None`` → no such stop."
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

    panel_gate: Annotated[PanelGateMode, Knob(Scope.POLICY, Estimand.GATE)] = Field(
        "strict",
        description=(
            "Halt at the end of a round whose electable candidates carry HOLES — cells "
            "the loop attempted and got no measurement back from, or a spend or token "
            "ceiling left unbought — instead of electing a "
            "winner from a comparison whose arms ran different cell sets. ``strict`` "
            "(default) halts on any hole; ``off`` disarms, and a selector that can elect "
            "on a cut panel then does. The halt is resumable and the "
            "round is not persisted, so a ``resume`` re-runs it once the bound that cut "
            "the cells has room: the cached "
            "candidates replay, the missing cells are re-measured, and the round decides "
            "on a complete panel. A PoBB elimination is NOT a hole (a rule stopped that "
            "arm on evidence) and neither is a classifier-deprecated sample."
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

    arm_gate: Annotated[ArmGateMode, Knob(Scope.POLICY, Estimand.GATE)] = Field(
        "off",
        description=(
            "Hold every round, before any arm is measured, until the operator answers with "
            "the arms it measures (`submit-arms`). ``off`` (default) never holds. "
            "``review``: the proposers run, their proposals are drafts, and the operator "
            "keeps any of them by index, adds arms of their own, or both. ``author``: no "
            "proposer runs and every arm is the operator's. An arm of theirs is an edit of "
            "the round's parent — prompt fields, a pipeline overlay — and is measured and "
            "elected like any other. The hold has no deadline; a pause ends it resumably, "
            "and a resume holds again unless an answer was already banked."
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
    determinism: Annotated[DeterminismClamp | None, Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
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
    def stopping(self) -> StoppingRule:
        return StoppingRule(
            lives=None if self.lives is None else self.lives.model_dump(mode="json"),
            convergence_patience=self.convergence_patience,
        )


class CampaignConfig(StrictModel):
    sp_budget_origin: Annotated[int, Knob(Scope.POLICY, Estimand.SELECTION)] = Field(
        DEFAULT_ORIGIN_BUDGET,
        ge=1,
        le=MAX_PASS_ROWS,
        description="Origin eval budget — how many bank samples the origin (C0) is "
        "scored on at check-in. The bench's, whatever the optimizer: a round's own panel is "
        "its sampler's knob. Wide because origin breadth is the one breadth that is nearly "
        "free: θ_origin is the term EVERY delta subtracts, and its rows are "
        "content-addressed cache replayed into every candidate arm, fork and resume — "
        "paid once per config. Candidate breadth is paid per candidate, per round. "
        "Every comparison downstream is matched by sample key or θ-space, so an origin "
        "scored on a superset stays like-for-like. A bank smaller than this scores on "
        "the whole bank (`sample_dataset` is a prefix slice) — not an error.",
    )
    exclude_nodes: Annotated[list[str], Knob(Scope.DATA, Estimand.SEARCH)] = Field(
        default_factory=list
    )
    task_framing: Annotated[Literal["committed", "off"], Knob(Scope.DATA, Estimand.SEARCH)] = Field(
        "committed",
        description="Which task framing every target render splices in. `committed` "
        "(default) runs under the front matter of the dataset's `task.md`, which the first mint "
        "decomposes from its body when none is committed yet. `off` runs "
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
        "`provider`/`route_order` are always locked. **`param_keys` SUBSETS the config axes, "
        "`prompt_fields_held` holds the prompt fields it names, and `param_allowed_values` "
        "REPLACES** (`PipelineSchema.narrow`, applied at pipeline setup): a campaign may only "
        "close axes the dataset opened, but the value space is "
        "its own statement — which is what lets an operator ADD a model or a reasoning rung "
        "no `dataset.yaml::pipeline` on disk carries. `param_allowed_values['model']` is also the ONE "
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
        "dataset owner can declare one (`dataset.yaml::campaign`) and a "
        "guessed value makes every campaign on it publish a round count nobody can defend. "
        "DISPLAY config — it moves no gate, no selection and no stop.",
    )
    dataset_split: Annotated[DatasetSplit | None, Knob(Scope.DATA, Estimand.SELECTION)] = Field(
        None,
        description="How run init partitions the bank into the search split every optimizer draw "
        "reads, the held-out bench set the headline is scored on, and a demo pool. `None` "
        "holds nothing out: the whole bank is the search split and there is no bench score.",
    )
    run_seed: Annotated[int, Knob(Scope.DATA, Estimand.SELECTION)] = Field(
        0,
        description="The run's one seed. It seeds which rows fall where and the order the search "
        "pool reads in: each row ranks by this and its content (`Sample.key`), never its slot, "
        "so a reordered bank holds out the same rows and reads them in the same order. An L4 "
        "seat seeds its determinism clamp and its solver node from it too.",
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

    def identity(self) -> dict[str, object]:
        """What this config adds to a cell's identity beside its optimizer's ``Treatment``, which
        resolves ``optimizer`` and ``nodes`` against the manifest: less those, and less every
        default, so which file states a value — or whether one restates a default — moves no cell."""
        return self.model_dump(mode="json", exclude_defaults=True, exclude=_TREATMENT_OWNED)


def merge_config_layers(base: dict[str, object], over: Mapping[str, object]) -> dict[str, object]:
    """Depth-first; a layer naming another ``optimizer`` drops the base's ``nodes``, which addressed the old one."""
    base_opt = base.get("optimization")
    over_opt = over.get("optimization")
    if isinstance(base_opt, dict) and isinstance(over_opt, Mapping) and "optimizer" in over_opt:
        held = base_opt.get("optimizer", OptimizationConfig.model_fields["optimizer"].default)
        if over_opt["optimizer"] != held:
            base = {**base, "optimization": {k: v for k, v in base_opt.items() if k != "nodes"}}
    return _merge_dicts(base, over)


def _merge_dicts(base: dict[str, object], over: Mapping[str, object]) -> dict[str, object]:
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


_TREATMENT_OWNED: dict[str, Any] = {"optimization": {"optimizer": True, "nodes": True}}

# What the target runs on and is graded by: the declaration's environment, every other key its loop.
_ENVIRONMENT_KEYS = frozenset(
    {"pipeline_overlay", "exclude_nodes", "task_framing", "scoring", "judges"}
)


def declare_run(
    config: CampaignConfig,
    *,
    purpose: Purpose,
    dataset_hash: str,
    connector: str,
    bench_defaults_pin: str,
) -> RunDeclaration:
    """Every door's config becomes ONE declaration: the keys its layers SET, never a default."""
    named = config.model_dump(mode="json", exclude_unset=True)
    optimization = {k: v for k, v in named.pop("optimization").items() if k != "budget"}
    run_seed = named.pop("run_seed", None)
    return RunDeclaration(
        purpose=purpose,
        dataset_hash=dataset_hash,
        run_seed=run_seed,
        loop={
            **{k: v for k, v in named.items() if k not in _ENVIRONMENT_KEYS},
            "optimization": optimization,
        },
        budget=named_over_the_default(config.optimization.budget),
        environment=Environment(
            connector=connector,
            target={k: v for k, v in named.items() if k in _ENVIRONMENT_KEYS},
        ),
        bench_defaults_pin=bench_defaults_pin,
    )


def effective_config(declaration: RunDeclaration) -> CampaignConfig:
    """The ONE derivation of what a declaration runs: today's defaults under its parts."""
    loop = declaration.loop
    seed = {} if declaration.run_seed is None else {"run_seed": declaration.run_seed}
    return CampaignConfig.model_validate(
        {
            **loop,
            **declaration.environment.target,
            **seed,
            "optimization": {**loop["optimization"], "budget": declaration.budget},
        }
    )


def _scoring_block(scoring: str | dict[str, str] | None) -> dict[str, str]:
    if isinstance(scoring, dict):
        return dict(scoring)
    return {"per_sample": scoring} if scoring else {}


def apply_config_overrides(config: CampaignConfig, overrides: ConfigOverrides) -> CampaignConfig:
    """ABSOLUTE values, laid axis by axis over the budget; the account admits it before launch."""
    opt_updates: dict[str, Any] = (
        {"budget": config.optimization.budget.over(overrides.budget)}
        if overrides.budget.named
        else {}
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
