from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from promptpotter.application.bench.children import DROPPED_MANDATORY_PLACEHOLDER
from promptpotter.application.bench.node_context import (
    Bounds,
    Calls,
    Children,
    Frontier,
    Lineage,
    Progress,
    Replay,
    Ruler,
    Target,
)
from promptpotter.application.intelligence.adaptive_queue_mechanism import (
    covered_prefix,
    parent_grades,
    stratum_shares,
)
from promptpotter.application.intelligence.indexes.axis import NOISE_THRESHOLD
from promptpotter.application.intelligence.rasch import build_round_order
from promptpotter.application.optimizers import nodes
from promptpotter.application.optimizers.nodes import OptimizerPhase
from promptpotter.application.optimizers.potter.couplings import ADAPTIVE_QUEUE, POBB
from promptpotter.application.optimizers.potter.dispatch.bundle import injection_registry
from promptpotter.application.optimizers.potter.dispatch.facade import (
    BUNDLE_DRAWS,
    BUNDLE_READS,
    BUNDLE_SEES,
    fingerprinted_modules,
)
from promptpotter.application.optimizers.potter.dispatch.injections.registry import injection_table
from promptpotter.application.optimizers.potter.dispatch.layout import (
    NODE_LAYOUTS,
    LayoutNode,
    coerce_l1_layout,
    unplaceable_edit,
    validate_l1_layout,
)
from promptpotter.application.optimizers.potter.dispatch.prompts import node_layout
from promptpotter.application.optimizers.potter.dispatch.schemas import (
    L1CritiqueOutput,
    L1GenerateOutput,
    L2ContextOutput,
    L2RecombiningOutput,
    L3PlanOutput,
    build_l2_response_model,
)
from promptpotter.application.optimizers.potter.election import elect_on_theta
from promptpotter.application.optimizers.potter.escalation.firing import (
    FORCED_BY_DIAG,
    Fire,
    LayerCall,
    TransitionResult,
    escalate_l2,
)
from promptpotter.application.optimizers.potter.escalation.rules import (
    CLIMB_RULES,
    DEFAULT_ESCALATION_RULES,
    HEAL_RULES,
    NextAction,
)
from promptpotter.application.optimizers.potter.escalation.state import (
    PotterPhase,
    l2_fired,
    l3_fired,
    observe,
)
from promptpotter.application.optimizers.potter.generation_only import run_generation_only_round
from promptpotter.application.optimizers.potter.knobs import (
    AdaptiveQueueKnobs,
    CellStrata,
    EscalationKnobs,
    L1GenerateKnobs,
    PoBBKnobs,
)
from promptpotter.application.optimizers.potter.l1.candidate_source import (
    propose_l1_population,
    variants_this_round,
)
from promptpotter.application.optimizers.potter.l1.critique import distil_critique
from promptpotter.application.optimizers.potter.l1.stats import (
    critique_facts,
    review_reading,
    yield_facts,
)
from promptpotter.application.optimizers.potter.pobb.checks import ABORT_LENS_SUPPRESS
from promptpotter.application.optimizers.potter.race import PoBBRace
from promptpotter.application.optimizers.potter.records import (
    L1Layout,
    L1Readout,
    L2Memory,
    L3Memory,
    Ladder,
    Recombination,
)
from promptpotter.application.optimizers.potter.resume import (
    ELECTION_REPLAYERS,
    RACE_REPLAYERS,
    rederive_critiques,
    round_packages,
)
from promptpotter.application.optimizers.potter.validators.l3_output import l3_plan_breaches
from promptpotter.application.scoring.selection import crowning_cells
from promptpotter.application.views.render.primitives import fmt_fitness, fmt_pct
from promptpotter.domain.dashboard_rows import OptimizerLimit
from promptpotter.domain.optimizer_state import Critique
from promptpotter.domain.phase_views import OptimizerStepEnterView, OptimizerStepExitView
from promptpotter.domain.phases import StopLoop
from promptpotter.domain.pipeline_schema import SCHEMA_RENAME_PARAM, NodeKind
from promptpotter.domain.results_health import evidence_starved_node
from promptpotter.domain.run_standing import rounds_without_advance
from promptpotter.domain.strict_model import StrictModel
from promptpotter.domain.validators import ValidatorOutcome
from promptpotter.shared.answer_text import truncate

if TYPE_CHECKING:
    from types import ModuleType

    from pydantic import BaseModel

    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.application.bench.node_context import NodeContext
    from promptpotter.application.bench.resume_and_fork.replayers import Replayer
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.application.optimizers.nodes import (
        CatchUpFn,
        CycleRead,
        Draw,
        MeasuredRound,
        Panel,
        PromptArity,
        Proposals,
        ReviewReading,
        RoundContext,
        Selection,
    )
    from promptpotter.application.optimizers.potter.dispatch.bundle import InjectionBundle
    from promptpotter.domain.arm_reading import DisplayMetric
    from promptpotter.domain.cycle_paths import CycleHop
    from promptpotter.domain.opt_search_point import VariationMode
    from promptpotter.domain.optimizer_state import StatePart
    from promptpotter.domain.paired_reading import ArmKey
    from promptpotter.domain.pool import Pool
    from promptpotter.domain.results import OptimizerFact, RoundResult
    from promptpotter.domain.round_audit import RoundAudit
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import CellSheet
    from promptpotter.infrastructure.store.campaign_store.store import CampaignStore


logger = logging.getLogger(__name__)

# What every layout node's prompt is said through; importing it fills the registry `injections` counts.
_HUB = fingerprinted_modules()


def _strata(pool: list[Sample], by: CellStrata) -> list[list[Sample]]:
    """In first-seen order; `none` is the pool whole."""
    if by == "none":
        return [pool]
    out: dict[str, list[Sample]] = {}
    for s in pool:
        out.setdefault(s.measured_dataset or "", []).append(s)
    return list(out.values())


class AdaptiveQueue(nodes.Sampler):
    """ONE order every arm walks: shared prefixes keep paired stats comparable."""

    name: ClassVar[str] = "adaptive_queue"
    kind: ClassVar[NodeKind] = NodeKind.SAMPLER
    knobs: ClassVar[type[StrictModel]] = AdaptiveQueueKnobs
    sees: ClassVar[frozenset[type[CycleRead]]] = frozenset(
        {Lineage, Frontier, Ruler, Bounds, Replay}
    )
    size_knob: ClassVar[str | None] = "sp_budget_round"
    couplings: ClassVar[tuple[nodes.MemberCoupling, ...]] = ADAPTIVE_QUEUE

    def round_cells(self, knobs: AdaptiveQueueKnobs, _search: int) -> int:
        # Unclamped: a budget above the search split is the finding preflight reports.
        return knobs.sp_budget_round

    def round_cells_ceiling(self, selected: SelectedOptimizer, search: int) -> int:
        # The draw is unclamped where the budget exceeds the search split; a round's is not.
        budget = selected.knobs_of(self.name, AdaptiveQueueKnobs).sp_budget_round
        return (selected.arms_per_round + 1) * min(budget, search)

    def draw(self, ctx: NodeContext, pool: list[Sample]) -> Panel:
        view = ctx.read(Ruler).difficulty
        knobs = ctx.knobs(AdaptiveQueueKnobs)
        lineage = ctx.read(Lineage)
        # A race read on cells that crowned its parent re-reads the parent's luck as its ability.
        crowned = crowning_cells(lineage.rounds, lineage.parent.id)
        pool = [s for s in pool if s.key not in crowned]
        n_min = ctx.read(Bounds).elimination_n_min
        # No outcome and no ability choose a cell: the election reads these rows as the pool's.
        # A cold ruler keeps the campaign-start prefix, whose shared cells warm it fastest.
        ruler = view.ruler if knobs.per_round_resubset else None
        rng = ctx.read(Replay).rng()

        def pick(stratum: list[Sample], budget: int) -> list[Sample]:
            if ruler is None:
                return covered_prefix(stratum, budget)
            shuffled = rng.sample(stratum, len(stratum))
            # A warm θ reads ruler cells alone: `elimination_n_min` of them lead, as blindly drawn.
            lead = [s for s in shuffled if s.key in ruler.delta][:n_min]
            led = {s.key for s in lead}
            return covered_prefix([*lead, *(s for s in shuffled if s.key not in led)], budget)

        strata = _strata(pool, knobs.strata)
        shares = stratum_shares([len(s) for s in strata], knobs.sp_budget_round)
        cells = [cell for s, n in zip(strata, shares, strict=True) if n for cell in pick(s, n)]
        order = build_round_order(
            parent_grades(ctx.read(Frontier).parent_own_rows),
            view.ruler,
            {int(s.id): s.key for s in cells},
            n_min=n_min,
        )
        by_id = {int(s.id): s for s in cells}
        # Every cell closes a block: PoBB decides after each one.
        return nodes.Panel(cells=cells, order=[by_id[sid] for sid in order], block_size=1)


class L1Generate(LayoutNode, nodes.Proposer):
    name: ClassVar[str] = "l1_generate"
    prompt_arity: ClassVar[PromptArity] = "each"
    proposes: ClassVar[nodes.Proposing] = "opens"
    variation_mode: ClassVar[VariationMode] = "llm"
    knobs: ClassVar[type[StrictModel]] = L1GenerateKnobs
    reads: ClassVar[frozenset[type[StatePart]]] = BUNDLE_READS
    writes: ClassVar[frozenset[type[StatePart]]] = frozenset({L1Readout})
    sees: ClassVar[frozenset[type[CycleRead]]] = BUNDLE_SEES | {Calls, Children, Replay}
    draws: ClassVar[Mapping[type[Pool], Draw]] = BUNDLE_DRAWS
    response_model: ClassVar[type[BaseModel] | None] = L1GenerateOutput
    # L2 moves this node's panels, so the outer's only lever here is its output schema's names.
    outer_levers: ClassVar[Mapping[str, str]] = {SCHEMA_RENAME_PARAM: "object"}
    prompt_sources: ClassVar[tuple[ModuleType, ...]] = _HUB
    priced_surface: ClassVar[Mapping[str, int]] = {"injections": len(injection_registry())}

    @property
    def consults(self) -> frozenset[type]:
        return frozenset({AdaptiveQueue, Escalation})

    def arms(self, selected: SelectedOptimizer) -> int:
        knobs = selected.knobs_of(self.name, L1GenerateKnobs)
        return knobs.n_variants + knobs.recombine

    def opening(self, ctx: RoundContext, _walk: nodes.RoundWalk) -> nodes.RoundOpening:
        cycle = ctx.cycle
        selected = cycle.optimizer
        knobs = selected.knobs_of(self.name, L1GenerateKnobs)
        replayed = cycle.banked_proposals(ctx.round_num) is not None
        # At patience 0 L2 fires every round, which "stall 1/0" would state as a riddle.
        patience = selected.reached_knobs(self, EscalationKnobs).l1_patience
        stall = cycle.slices.get(Ladder).l1_stall_count
        standing = "L2 every round" if patience == 0 else f"stall {stall}/{patience} → L2"
        if ctx.round_num and cycle.output(Critique, ctx.round_num - 1).readout:
            critique = f"from R{ctx.round_num - 1}"
        elif ctx.round_num <= 1:
            critique = "none yet (first round)"
        else:
            critique = f"none (R{ctx.round_num - 1} produced none)"
        return nodes.RoundOpening(
            standing=standing,
            note=f"Prior critique: {critique}",
            arms=variants_this_round(knobs.n_variants, cycle.slices.get(L2Memory))
            + knobs.recombine,
            proposer="L1",
            replayed=replayed,
        )

    def round_facts(
        self, _selected: SelectedOptimizer, round_result: RoundResult
    ) -> list[OptimizerFact]:
        return yield_facts(round_result)

    def review(
        self,
        rounds: list[RoundResult],
        audits: list[RoundAudit | None],
        *,
        context_object: list[str],
    ) -> ReviewReading:
        return review_reading(rounds, audits, context_object=context_object)

    def complete(self) -> None:
        injection_table()

    async def propose(self, ctx: NodeContext, _panel: Panel, _proposals: Proposals) -> Proposals:
        return await propose_l1_population(ctx)


class PoBB(nodes.Eliminator):
    name: ClassVar[str] = "pobb"
    kind: ClassVar[NodeKind] = NodeKind.ELIMINATOR
    knobs: ClassVar[type[StrictModel]] = PoBBKnobs
    sees: ClassVar[frozenset[type[CycleRead]]] = frozenset(
        {Lineage, Frontier, Ruler, Bounds, Target, Progress, Replay}
    )
    abort_lenses: ClassVar[Mapping[str, frozenset[str]]] = ABORT_LENS_SUPPRESS
    # A PoBB stop ends the buying of an arm; θ still reads what it measured.
    stop_disqualifies: ClassVar[bool] = False
    replayers: ClassVar[Mapping[str, Replayer]] = RACE_REPLAYERS
    couplings: ClassVar[tuple[nodes.MemberCoupling, ...]] = POBB

    def limits(self, selected: SelectedOptimizer) -> tuple[OptimizerLimit, ...]:
        epsilon = selected.knobs_of(self.name, PoBBKnobs).epsilon
        return (
            OptimizerLimit(
                node=self.name, knob="epsilon", label="PoBB ε", value=epsilon, integer=False
            ),
        )

    def race(self, ctx: NodeContext, panel: Panel, catch_up: CatchUpFn) -> PoBBRace:
        return PoBBRace(ctx, panel, catch_up, measured_unit=ctx.read(Target).measured_unit)


class ThetaElection(nodes.Selector):
    name: ClassVar[str] = "theta_election"
    kind: ClassVar[NodeKind] = NodeKind.SELECTOR
    sees: ClassVar[frozenset[type[CycleRead]]] = frozenset({Lineage, Ruler, Replay})
    # Admission mirrors the stopping rule: one ε says when the parent is futile against an arm.
    consults: ClassVar[frozenset[type]] = frozenset({PoBB})
    elects_on: ClassVar[DisplayMetric] = "ability"
    elects_partial: ClassVar[bool] = True
    replayers: ClassVar[Mapping[str, Replayer]] = ELECTION_REPLAYERS

    def parent_cells(
        self,
        _ctx: NodeContext,
        panel: Panel,
        _rows: Mapping[ArmKey, CellSheet],
    ) -> list[Sample]:
        return panel.cells

    def select(self, ctx: NodeContext, measured: MeasuredRound) -> Selection:
        return elect_on_theta(ctx, measured)


class L1Critique(LayoutNode, nodes.Adapter):
    """A malformed response is survived: the next walk re-runs it (`round.py::settle_owed_outputs`)."""

    name: ClassVar[str] = "l1_critique"
    reads: ClassVar[frozenset[type[StatePart]]] = BUNDLE_READS
    writes: ClassVar[frozenset[type[StatePart]]] = frozenset({Critique})
    sees: ClassVar[frozenset[type[CycleRead]]] = BUNDLE_SEES | {Calls}
    draws: ClassVar[Mapping[type[Pool], Draw]] = BUNDLE_DRAWS
    response_model: ClassVar[type[BaseModel] | None] = L1CritiqueOutput
    prompt_sources: ClassVar[tuple[ModuleType, ...]] = _HUB

    @property
    def consults(self) -> frozenset[type]:
        return frozenset({AdaptiveQueue, Escalation, L1Generate})

    def round_facts(
        self, _selected: SelectedOptimizer, round_result: RoundResult
    ) -> list[OptimizerFact]:
        return critique_facts(round_result)

    def round_packages(self, cycle: Cycle, rounds: list[RoundResult]) -> dict[int, dict[str, str]]:
        """Every layout node's: one bundle feeds them all."""
        return round_packages(cycle, rounds)

    async def rederive(
        self,
        campaign_store: CampaignStore,
        hop: CycleHop,
        cycle: Cycle,
        rounds: list[RoundResult],
        drifted: list[int],
    ) -> list[RoundResult]:
        return await rederive_critiques(campaign_store, hop, cycle, rounds, drifted)

    async def adapt(self, ctx: NodeContext) -> None:
        ctx.emit(await distil_critique(ctx))


class L2Context(LayoutNode):
    name: ClassVar[str] = "l2_context"
    layer_id: ClassVar[Literal["L2", "L3"]] = "L2"
    reads: ClassVar[frozenset[type[StatePart]]] = BUNDLE_READS
    writes: ClassVar[frozenset[type[StatePart]]] = frozenset({L2Memory})
    sees: ClassVar[frozenset[type[CycleRead]]] = BUNDLE_SEES | {Calls, Progress}
    draws: ClassVar[Mapping[type[Pool], Draw]] = BUNDLE_DRAWS
    response_model: ClassVar[type[BaseModel] | None] = L2ContextOutput
    prompt_sources: ClassVar[tuple[ModuleType, ...]] = _HUB
    phase: ClassVar[OptimizerPhase] = OptimizerPhase(
        phase=PotterPhase.REFINE_STRATEGY, node="l2_context", activity="refining strategy"
    )
    # `l1_generate`'s empty manifest `param_keys` say L1 does not search its own config; L2 does.
    steers: ClassVar[Mapping[str, frozenset[str]]] = {
        L1Generate.name: frozenset({"temperature", "n_variants"})
    }

    @property
    def consults(self) -> frozenset[type]:
        return frozenset({AdaptiveQueue, Escalation, L1Generate})

    def reply_model(self, bundle: InjectionBundle) -> type[BaseModel]:
        return build_l2_response_model(
            bundle.silent_l1_panels, bundle.recombination_offer(self.name)
        )

    def parse(self, ctx: NodeContext, raw: L2ContextOutput) -> TransitionResult:
        # An absent reason is REPORTED, never replaced by a sentence that reads as L2's own.
        rationale = truncate(raw.rationale, 80) if raw.rationale else "(no rationale given)"
        # Filtered here: a landed steer is carried forward on every later fire and never pruned.
        steer: dict[str, Any] = {
            key: value
            for key, value in raw.l1_overrides.items()
            if key in self.steers[L1Generate.name]
            and isinstance(value, int | float)
            and not isinstance(value, bool)
        }

        prior = node_layout("l1_generate", ctx.slice(L2Memory))
        layout_outcomes: list[ValidatorOutcome] = []
        accepted_layout: L1Layout | None = None
        layout_refused = False
        if breach := unplaceable_edit(raw.l1_layout):
            layout_outcomes = [breach]
            layout_refused = True
        elif proposed_layout := coerce_l1_layout(raw.l1_layout, base=prior):
            layout_result = validate_l1_layout(
                proposed_layout, spec=NODE_LAYOUTS["l1_generate"], prior_layout=prior
            )
            layout_outcomes = list(layout_result.outcomes)
            if layout_result.is_valid:
                accepted_layout = proposed_layout
                # Whole, never the edit: a move lands relative to where each panel then sat.
                steer["layout"] = proposed_layout.model_dump(mode="json")
            else:
                layout_refused = True

        if layout_outcomes:
            logger.warning(
                "L2 layout %s — %d outcome(s): %s",
                "REFUSED" if layout_refused else "accepted with reports",
                len(layout_outcomes),
                ", ".join(o.validator_id for o in layout_outcomes),
            )

        named = raw.recombinations if isinstance(raw, L2RecombiningOutput) else []
        return TransitionResult(
            described=f"L2: {rationale}",
            steer=steer,
            axis_targeted=raw.axis_targeted,
            l1_layout=accepted_layout,
            l1_layout_refused=layout_refused,
            l2_guard_breaches=layout_outcomes,
            fork_proposal=raw.fork_proposal,
            terminate_proposal=raw.terminate_proposal,
            recombinations=[
                Recombination.model_validate(spec.model_dump())
                for spec in named[: ctx.knobs(L1GenerateKnobs).recombine]
            ],
            # The round the bundle renders for, as the next round's L1 reads it.
            for_round=ctx.read(Lineage).rounds[-1].round + 1,
        )

    def land(self, ctx: NodeContext, result: TransitionResult) -> None:
        memory = ctx.slice(L2Memory)
        steered = memory.steered_onto("l1_generate", result.steer) if result.steer else memory
        ctx.put(
            steered.model_copy(
                update={
                    "guard_breaches": list(result.l2_guard_breaches),
                    "recombinations": result.recombinations,
                    "for_round": result.for_round if result.recombinations else None,
                }
            )
        )
        ctx.restate(L2Memory)

    def record(self, ladder: Ladder, fire: Fire) -> Ladder:
        assert fire.ask is not None, "only L3 heals"
        return l2_fired(ladder, fire.ask.l2)

    def enter_view(self, ctx: NodeContext, fire: Fire) -> OptimizerStepEnterView:
        items = [(k, str(v)) for k, v in ctx.slice(L2Memory).steered("l1_generate").items()]
        if items:
            shown = [f"{k}={s if len(s) <= 30 else s[:27] + '...'}" for k, s in items[:5]]
            more = f", +{len(items) - 5} more" if len(items) > 5 else ""
            overrides = f"l1_overrides: {', '.join(shown)}{more}"
        else:
            overrides = "l1_overrides: (none)"
        return OptimizerStepEnterView(
            node=self.name,
            activity=self.phase.activity,
            title="L2 REFINE CONTEXT",
            tag=f"L2 fire {ctx.slice(Ladder).l2.fires + 1}",
            lines=(
                f"rule={fire.cause}  |  "
                f"L1 stalled {rounds_without_advance(ctx.read(Lineage).rounds)} rounds  |  "
                f"acc={fmt_pct(ctx.read(Frontier).parent_accuracy)}  "
                f"peak fitness={fmt_fitness(fire.best.composite_fitness)}",
                overrides,
                "LLM analyzing failure patterns...",
            ),
        )

    def exit_view(self, ctx: NodeContext, result: TransitionResult) -> OptimizerStepExitView:
        layout = ", l1_layout edited" if result.l1_layout is not None else ""
        axis = f", axis={result.axis_targeted}" if result.axis_targeted else ""
        changed = len(ctx.slice(L2Memory).steered("l1_generate"))
        return OptimizerStepExitView(
            headline=f"L2 decision: {changed} param changes{layout}{axis}",
            details=(result.described,),
            audit=("L2 call", self.name),
        )


class L3Plan(LayoutNode):
    name: ClassVar[str] = "l3_plan"
    layer_id: ClassVar[Literal["L2", "L3"]] = "L3"
    reads: ClassVar[frozenset[type[StatePart]]] = BUNDLE_READS
    writes: ClassVar[frozenset[type[StatePart]]] = frozenset({L3Memory})
    sees: ClassVar[frozenset[type[CycleRead]]] = BUNDLE_SEES | {Calls, Progress}
    draws: ClassVar[Mapping[type[Pool], Draw]] = BUNDLE_DRAWS
    response_model: ClassVar[type[BaseModel] | None] = L3PlanOutput
    prompt_sources: ClassVar[tuple[ModuleType, ...]] = _HUB
    phase: ClassVar[OptimizerPhase] = OptimizerPhase(
        phase=PotterPhase.MODIFY_PLAN, node="l3_plan", activity="replanning"
    )

    @property
    def consults(self) -> frozenset[type]:
        return frozenset({AdaptiveQueue, Escalation, L1Generate})

    def reply_model(self, _bundle: InjectionBundle) -> type[BaseModel]:
        return L3PlanOutput

    def parse(self, ctx: NodeContext, raw: L3PlanOutput) -> TransitionResult:
        rationale = truncate(raw.rationale, 80) if raw.rationale else "(no rationale given)"
        failures = l3_plan_breaches(raw.plan, prior_plan=ctx.slice(L3Memory).plan)
        if failures:
            logger.warning(
                "L3 output failed %d validator(s): %s",
                len(failures),
                ", ".join(o.validator_id for o in failures),
            )
        return TransitionResult(
            described=f"L3: {rationale}",
            plan=raw.plan,
            l3_note=raw.note,
            l3_guard_breaches=failures,
            fork_proposal=raw.fork_proposal,
            terminate_proposal=raw.terminate_proposal,
        )

    def land(self, ctx: NodeContext, result: TransitionResult) -> None:
        ctx.put(
            L3Memory(
                plan=result.plan,
                note=result.l3_note,
                guard_breaches=list(result.l3_guard_breaches),
            )
        )
        ctx.restate(L3Memory)

    def record(self, ladder: Ladder, fire: Fire) -> Ladder:
        return l3_fired(ladder, None if fire.ask is None else fire.ask.l3)

    def enter_view(self, ctx: NodeContext, fire: Fire) -> OptimizerStepEnterView:
        return OptimizerStepEnterView(
            node=self.name,
            activity=self.phase.activity,
            title="L3 MODIFY PLAN",
            tag=f"L3 fire {ctx.slice(Ladder).l3.fires + 1}",
            lines=(
                "Healing L2's refused l1_layout edit"
                if fire.ask is None
                else f"L2 stalled {fire.ask.l2.stall_count} rounds",
                f"Current plan: {truncate(ctx.slice(L3Memory).plan[:120], 55, '...')}",
                "LLM designing new strategy...",
            ),
        )

    def exit_view(self, _ctx: NodeContext, result: TransitionResult) -> OptimizerStepExitView:
        return OptimizerStepExitView(
            headline=f"New plan: {truncate(result.plan[:120], 55, '...')}",
            details=(result.described,),
            audit=None,
        )


class Escalation(nodes.Controller):
    name: ClassVar[str] = "escalation"
    kind: ClassVar[NodeKind] = NodeKind.CONTROLLER
    knobs: ClassVar[type[StrictModel]] = EscalationKnobs
    reads: ClassVar[frozenset[type[StatePart]]] = frozenset({L1Readout, Ladder})
    writes: ClassVar[frozenset[type[StatePart]]] = frozenset({Ladder})
    sees: ClassVar[frozenset[type[CycleRead]]] = frozenset({Lineage, Ruler, Replay})
    fires: ClassVar[frozenset[type]] = frozenset({L2Context, L3Plan})
    priced_surface: ClassVar[Mapping[str, int]] = {
        "escalation_rules": len(DEFAULT_ESCALATION_RULES) + len(CLIMB_RULES) + len(HEAL_RULES)
    }

    def limits(self, selected: SelectedOptimizer) -> tuple[OptimizerLimit, ...]:
        esc = selected.knobs_of(self.name, EscalationKnobs)
        patiences = [
            ("l1_patience", "L1 patience", esc.l1_patience),
            ("l2_patience", "L2 patience", esc.l2_patience),
            ("l3_patience", "L3 patience", esc.l3_patience),
        ]
        return tuple(
            OptimizerLimit(node=self.name, knob=knob, label=label, value=value, integer=True)
            for knob, label, value in patiences
        )

    def observe(self, ctx: NodeContext, round_result: RoundResult) -> str | None:
        knobs = ctx.knobs(EscalationKnobs)
        axes = ctx.read(Ruler).axes
        axes_with_positive_yield = (
            None
            if axes is None
            else sum(1 for r in axes.axis_rankings() if r.effect_size > NOISE_THRESHOLD)
        )
        # Structural, not a stall: heals L2 now instead of burning l1_patience rounds.
        l1_mandatory_breach = any(
            vf.reason == DROPPED_MANDATORY_PLACEHOLDER
            for arm in round_result.panel.values()
            for vf in arm.validation_failures
        )
        # The same structural fault with an empty panel, so it rides `l1_parse_failure`.
        l1_zero_candidates = ctx.recall(L1Readout, round_result.round).l1_parse_failure is not None
        # The stamped grade's own rates, so routing and verdict cannot diverge.
        evidence_starved = evidence_starved_node(round_result.health.node_failure_rates) is not None
        ladder, event = observe(
            ctx.slice(Ladder),
            advance=round_result.overlap.advance,
            l1_patience=knobs.l1_patience,
            escalation_ladder=knobs.escalation_ladder,
            axes_with_positive_yield=axes_with_positive_yield,
            l1_mandatory_breach=l1_mandatory_breach,
            l1_zero_candidates=l1_zero_candidates,
            evidence_starved=evidence_starved,
        )
        ctx.put(ladder)
        return event.rule if event.next_action == NextAction.FIRE_L2 else None

    async def act(self, ctx: NodeContext, cause: str) -> None:
        if stop := await escalate_l2(
            ctx,
            cause=cause,
            l2=LayerCall(L2, ctx.fire(L2Context)),
            l3=LayerCall(L3, ctx.fire(L3Plan)),
        ):
            raise StopLoop(stop)

    async def diagnose(self, ctx: NodeContext) -> None:
        await self.act(ctx, FORCED_BY_DIAG)

    async def show_round(self, ctx: RoundContext) -> None:
        await run_generation_only_round(ctx)


L2 = L2Context()
L3 = L3Plan()

MEMBERS = (
    AdaptiveQueue(),
    L1Generate(),
    PoBB(),
    ThetaElection(),
    L1Critique(),
    Escalation(),
    L2,
    L3,
)
