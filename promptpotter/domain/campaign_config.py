from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import Field, TypeAdapter, field_validator

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
from promptpotter.domain.pipeline_overlay import steers_disallowed_model
from promptpotter.domain.pipeline_params import apply_node_overlay
from promptpotter.domain.pipeline_schema import (
    KnobLayer,
    KnobLayers,
    ManifestNodeOverlay,
    NodeSearchNarrowing,
)
from promptpotter.domain.run_records import ConfigOverrides, CycleSeed, ScoringLockedRecord
from promptpotter.domain.scoring import MAX_PASS_ROWS
from promptpotter.domain.spend import Budget
from promptpotter.domain.strict_model import FrozenModel, StrictModel

DEFAULT_ORIGIN_BUDGET: int = 40

OriginGateMode = Literal["strict", "critical_only", "off"]
PanelGateMode = Literal["strict", "off"]
ArmGateMode = Literal["off", "review", "author"]


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


def knob_label(path: str) -> str:
    short = path.removeprefix("optimization.")
    if short.startswith("nodes."):
        node, _, knob = short.removeprefix("nodes.").partition(".config.")
        return f"{node}.{knob}"
    return short


@dataclass(frozen=True, init=False)
class Knob:
    """``Annotated`` metadata declaring a knob; a field carrying one is a LEAF whatever its shape."""

    estimands: tuple[Estimand, ...]

    def __init__(self, *estimands: Estimand) -> None:
        object.__setattr__(self, "estimands", estimands)


class LivesConfig(StrictModel):
    """Improvement-banked round budget: banks a life each round that promotes an arm, loses one
    each round that does not. ``budget.rounds`` and spend stay the ceilings."""

    start: Annotated[int, Knob(Estimand.STOPPING, Estimand.SPEND)] = Field(
        2,
        ge=1,
        description="Lives a run starts with (a fully-stalling run does exactly this many rounds).",
    )
    cap: Annotated[int, Knob(Estimand.STOPPING, Estimand.SPEND)] = Field(
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


class OptimizerChoice(StrictModel):
    """What `optimizer_manifest.py::select_optimizer` reads: which optimizer, its overlay, and
    the gate its roster closes under. Bare, it is the manifest as declared."""

    optimizer: Annotated[str, Knob(Estimand.SEARCH)] = Field(
        "potter",
        min_length=1,
        description=(
            "The optimizer manifest this campaign runs — a directory under "
            "``promptpotter/assets/optimizers/``. Every entry point selects it here; nothing "
            "selects it install-wide."
        ),
    )
    nodes: Annotated[dict[str, ManifestNodeOverlay], Knob(Estimand.SEARCH)] = Field(
        default_factory=dict,
        description=(
            "This campaign's overlay on the selected manifest, keyed by node name — "
            "``{node: {config: {...}}}``, the shape a target pipeline's overlay takes. An "
            "optimizer's knobs, a paper's configuration and each llm node's ``config.model`` "
            "all ride here; each node validates its own knobs when the optimizer is selected."
        ),
    )
    arm_gate: Annotated[ArmGateMode, Knob(Estimand.GATE)] = Field(
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


class OptimizationConfig(OptimizerChoice):
    """The bench's own loop config, plus which optimizer runs and its overlay. An optimizer's knobs
    are its nodes' ``config`` in its manifest, never fields here."""

    @field_validator("budget", mode="before")
    @classmethod
    def _over_the_default(cls, given: object) -> Budget:
        return over_the_default(Budget.model_validate(given))

    budget: Annotated[Budget, Knob(Estimand.SPEND)] = Field(
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
    halt_at_accuracy: Annotated[float | None, Knob(Estimand.STOPPING)] = Field(
        None,
        ge=0.0,
        le=1.0,
        description=(
            "Stop ``target_hit`` once the declared pick's level over the origin, on the origin's "
            "panel, reaches this at its paired interval's lower bound. ``None`` → no such stop. Refused on an arm of a head-to-head, which stops only on its "
            "record's rule."
        ),
    )
    convergence_patience: Annotated[int | None, Knob(Estimand.STOPPING, Estimand.SPEND)] = Field(
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
    degradation_threshold: Annotated[float, Knob(Estimand.STOPPING)] = Field(...)
    degradation_fatal_fastpath: Annotated[bool, Knob(Estimand.STOPPING)] = Field(
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
        int, Knob(Estimand.STOPPING, Estimand.ABILITY, Estimand.DIFFICULTY)
    ] = Field(
        6,
        description="Minimum distinct samples before θ is read as ability: the δ ruler stays "
        "flat below it, and no eliminator cuts an arm (nor a selector crowns one) on fewer — "
        "the floor on n for a posterior to be meaningful.",
    )

    panel_gate: Annotated[PanelGateMode, Knob(Estimand.GATE)] = Field(
        "strict",
        description=(
            "Halt at the end of a round whose electable candidates carry HOLES — cells "
            "the loop attempted and got no measurement back from, or a spend or token "
            "ceiling left unbought — instead of electing a "
            "winner from a comparison whose arms ran different cell sets. ``strict`` "
            "(default) halts on any hole; ``off`` disarms, and a selector that can elect "
            "on a cut panel then does. The halt is resumable and the "
            "round is not persisted, so a ``start-run`` re-runs it once the bound that cut "
            "the cells has room: the cached "
            "candidates replay, the missing cells are re-measured, and the round decides "
            "on a complete panel. A PoBB elimination is NOT a hole (a rule stopped that "
            "arm on evidence) and neither is a classifier-deprecated sample."
        ),
    )

    lift_reference: Annotated[LiftReference, Knob(Estimand.GATE)] = Field(
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

    origin_gate: Annotated[OriginGateMode, Knob(Estimand.GATE)] = Field(
        "strict",
        description=(
            "Halt after round 0 when the origin's degradation verdict is non-healthy, "
            "instead of optimizing against a broken floor — the common failure while "
            "bringing up a new connector. ``strict`` (default) halts on ``critical`` or "
            "``degraded``; ``critical_only`` halts only on a structurally-broken origin; "
            "``off`` disarms the gate. Resuming re-measures the cells the origin lost and "
            "grades it again; an origin still unhealthy when resumed proceeds into the search."
        ),
    )

    enable_2pl_graduation: Annotated[
        bool,
        Knob(Estimand.DISCRIMINATION, Estimand.DIFFICULTY, Estimand.ABILITY),
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
    determinism: Annotated[DeterminismClamp | None, Knob(Estimand.SEARCH)] = Field(
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
    sp_budget_origin: Annotated[int, Knob(Estimand.SELECTION)] = Field(
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
    exclude_nodes: Annotated[list[str], Knob(Estimand.SEARCH)] = Field(default_factory=list)
    task_framing: Annotated[Literal["committed", "off"], Knob(Estimand.SEARCH)] = Field(
        "committed",
        description="Which task framing every target render splices in. `committed` "
        "(default) runs under the front matter of the dataset's `task.md`, which the first mint "
        "decomposes from its body when none is committed yet. `off` runs "
        "UNFRAMED on purpose — the ablation arm — even where framing exists, and the campaign "
        "manifest keeps the delta, so the result never reads as a framed one.",
    )
    pipeline_overlay: Annotated[dict[str, Any], Knob(Estimand.SEARCH)] = Field(default_factory=dict)
    optimizer_narrowing: Annotated[dict[str, NodeSearchNarrowing], Knob(Estimand.SEARCH)] = Field(
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
        "+ graded C (`steer_verdict`, asked when a fork's seed is parsed). Nothing declared for "
        "a node = nothing sanctioned there, the restrictive default.",
    )
    scoring: Annotated[str | dict[str, str] | None, Knob(Estimand.GATE)] = Field(None)
    judges: Annotated[dict[str, JudgeSpec], Knob(Estimand.GATE)] = Field(
        default_factory=dict,
        description="LLM-as-judge graders for datasets whose answer is free text, where no "
        "deterministic matcher can grade a cell. Each value names a registered judge "
        "(`promptpotter.judges`) and the models to run it on; its verdict is banked as a "
        "per-sample observation the `scoring` formula reads. "
        "**The KEY is the term that formula reads, not the judge's name** — so one cell can "
        "carry several graded terms, which is what a multi-step schema (retrieve -> ground -> "
        "answer) is, and two terms may run the same rubric on different models without one "
        "verdict landing on top of the other. Declaration order is the step order. "
        "Swapping a judge invalidates every verdict taken under the old one, so a resume "
        "must run divergence detection rather than carry them forward. "
        "**Their models are declared here and inherited from nowhere** — not from "
        "the permitted model set, not from the pipeline's node config, not from the optimizer's own "
        "LLMs. A judge is a ruler, and a ruler that moved with whatever the search was last "
        "steered to would not be one.",
    )
    display_metric: Annotated[DisplayMetric, Knob(Estimand.DISPLAY)] = Field(
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
    hard_sample_order: Annotated[HardSampleOrder, Knob(Estimand.DISPLAY)] = Field(
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
    accuracy_ceiling: Annotated[float | None, Knob(Estimand.DISPLAY)] = Field(
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
    dataset_split: Annotated[DatasetSplit | None, Knob(Estimand.SELECTION)] = Field(
        None,
        description="How run init partitions the bank into the search split every optimizer draw "
        "reads, the held-out bench set the headline is scored on, and a demo pool. `None` "
        "holds nothing out: the whole bank is the search split and there is no bench score.",
    )
    run_seed: Annotated[int, Knob(Estimand.SELECTION)] = Field(
        0,
        description="The run's one seed. It seeds which rows fall where and the order the search "
        "pool reads in: each row ranks by this and its content (`Sample.key`), never its slot, "
        "so a reordered bank holds out the same rows and reads them in the same order. An L4 "
        "seat seeds its determinism clamp and its solver node from it too.",
    )
    bench_trigger: Annotated[BenchTrigger, Knob(Estimand.SPEND)] = Field(
        "manual",
        description="When the line's bench passes are sent — each a cell per bench row, the "
        "ranked `dataset_split.bench` and every bench-only row the bank declares. `manual` "
        "(default): a launch sends none and sets nothing aside for one; the operator asks for "
        "the headline (CLI `bench`, command `grade-bench`), which grades the origin and the "
        "selection as they then stand. `at_end`: the origin's pass at launch, its price set "
        "aside under the ceiling, and the selection's once the run ends. `each_round`: that, "
        "and every round that selects a new individual is graded too. An arm of a head-to-head "
        "is minted `at_end` where this says `manual`: its comparison is the bench score.",
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


# `optimization.nodes` as a layer of `merge_config_layers`, which lays a node's config key by key.
NODE_OVERLAYS: TypeAdapter[dict[str, ManifestNodeOverlay]] = TypeAdapter(
    dict[str, ManifestNodeOverlay]
)


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
        opt_updates["nodes"] = NODE_OVERLAYS.validate_python(
            merge_config_layers(
                NODE_OVERLAYS.dump_python(config.optimization.nodes, exclude_unset=True),
                NODE_OVERLAYS.dump_python(overrides.nodes, exclude_unset=True),
            )
        )
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


def scoring_locked(config: CampaignConfig, lock: ScoringLockedRecord | None) -> CampaignConfig:
    if lock is None or lock.declared != config.scoring:
        return config
    return config.model_copy(update={"scoring": lock.locked})


def _narrowed(
    held: Mapping[str, NodeSearchNarrowing], later: Mapping[str, NodeSearchNarrowing]
) -> dict[str, NodeSearchNarrowing]:
    out = dict(held)
    for node, narrowing in later.items():
        out[node] = out[node].over(narrowing) if node in out else narrowing
    return out


class LaidSpan(FrozenModel):
    """One ledger of a cycle's chain: its own last seed, then the criterion its origin locked."""

    seed: CycleSeed | None
    scoring_lock: ScoringLockedRecord | None


class CycleConfig(FrozenModel):
    """What one cycle runs: its chain's spans laid in order over the declaration, each child's over
    its parent's, so no reader lays a part of one again. Built by ``laid`` alone."""

    config: CampaignConfig
    # One per ledger of the cycle's chain, its root's first and its own last.
    spans: tuple[LaidSpan, ...]
    seed_overlay: dict[str, dict[str, Any]]

    @classmethod
    def laid(cls, declared: CampaignConfig, spans: Sequence[LaidSpan]) -> CycleConfig:
        config = declared
        overlay: dict[str, dict[str, Any]] = {}
        for span in spans:
            if (seed := span.seed) is not None:
                config = apply_config_overrides(config, seed.config_overrides)
                narrowing = _narrowed(config.optimizer_narrowing, seed.optimizer_narrowing)
                config = config.model_copy(update={"optimizer_narrowing": narrowing})
                if seed.origin_params is not None:
                    overlay = apply_node_overlay(overlay, seed.origin_params, None)
                overlay = apply_node_overlay(overlay, seed.pipeline_overlay, None)
            config = scoring_locked(config, span.scoring_lock)
        return cls(config=config, spans=tuple(spans), seed_overlay=overlay)

    def steers_outside(self, permitted: Mapping[str, NodeSearchNarrowing]) -> bool:
        """The babysit act (decisions.md § 0005): a seed of the line steers outside what the nodes
        *permitted* before any seed. Per seed, so a later steer back inside leaves its parent's act standing."""
        return any(
            span.seed is not None and steers_disallowed_model(permitted, span.seed.pipeline_overlay)
            for span in self.spans
        )

    def knob_layers(self, recorded: KnobLayers) -> dict[str, dict[str, KnobLayer]]:
        """The layer that last set each ``optimization.nodes`` key ``config`` holds: a seed of the
        line over what its mint *recorded* (`PipelineRead.knob_layers`), any other the draft's edit."""
        seeded = {
            (node, key)
            for span in self.spans
            if span.seed is not None
            for node, overlay in span.seed.config_overrides.nodes.items()
            for key in overlay.config
        }
        return {
            node: {
                key: "seed"
                if (node, key) in seeded
                else recorded.get(node, {}).get(key, "campaign")
                for key in overlay.config
            }
            for node, overlay in self.config.optimization.nodes.items()
        }

    @property
    def own_seed(self) -> CycleSeed | None:
        """The seed the cycle's own ledger holds; ``None`` where it wrote none."""
        return self.spans[-1].seed if self.spans else None

    @property
    def origin(self) -> CycleSeed | None:
        """The last seed that stated C0's prompts: a child that states none runs its parent's."""
        return next(
            (
                span.seed
                for span in reversed(self.spans)
                if span.seed is not None and span.seed.origin_prompts
            ),
            None,
        )
