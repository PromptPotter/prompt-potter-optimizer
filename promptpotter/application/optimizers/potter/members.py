from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, ClassVar, Literal

from pydantic import BaseModel

from promptpotter.application.bench.node_context import (
    Bounds,
    Calls,
    Children,
    Lineage,
    NodeContext,
    Parent,
    Progress,
    Replay,
    Ruler,
)
from promptpotter.application.intelligence.adaptive_queue_mechanism import (
    covered_prefix,
    parent_grades,
    stratum_shares,
)
from promptpotter.application.intelligence.rasch import build_round_order
from promptpotter.application.optimizer_manifest import SelectedOptimizer
from promptpotter.application.optimizers import nodes
from promptpotter.application.optimizers.nodes import (
    CycleRead,
    Draw,
    MeasuredRound,
    OptimizerPhase,
    PromptArity,
    Proposals,
    ReviewReading,
    RoundSamples,
    Selection,
)
from promptpotter.application.optimizers.potter.couplings import ADAPTIVE_QUEUE, LADDER, POBB
from promptpotter.application.optimizers.potter.dispatch.bundle import injection_registry
from promptpotter.application.optimizers.potter.dispatch.facade import (
    VIEW_READS,
    VIEW_SEES,
    PotterView,
)
from promptpotter.application.optimizers.potter.dispatch.injections.registry import (
    LayoutNode,
    injection_table,
)
from promptpotter.application.optimizers.potter.dispatch.layout import (
    NODE_LAYOUTS,
    coerce_l1_layout,
    unplaceable_edit,
    validate_l1_layout,
)
from promptpotter.application.optimizers.potter.dispatch.prompts import node_layout
from promptpotter.application.optimizers.potter.dispatch.schemas import (
    L1CritiqueOutput,
    L1GenerateOutput,
    L1Variant,
    L2ContextOutput,
    L2RecombiningOutput,
    L2TerminatingOutput,
    L3PlanOutput,
    L3TerminatingOutput,
    build_l2_response_model,
    build_l3_response_model,
)
from promptpotter.application.optimizers.potter.election import elect_on_theta
from promptpotter.application.optimizers.potter.escalation import (
    ESCALATION_RULES,
    Fire,
    LayerCall,
    NextAction,
    PotterPhase,
    TransitionResult,
    bank_and_route,
    escalate_l2,
    l2_fired,
    l3_fired,
)
from promptpotter.application.optimizers.potter.knobs import (
    AdaptiveQueueKnobs,
    CellStrata,
    ElectionKnobs,
    EscalationKnobs,
    L1GenerateKnobs,
    PoBBKnobs,
    TranscriptKnobs,
)
from promptpotter.application.optimizers.potter.l1 import (
    critique_facts,
    distil_critique,
    propose_l1_population,
    review_reading,
    variants_this_round,
    yield_facts,
)
from promptpotter.application.optimizers.potter.pobb import ABORT_LENS_SUPPRESS
from promptpotter.application.optimizers.potter.race import PoBBRace
from promptpotter.application.optimizers.potter.records import (
    L1Layout,
    L1Readout,
    L2Memory,
    L3Memory,
    Ladder,
    Recombination,
)
from promptpotter.application.optimizers.potter.validators import l3_plan_breaches
from promptpotter.application.scoring.selection import crowning_cells
from promptpotter.domain.arm_reading import DisplayMetric
from promptpotter.domain.crowning_bar import Vote
from promptpotter.domain.dashboard_rows import OptimizerLimit
from promptpotter.domain.opt_search_point import VariationMode
from promptpotter.domain.optimizer_state import Critique, StatePart
from promptpotter.domain.phase_acts import (
    CountFact,
    FitnessFact,
    SettingsFact,
    StepClosed,
    StepOpened,
    TextFact,
)
from promptpotter.domain.pipeline_schema import SCHEMA_RENAME_PARAM, NodeKind
from promptpotter.domain.pool import BlockPool, DemoPool, Pool
from promptpotter.domain.records.run_records import StopLoop
from promptpotter.domain.results import OptimizerFact, RoundResult
from promptpotter.domain.results_health import evidence_starved_node
from promptpotter.domain.round_audit import RoundAudit
from promptpotter.domain.run_standing import rounds_without_advance
from promptpotter.domain.sample import Sample
from promptpotter.domain.strict_model import StrictModel
from promptpotter.domain.validators import ValidatorOutcome
from promptpotter.domain.wounds import ValidationReason


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
    sees: ClassVar[frozenset[type[CycleRead]]] = frozenset({Lineage, Parent, Ruler, Bounds, Replay})
    size_knob: ClassVar[str | None] = "sp_budget_round"
    couplings: ClassVar[tuple[nodes.MemberCoupling, ...]] = ADAPTIVE_QUEUE

    def drawn(self, knobs: AdaptiveQueueKnobs, _search: int, _pipeline: str) -> nodes.DrawSize:
        # Unclamped: a budget above the search split is the finding preflight reports.
        return nodes.DrawSize(cells=knobs.sp_budget_round, block_size=1)

    def draw(self, ctx: NodeContext, pool: list[Sample]) -> RoundSamples:
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
        by_id = {int(s.id): s for s in cells}
        order = build_round_order(
            parent_grades(ctx.read(Parent).own_rows), view.ruler, by_id, n_min=n_min
        )
        # Every cell closes a block: PoBB decides after each one.
        return nodes.RoundSamples(
            cells=cells,
            order=[by_id[sid] for sid in order],
            block_size=1,
            pool=[s for s in pool if int(s.id) not in by_id],
        )


class L1Generate(LayoutNode, nodes.Proposer):
    name: ClassVar[str] = "l1_generate"
    prompt_arity: ClassVar[PromptArity] = "each"
    proposes: ClassVar[nodes.Proposing] = "opens"
    variation_mode: ClassVar[VariationMode] = "llm"
    knobs: ClassVar[type[StrictModel]] = L1GenerateKnobs
    reads: ClassVar[frozenset[type[StatePart]]] = VIEW_READS
    writes: ClassVar[frozenset[type[StatePart]]] = frozenset({L1Readout})
    sees: ClassVar[frozenset[type[CycleRead]]] = VIEW_SEES | {Calls, Children}
    # Every view member reads the pools through this draw (`NodeContext.drawn`), so L2 lays out
    # what L1 is shown.
    draws: ClassVar[Mapping[type[Pool], Draw]] = MappingProxyType(
        {
            BlockPool: nodes.AccessKnob(L1GenerateKnobs, "block_access"),
            DemoPool: nodes.CountKnob(L1GenerateKnobs, "k_max"),
        }
    )
    response_model: ClassVar[type[BaseModel] | None] = L1GenerateOutput
    # L2 moves this node's panels, so the outer's only lever here is its output schema's names.
    outer_levers: ClassVar[Mapping[str, str]] = {SCHEMA_RENAME_PARAM: "object"}
    priced_surface: ClassVar[Mapping[str, int]] = {"injections": len(injection_registry())}

    @property
    def consults(self) -> frozenset[type]:
        return frozenset({AdaptiveQueue, Escalation})

    def resolved_levers(self, declared: Mapping[str, Any]) -> dict[str, Any]:
        """The rename with each wire name stripped, here alone; an empty one renames nothing."""
        rename = declared.get(SCHEMA_RENAME_PARAM)
        if not isinstance(rename, Mapping):
            return {} if rename is None else {SCHEMA_RENAME_PARAM: rename}
        stripped = {f: w.strip() if isinstance(w, str) else w for f, w in rename.items()}
        return {SCHEMA_RENAME_PARAM: stripped} if stripped else {}

    def lever_refusals(self, declared: Mapping[str, Any]) -> list[str]:
        """A rename the emission and the parse twin cannot both apply, which would fail every parse."""
        rename = self.resolved_levers(declared).get(SCHEMA_RENAME_PARAM)
        if rename is None:
            return []
        if not isinstance(rename, Mapping):
            return [f"{SCHEMA_RENAME_PARAM} is no field-to-name map"]
        fields = set(L1Variant.model_fields)
        taken = [*rename.values(), *(fields - set(rename))]
        refused: list[str] = []
        for field, wire in rename.items():
            if field not in fields:
                refused.append(f"{SCHEMA_RENAME_PARAM} renames {field!r}, no L1 variant field")
            elif not isinstance(wire, str) or not wire.isidentifier() or wire == field:
                refused.append(
                    f"{SCHEMA_RENAME_PARAM} renames {field!r} to {wire!r}, no new identifier"
                )
            elif taken.count(wire) > 1:
                refused.append(f"{SCHEMA_RENAME_PARAM} names {wire!r} for two fields")
        return refused

    def arms(self, selected: SelectedOptimizer) -> int:
        knobs = selected.knobs_of(self.name, L1GenerateKnobs)
        # An unfired host writes no child.
        ladder = selected.knobs_of(Escalation.name, EscalationKnobs).escalation_ladder
        hosted = knobs.recombination_host == self.name or ladder.fires_l2
        return knobs.n_variants + (knobs.recombine if hosted else 0)

    def opening(self, ctx: NodeContext, bench: nodes.RoundOpening) -> nodes.RoundOpening:
        knobs = ctx.knobs(L1GenerateKnobs)
        prior = ctx.round_num - 1
        critiqued = ctx.round_num > 0 and bool(ctx.recall(Critique, prior).readout)
        # A round the critique failed is said: the generate below runs without one.
        critique = "critique from round" if critiqued else "no critique from round"
        return nodes.RoundOpening(
            facts={
                "L1 stalled rounds": CountFact(value=ctx.slice(Ladder).l1_stall_count),
                "L2 after stalled rounds": CountFact(value=ctx.knobs(EscalationKnobs).l1_patience),
                critique: CountFact(value=prior) if prior >= 0 else None,
            },
            arms=variants_this_round(knobs.n_variants, ctx.slice(L2Memory)) + knobs.recombine,
            replayed=bench.replayed,
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

    async def propose(self, ctx: NodeContext, _proposals: Proposals) -> Proposals:
        return await propose_l1_population(ctx)


class PoBB(nodes.Eliminator):
    name: ClassVar[str] = "pobb"
    kind: ClassVar[NodeKind] = NodeKind.ELIMINATOR
    knobs: ClassVar[type[StrictModel]] = PoBBKnobs
    sees: ClassVar[frozenset[type[CycleRead]]] = frozenset(
        {Lineage, Parent, Ruler, Bounds, Progress, Replay}
    )
    abort_lenses: ClassVar[Mapping[str, frozenset[str]]] = ABORT_LENS_SUPPRESS
    # A PoBB stop ends the buying of an arm; θ still reads what it measured.
    stop_disqualifies: ClassVar[bool] = False
    couplings: ClassVar[tuple[nodes.MemberCoupling, ...]] = POBB

    def limits(self, selected: SelectedOptimizer) -> tuple[OptimizerLimit, ...]:
        epsilon = selected.knobs_of(self.name, PoBBKnobs).epsilon
        return (
            OptimizerLimit(
                node=self.name, knob="epsilon", label="PoBB ε", value=epsilon, integer=False
            ),
        )

    def race(self, ctx: NodeContext, panel: RoundSamples) -> PoBBRace:
        return PoBBRace(ctx, panel)


class ThetaElection(nodes.Selector):
    name: ClassVar[str] = "theta_election"
    kind: ClassVar[NodeKind] = NodeKind.SELECTOR
    knobs: ClassVar[type[StrictModel]] = ElectionKnobs
    sees: ClassVar[frozenset[type[CycleRead]]] = frozenset({Bounds, Lineage, Ruler, Replay})
    elects_on: ClassVar[DisplayMetric] = "ability"
    elects_partial: ClassVar[bool] = True

    def admission(self, selected: SelectedOptimizer) -> float:
        return selected.knobs_of(self.name, ElectionKnobs).admission

    def vote(self, selected: SelectedOptimizer) -> Vote:
        return selected.knobs_of(self.name, ElectionKnobs).vote

    def select(self, ctx: NodeContext, measured: MeasuredRound) -> Selection:
        return elect_on_theta(ctx, measured)


class L1Critique(LayoutNode, nodes.Adapter):
    """A malformed response is survived: the next walk re-runs it (`round.py::settle_owed_outputs`)."""

    name: ClassVar[str] = "l1_critique"
    knobs: ClassVar[type[StrictModel]] = TranscriptKnobs
    reads: ClassVar[frozenset[type[StatePart]]] = VIEW_READS
    writes: ClassVar[frozenset[type[StatePart]]] = frozenset({Critique})
    sees: ClassVar[frozenset[type[CycleRead]]] = VIEW_SEES | {Calls}
    response_model: ClassVar[type[BaseModel] | None] = L1CritiqueOutput

    @property
    def consults(self) -> frozenset[type]:
        return frozenset({AdaptiveQueue, Escalation, L1Generate})

    def round_facts(
        self, _selected: SelectedOptimizer, round_result: RoundResult
    ) -> list[OptimizerFact]:
        return critique_facts(round_result)

    async def adapt(self, ctx: NodeContext) -> None:
        ctx.emit(await distil_critique(ctx))


class L2Context(LayoutNode):
    name: ClassVar[str] = "l2_context"
    layer_id: ClassVar[Literal["L2", "L3"]] = "L2"
    reads: ClassVar[frozenset[type[StatePart]]] = VIEW_READS
    writes: ClassVar[frozenset[type[StatePart]]] = frozenset({L2Memory})
    sees: ClassVar[frozenset[type[CycleRead]]] = VIEW_SEES | {Calls, Progress}
    response_model: ClassVar[type[BaseModel] | None] = L2ContextOutput
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

    def reply_model(self, view: PotterView) -> type[BaseModel]:
        return build_l2_response_model(
            view.silent_l1_panels,
            view.recombination_offer(self.name),
            terminating=view.terminate_offer is not None,
            stand_on=tuple(view.reelect_offer),
        )

    def parse(self, ctx: NodeContext, raw: L2ContextOutput) -> TransitionResult:
        # Filtered here: a landed steer is carried forward on every later fire and never pruned.
        steer: dict[str, Any] = {
            key: value
            for key, value in raw.l1_overrides.model_dump(exclude_none=True).items()
            if key in self.steers[L1Generate.name]
        }

        prior = node_layout(
            "l1_generate", ctx.slice(L2Memory), ctx.read(Calls).levers("l1_generate")
        )
        layout_outcomes: list[ValidatorOutcome] = []
        accepted_layout: L1Layout | None = None
        layout_refused = False
        spec = NODE_LAYOUTS[L1Generate.name]
        if breach := unplaceable_edit(raw.l1_layout, spec):
            layout_outcomes = [breach]
            layout_refused = True
        elif proposed_layout := coerce_l1_layout(raw.l1_layout, base=prior):
            layout_result = validate_l1_layout(proposed_layout, spec=spec, prior_layout=prior)
            layout_outcomes = list(layout_result.outcomes)
            if layout_result.is_valid:
                accepted_layout = proposed_layout
                # Whole, never the edit: a move lands relative to where each panel then sat.
                steer["layout"] = proposed_layout.model_dump(mode="json")
            else:
                layout_refused = True

        named = raw.recombinations if isinstance(raw, L2RecombiningOutput) else []
        return TransitionResult(
            rationale=raw.rationale,
            steer=steer,
            axis_targeted=raw.axis_targeted,
            l1_layout=accepted_layout,
            l1_layout_refused=layout_refused,
            l2_guard_breaches=layout_outcomes,
            fork_proposal=raw.fork_proposal,
            terminate_proposal=(
                raw.terminate_proposal if isinstance(raw, L2TerminatingOutput) else None
            ),
            recombinations=[
                Recombination.model_validate(spec.model_dump())
                for spec in named[: ctx.knobs(L1GenerateKnobs).recombine]
            ],
            # The round the view renders for, as the next round's L1 reads it.
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

    def enter_act(self, ctx: NodeContext, fire: Fire) -> StepOpened:
        steered = ctx.slice(L2Memory).steered("l1_generate")
        peak = fire.best.composite_fitness
        stalled = rounds_without_advance(ctx.read(Lineage).rounds)
        return StepOpened(
            node=self.name,
            activity=self.phase.activity,
            facts={
                "fire": CountFact(value=ctx.slice(Ladder).l2.fires + 1),
                "rule": TextFact(value=fire.cause),
                "L1 stalled rounds": CountFact(value=stalled),
                "parent fitness": ctx.read(Parent).fitness,
                "peak fitness": None if peak is None else FitnessFact(value=peak),
                "l1_overrides": SettingsFact(value=steered) if steered else None,
            },
        )

    def exit_act(self, ctx: NodeContext, result: TransitionResult) -> StepClosed:
        return StepClosed(
            facts={
                "param changes": CountFact(value=len(ctx.slice(L2Memory).steered("l1_generate"))),
                # An edit's whole layout, as the panels it places.
                "l1_layout panels": None
                if result.l1_layout is None
                else CountFact(value=len(result.l1_layout.all_placeholders())),
                "axis": TextFact(value=result.axis_targeted) if result.axis_targeted else None,
                "decided": TextFact(value=result.rationale),
            },
            audit=self.name,
        )


class L3Plan(LayoutNode):
    name: ClassVar[str] = "l3_plan"
    layer_id: ClassVar[Literal["L2", "L3"]] = "L3"
    reads: ClassVar[frozenset[type[StatePart]]] = VIEW_READS
    writes: ClassVar[frozenset[type[StatePart]]] = frozenset({L3Memory})
    sees: ClassVar[frozenset[type[CycleRead]]] = VIEW_SEES | {Calls, Progress}
    response_model: ClassVar[type[BaseModel] | None] = L3PlanOutput
    phase: ClassVar[OptimizerPhase] = OptimizerPhase(
        phase=PotterPhase.MODIFY_PLAN, node="l3_plan", activity="replanning"
    )

    @property
    def consults(self) -> frozenset[type]:
        return frozenset({AdaptiveQueue, Escalation, L1Generate})

    def reply_model(self, view: PotterView) -> type[BaseModel]:
        return build_l3_response_model(
            terminating=view.terminate_offer is not None, stand_on=tuple(view.reelect_offer)
        )

    def parse(self, ctx: NodeContext, raw: L3PlanOutput) -> TransitionResult:
        failures = l3_plan_breaches(raw.plan, prior_plan=ctx.slice(L3Memory).plan)
        return TransitionResult(
            rationale=raw.rationale,
            plan=raw.plan,
            l3_note=raw.note,
            l3_guard_breaches=failures,
            fork_proposal=raw.fork_proposal,
            terminate_proposal=(
                raw.terminate_proposal if isinstance(raw, L3TerminatingOutput) else None
            ),
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

    def enter_act(self, ctx: NodeContext, fire: Fire) -> StepOpened:
        return StepOpened(
            node=self.name,
            activity=self.phase.activity,
            facts={
                "fire": CountFact(value=ctx.slice(Ladder).l3.fires + 1),
                "rule": TextFact(value=fire.cause),
                "L2 stalled rounds": None
                if fire.ask is None
                else CountFact(value=fire.ask.l2.stall_count),
                "plan": TextFact(value=ctx.slice(L3Memory).plan),
            },
        )

    def exit_act(self, _ctx: NodeContext, result: TransitionResult) -> StepClosed:
        return StepClosed(
            facts={
                "plan": TextFact(value=result.plan),
                "decided": TextFact(value=result.rationale),
            },
            audit=None,
        )


class Escalation(nodes.Controller):
    name: ClassVar[str] = "escalation"
    kind: ClassVar[NodeKind] = NodeKind.CONTROLLER
    knobs: ClassVar[type[StrictModel]] = EscalationKnobs
    reads: ClassVar[frozenset[type[StatePart]]] = frozenset({L1Readout, Ladder})
    writes: ClassVar[frozenset[type[StatePart]]] = frozenset({Ladder})
    sees: ClassVar[frozenset[type[CycleRead]]] = frozenset({Lineage, Ruler, Bounds, Replay})
    fires: ClassVar[frozenset[type]] = frozenset({L2Context, L3Plan})
    couplings: ClassVar[tuple[nodes.MemberCoupling, ...]] = LADDER
    priced_surface: ClassVar[Mapping[str, int]] = {"escalation_rules": len(ESCALATION_RULES)}

    def limits(self, selected: SelectedOptimizer) -> tuple[OptimizerLimit, ...]:
        esc = selected.knobs_of(self.name, EscalationKnobs)
        patiences = [
            ("l1_patience", "L1 patience", esc.l1_patience),
            ("l2_patience", "L2 patience", esc.l2_patience),
            ("l3_patience", "L3 patience", esc.l3_patience),
        ]
        return tuple(
            OptimizerLimit(
                node=self.name,
                knob=knob,
                label=label,
                value=value,
                integer=True,
            )
            for knob, label, value in patiences
        )

    def bank_round(self, ctx: NodeContext, round_result: RoundResult) -> str | None:
        knobs = ctx.knobs(EscalationKnobs)
        # Structural, not a stall: heals L2 now instead of burning l1_patience rounds.
        l1_mandatory_breach = any(
            vf.reason == ValidationReason.DROPPED_MANDATORY_PLACEHOLDER
            for arm in round_result.proposed.values()
            for vf in arm.validation_failures
        )
        # The same structural fault with an empty panel, so it rides `l1_parse_failure`.
        l1_zero_candidates = ctx.recall(L1Readout, round_result.round).l1_parse_failure is not None
        # The stamped grade's own rates, so routing and verdict cannot diverge.
        evidence_starved = evidence_starved_node(round_result.health.node_failure_rates) is not None
        ladder, event = bank_and_route(
            ctx.slice(Ladder),
            advance=round_result.overlap.advance,
            l1_patience=knobs.l1_patience,
            escalation_ladder=knobs.escalation_ladder,
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
