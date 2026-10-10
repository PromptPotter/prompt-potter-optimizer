from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from promptpotter.application import optimizers
from promptpotter.application.bench.llm_call import (
    LLMCallContext,
    run_optimizer_node,
)
from promptpotter.application.mask.backprop import select_rewind_round
from promptpotter.application.mask.load import load_lineage_spine
from promptpotter.application.optimizers.nodes import OptimizerPhase
from promptpotter.application.optimizers.potter.dispatch.facade import (
    DispatchHub,
    build_bundle,
)
from promptpotter.application.optimizers.potter.dispatch.layout import (
    NODE_LAYOUTS,
    LayoutNode,
    coerce_l1_layout,
    unplaceable_edit,
    validate_l1_layout,
)
from promptpotter.application.optimizers.potter.dispatch.prompts import (
    load_optimizer_prompt,
    node_layout,
)
from promptpotter.application.optimizers.potter.dispatch.schemas import (
    ForkProposal,
    L2ContextOutput,
    L3PlanOutput,
    TerminateProposal,
    build_l2_response_model,
)
from promptpotter.application.optimizers.potter.escalation.rules import (
    L1_LAYOUT_REFUSED,
    L2_PATIENCE_SPENT,
    NEXT_ACTION_WALK,
    LadderInputs,
    NextAction,
    decide_heal,
)
from promptpotter.application.optimizers.potter.escalation.state import (
    LadderAsk,
    LayerReading,
    PotterPhase,
)
from promptpotter.application.optimizers.potter.knobs import EscalationKnobs, potter_knobs
from promptpotter.application.optimizers.potter.records import (
    L1Layout,
    L2L3Memory,
    PotterCheckpointKind,
)
from promptpotter.application.optimizers.potter.validators.l3_output import run_l3_output_validators
from promptpotter.application.views.render.optimizer_prompt_text import fmt_pct
from promptpotter.application.views.render.primitives import fmt_fitness
from promptpotter.domain.phase_views import OptimizerStepEnterView, OptimizerStepExitView
from promptpotter.domain.phases import StopLoop, StopReason
from promptpotter.domain.pipeline_schema import ManifestNodeOverlay
from promptpotter.domain.results import (
    merge_known_outcomes,
    order_floor,
    rounds_without_advance,
)
from promptpotter.domain.run_records import (
    ConfigOverrides,
    ForkTrigger,
    RebaseRequest,
)
from promptpotter.domain.strict_model import StrictModel
from promptpotter.domain.validators import ValidatorOutcome
from promptpotter.infrastructure.llm.json_parse import OptimizerPromptParseError
from promptpotter.infrastructure.llm.telemetry import emit_round_warning
from promptpotter.shared import truncate

if TYPE_CHECKING:
    from pydantic import BaseModel

    from promptpotter.application.bench.node_context import NodeContext
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.application.optimizers.potter.dispatch.bundle import InjectionBundle
    from promptpotter.application.optimizers.potter.state import PotterState
    from promptpotter.domain.ruler import AbilityReading
    from promptpotter.domain.scoring import GradedCell

logger = logging.getLogger(__name__)


@dataclass
class TransitionResult:
    described: str
    steer: dict[str, Any] = field(default_factory=dict)
    plan: str = ""
    l3_note: str = ""
    axis_targeted: str = ""
    l1_layout: L1Layout | None = None
    # The L3 force-trigger reads this, never ``l2_guard_breaches``, which also carries SOFT reports.
    l1_layout_refused: bool = False
    l2_guard_breaches: list[ValidatorOutcome] = field(default_factory=list)
    l3_guard_breaches: list[ValidatorOutcome] = field(default_factory=list)
    fork_proposal: ForkProposal | None = None
    terminate_proposal: TerminateProposal | None = None


@dataclass(frozen=True)
class _HighWater:
    composite_fitness: float | None
    theta: float | None
    theta_se: float | None


def _high_water(ctx: NodeContext[EscalationKnobs]) -> _HighWater:
    """A round stamped on another δ scale than the cycle's is re-read on the cycle's."""
    # `max` keeps the first of equals, so a later round takes the peak only by beating it.
    best = max(ctx.rounds, key=lambda rr: order_floor(rr.composite_fitness))
    view = ctx.difficulty
    peak: AbilityReading | None = None
    frontier: list[GradedCell] = []
    for rr in ctx.rounds:
        frontier = merge_known_outcomes(frontier, rr.results)
        reading = (
            rr.ability
            if rr.ability is not None and rr.ability.ruler_id == view.scale_id
            else view.frontier(frontier).ability
        )
        if reading is not None and (peak is None or reading.theta > peak.theta):
            peak = reading
    return _HighWater(
        composite_fitness=best.composite_fitness,
        theta=None if peak is None else peak.theta,
        theta_se=None if peak is None else peak.se,
    )


FORCED_BY_DIAG = "diag"


@dataclass(frozen=True)
class _Fire:
    cause: str
    # ``None`` is a heal (`EscalationFSM.record_l3_fired`).
    ask: LadderAsk | None
    best: _HighWater


class LayerKnobs(StrictModel):
    """A layer's call config is all it has; its patience is the controller's knob."""


class Layer(LayoutNode):
    knobs: ClassVar[type[StrictModel]] = LayerKnobs
    layer_id: ClassVar[Literal["L2", "L3"]]
    phase: ClassVar[OptimizerPhase]

    def reply_model(self, bundle: InjectionBundle) -> type[BaseModel]:
        assert self.response_model is not None
        return self.response_model

    def parse(self, raw: Any, memory: L2L3Memory) -> TransitionResult:
        raise NotImplementedError

    def write(self, memory: L2L3Memory, result: TransitionResult) -> L2L3Memory:
        raise NotImplementedError

    def record(self, state: PotterState, fire: _Fire) -> None:
        raise NotImplementedError

    def enter_view(
        self, ctx: NodeContext[EscalationKnobs], state: PotterState, fire: _Fire
    ) -> OptimizerStepEnterView:
        raise NotImplementedError

    def exit_view(self, state: PotterState, result: TransitionResult) -> OptimizerStepExitView:
        raise NotImplementedError


class L2Context(Layer):
    name: ClassVar[str] = "l2_context"
    layer_id: ClassVar[Literal["L2", "L3"]] = "L2"
    response_model: ClassVar[type[BaseModel] | None] = L2ContextOutput
    phase: ClassVar[OptimizerPhase] = OptimizerPhase(
        phase=PotterPhase.REFINE_STRATEGY, node="l2_context", activity="refining strategy"
    )
    # `l1_generate`'s empty manifest `param_keys` say L1 does not search its own config; L2 does.
    steers: ClassVar[Mapping[str, frozenset[str]]] = {
        "l1_generate": frozenset({"temperature", "n_variants"})
    }

    def reply_model(self, bundle: InjectionBundle) -> type[BaseModel]:
        return build_l2_response_model(bundle.silent_l1_panels)

    def parse(self, raw: L2ContextOutput, memory: L2L3Memory) -> TransitionResult:
        # An absent reason is REPORTED, never replaced by a sentence that reads as L2's own.
        rationale = truncate(raw.rationale, 80) if raw.rationale else "(no rationale given)"
        # Filtered here: a landed steer is carried forward on every later fire and never pruned.
        steer: dict[str, Any] = {
            key: value
            for key, value in raw.l1_overrides.items()
            if key in self.steers["l1_generate"]
            and isinstance(value, int | float)
            and not isinstance(value, bool)
        }

        prior = node_layout("l1_generate", memory)
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

        return TransitionResult(
            described=f"L2: {rationale}",
            steer=steer,
            axis_targeted=raw.axis_targeted,
            l1_layout=accepted_layout,
            l1_layout_refused=layout_refused,
            l2_guard_breaches=layout_outcomes,
            fork_proposal=raw.fork_proposal,
            terminate_proposal=raw.terminate_proposal,
        )

    def write(self, memory: L2L3Memory, result: TransitionResult) -> L2L3Memory:
        steered = memory.steered_onto("l1_generate", result.steer) if result.steer else memory
        return steered.wounded(l2_guard_breaches=list(result.l2_guard_breaches))

    def record(self, state: PotterState, fire: _Fire) -> None:
        assert fire.ask is not None, "only L3 heals"
        state.escalation.record_l2_fired(fire.ask.l2)

    def enter_view(
        self, ctx: NodeContext[EscalationKnobs], state: PotterState, fire: _Fire
    ) -> OptimizerStepEnterView:
        items = [(k, str(v)) for k, v in state.memory.steered("l1_generate").items()]
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
            tag=f"L2 fire {state.escalation.ladder.l2.fires + 1}",
            lines=(
                f"rule={fire.cause}  |  "
                f"L1 stalled {rounds_without_advance(ctx.rounds)} rounds  |  "
                f"acc={fmt_pct(ctx.parent_accuracy)}  "
                f"peak fitness={fmt_fitness(fire.best.composite_fitness)}",
                overrides,
                "LLM analyzing failure patterns...",
            ),
        )

    def exit_view(self, state: PotterState, result: TransitionResult) -> OptimizerStepExitView:
        layout = ", l1_layout edited" if result.l1_layout is not None else ""
        axis = f", axis={result.axis_targeted}" if result.axis_targeted else ""
        changed = len(state.memory.steered("l1_generate"))
        return OptimizerStepExitView(
            headline=f"L2 decision: {changed} param changes{layout}{axis}",
            details=(result.described,),
            audit=("L2 call", self.name),
        )


class L3Plan(Layer):
    name: ClassVar[str] = "l3_plan"
    layer_id: ClassVar[Literal["L2", "L3"]] = "L3"
    response_model: ClassVar[type[BaseModel] | None] = L3PlanOutput
    phase: ClassVar[OptimizerPhase] = OptimizerPhase(
        phase=PotterPhase.MODIFY_PLAN, node="l3_plan", activity="replanning"
    )

    def parse(self, raw: L3PlanOutput, memory: L2L3Memory) -> TransitionResult:
        new_plan = raw.plan or memory.plan
        rationale = truncate(raw.rationale, 80) if raw.rationale else "(no rationale given)"
        failures = run_l3_output_validators({"plan": new_plan}, prior_plan=memory.plan)
        if failures:
            logger.warning(
                "L3 output failed %d validator(s): %s",
                len(failures),
                ", ".join(o.validator_id for o in failures),
            )
        return TransitionResult(
            described=f"L3: {rationale}",
            plan=new_plan,
            l3_note=raw.note,
            l3_guard_breaches=failures,
            fork_proposal=raw.fork_proposal,
            terminate_proposal=raw.terminate_proposal,
        )

    def write(self, memory: L2L3Memory, result: TransitionResult) -> L2L3Memory:
        # The overwrite, possibly with ``""``, is the "cleared only when L3 fires again" contract.
        return memory.model_copy(update={"plan": result.plan}).wounded(
            l3_note=result.l3_note, l3_guard_breaches=list(result.l3_guard_breaches)
        )

    def record(self, state: PotterState, fire: _Fire) -> None:
        state.escalation.record_l3_fired(None if fire.ask is None else fire.ask.l3)

    def enter_view(
        self, ctx: NodeContext[EscalationKnobs], state: PotterState, fire: _Fire
    ) -> OptimizerStepEnterView:
        return OptimizerStepEnterView(
            node=self.name,
            activity=self.phase.activity,
            title="L3 MODIFY PLAN",
            tag=f"L3 fire {state.escalation.ladder.l3.fires + 1}",
            lines=(
                "Healing L2's refused l1_layout edit"
                if fire.ask is None
                else f"L2 stalled {fire.ask.l2.stall_count} rounds",
                f"Current plan: {truncate(state.memory.plan[:120], 55, '...')}",
                "LLM designing new strategy...",
            ),
        )

    def exit_view(self, state: PotterState, result: TransitionResult) -> OptimizerStepExitView:
        return OptimizerStepExitView(
            headline=f"New plan: {truncate(result.plan[:120], 55, '...')}",
            details=(result.described,),
            audit=None,
        )


L2 = L2Context()
L3 = L3Plan()


def _fired(ctx: NodeContext[EscalationKnobs], action: NextAction) -> Layer:
    layer = optimizers.member(ctx.optimizer.schema.pipelines[NEXT_ACTION_WALK[action]][0])
    if not isinstance(layer, Layer):
        raise TypeError(f"{NEXT_ACTION_WALK[action]!r} leads with {layer.name!r}, no layer")
    return layer


async def _run_transition(
    transition: Layer,
    ctx: NodeContext[EscalationKnobs],
    state: PotterState,
    fire: _Fire,
) -> TransitionResult | None:
    """``None`` when the layer's output never parsed."""
    session = state.session
    round_num = ctx.round_num
    callbacks = ctx.callbacks
    callbacks.on_phase(
        transition.phase.phase,
        "enter",
        round=round_num,
        view=transition.enter_view(ctx, state, fire),
    )
    # The prompt reports the verdict that asked for it, which lands only if this fire does.
    seen = (
        state
        if fire.ask is None
        else replace(state, escalation=state.escalation.as_read_by(fire.ask))
    )
    bundle = build_bundle(ctx, seen)
    filled = DispatchHub.fill(load_optimizer_prompt(transition.name), bundle, node=transition.name)
    try:
        raw, _, _ = await run_optimizer_node(
            template_name=transition.name,
            prompt_vars=filled.injection_vars,
            template=filled.template,
            response_model=transition.reply_model(bundle),
            context=LLMCallContext(
                ledger=session.state.ledger,
                round_num=round_num,
                cache=session.store.optimizer_reuse,
                injections=filled.breakdown,
            ),
        )
        result = transition.parse(raw, state.memory)
    except OptimizerPromptParseError as parse_err:
        # Unhandled it kills the cycle, and under L4 that voids a whole outer sample.
        logger.error(
            "%s: optimizer prompt parse failure — refinement discarded, prior framing kept. [%s]",
            transition.name,
            parse_err.diagnosis(),
        )
        emit_round_warning(
            kind="layer_parse_failure",
            severity="error",
            message=(
                f"{transition.name} returned unusable output "
                f"({'empty/truncated' if parse_err.is_empty else 'schema-noncompliant'}) "
                "— the prior framing was kept and the loop continues."
            ),
            detail={
                "node": transition.name,
                "layer": transition.layer_id,
                **parse_err.warning_detail(),
            },
        )
        callbacks.on_phase(
            transition.phase.phase,
            "exit",
            round=round_num,
            view=OptimizerStepExitView(headline="", details=(), audit=None),
        )
        return None

    state.memory = transition.write(state.memory, result)
    transition.record(state, fire)
    # The fire landed after its round closed: restated, since a resume or fork starts from that state.
    ctx.restate(state.restated(ctx.rounds[-1]))
    callbacks.on_phase(
        transition.phase.phase,
        "exit",
        round=round_num,
        view=transition.exit_view(state, result),
    )
    knobs = potter_knobs(ctx.optimizer)

    # Terminate outranks rebase.
    if result.terminate_proposal is not None:
        reason = result.terminate_proposal.reason.strip()
        if not knobs.escalation.terminate_capability:
            # A model can volunteer the field with no guidance in the prompt; it must not abort the run.
            logger.warning(
                "%s emitted terminate_proposal while terminate_capability is off — ignored",
                transition.layer_id,
            )
        elif not reason:
            # Never substitute a stand-in reason: a blank stop is not a decision.
            logger.warning(
                "%s emitted a blank terminate_proposal — ignored, cycle continues",
                transition.layer_id,
            )
            emit_round_warning(
                kind="layer_terminate_blank",
                message=(
                    f"{transition.layer_id} asked to stop the cycle but named no reason, so the "
                    "request was ignored and the run continued. Nothing is wrong with this "
                    "cycle; the layer's own output schema let it fill the stop field with an "
                    "empty string."
                ),
                detail={"layer": transition.layer_id, "round": round_num},
            )
        else:
            logger.info(
                "%s emitted terminate_proposal — cycle will exit HALTED (ABORT): %s",
                transition.layer_id,
                reason,
            )
            emit_round_warning(
                kind="layer_terminated_cycle",
                message=(
                    f"{transition.layer_id} stopped this cycle: {reason} Nothing here resumes on "
                    "its own — fix what that names, then `python -m promptpotter resume` picks "
                    "the cycle up where it halted."
                ),
                severity="error",
                detail={"layer": transition.layer_id, "reason": reason, "round": round_num},
            )
            raise StopLoop(StopReason.OPTIMIZER_ABORT)

    if result.fork_proposal is not None:
        if not knobs.escalation.rebase_capability:
            logger.warning(
                "%s emitted fork_proposal while rebase_capability is off — ignored",
                transition.layer_id,
            )
        elif fork := _rebase_request(
            session, ctx.optimizer, transition.layer_id, result.fork_proposal, round_num
        ):
            raise StopLoop(StopReason.REBASED, fork=fork)

    return result


def _rebase_request(
    session: Session,
    selected: SelectedOptimizer,
    layer_id: str,
    proposal: ForkProposal,
    round_num: int,
) -> RebaseRequest | None:
    spine = load_lineage_spine(session.store, session.campaign_id)
    target_round = select_rewind_round(
        spine, cycle_id=session.state.cycle_id or "", current_round=round_num
    )
    if target_round is None:
        # A rewind to nowhere mints a duplicate of this cycle.
        logger.warning(
            "%s emitted fork_proposal at round %d but no ancestor is available to rewind to — ignored",
            layer_id,
            round_num,
        )
        return None

    unlock = bool(proposal.unlock_schema_field_rename) and not (
        potter_knobs(selected).l1_generate.schema_field_rename
    )
    request = RebaseRequest(
        fork_from_round=target_round,
        trigger=ForkTrigger.OPTIMIZER_REBASE,
        reason=str(proposal.reason or f"{layer_id} fork_proposal"),
        issued_by=f"{layer_id}/round_{round_num}",
        config_overrides=(
            ConfigOverrides(
                nodes={"l1_generate": ManifestNodeOverlay(config={"schema_field_rename": True})}
            )
            if unlock
            else None
        ),
    )
    logger.info(
        "%s emitted fork_proposal; UCB selected round %d of %d as the rewind target%s "
        "— cycle will exit with REBASED",
        layer_id,
        target_round,
        round_num,
        ", unlocking schema_field_rename" if unlock else "",
    )
    return request


def _record_trigger(
    ctx: NodeContext[EscalationKnobs],
    patience: int | None,
    reading: LayerReading,
    best: _HighWater,
    *,
    kind: PotterCheckpointKind,
    layer: Literal["l2", "l3"],
    counter_round: int,
    fired: bool,
    rule: str,
    heal: bool | None = None,
    l2_guard_breaches: list[str] | None = None,
) -> None:
    asked: dict[str, Any] = {"rule": rule}
    if heal is not None:
        asked["heal"] = heal
    if l2_guard_breaches is not None:
        asked["l2_guard_breaches"] = l2_guard_breaches
    ctx.decide(
        kind,
        {
            "round_num": ctx.round_num,
            f"{layer}_patience": patience,
            "entry_round": counter_round if counter_round > 0 else -1,
        },
        fired,
        data={
            f"{layer}_round": counter_round,
            "stall_count": reading.stall_count,
            "best_composite_fitness_at_entry": reading.best_composite_fitness_at_entry,
            "best_composite_fitness_this_round": best.composite_fitness,
            "best_theta_at_entry": reading.best_theta_at_entry,
            "best_theta_this_round": best.theta,
            "comparator": reading.comparator,
            **asked,
        },
    )


async def escalate_l2(
    ctx: NodeContext[EscalationKnobs], state: PotterState, *, cause: str
) -> StopReason | None:
    opt = ctx.knobs
    esc = state.escalation
    best = _high_water(ctx)

    ask = esc.ask_l2_escalation(
        current_composite_fitness=best.composite_fitness,
        current_theta=best.theta,
        current_theta_se=best.theta_se,
        escalation_ladder=opt.escalation_ladder,
        l2_patience=opt.l2_patience,
        l3_patience=opt.l3_patience,
    )

    # Recorded fired or not: the verdict is a fold over the cycle, which no replayer holds.
    _record_trigger(
        ctx,
        opt.l2_patience,
        ask.l2,
        best,
        kind=PotterCheckpointKind.L2_ESCALATION_TRIGGER,
        layer="l2",
        counter_round=esc.ladder.l2.fires,
        fired=ask.next_action == NextAction.FIRE_L2,
        rule=cause,
    )

    if ask.next_action == NextAction.FIRE_L2:
        result = await _run_transition(
            _fired(ctx, NextAction.FIRE_L2), ctx, state, _Fire(cause, ask, best)
        )
        heal = decide_heal(
            LadderInputs(
                escalation_ladder=opt.escalation_ladder,
                l2_stall_count=ask.l2.stall_count,
                l2_patience=opt.l2_patience,
                l3_stall_count=ask.l3.stall_count,
                l3_patience=opt.l3_patience,
                l1_layout_refused=result is not None and result.l1_layout_refused,
            )
        )
        if heal.next_action is NextAction.FIRE_L3:
            assert result is not None
            logger.warning(
                "L3 force-triggered — L2's l1_layout edit was refused at round %d", ctx.round_num
            )
            # A heal reads no gate, so its record banks the counters the ladder holds.
            l3 = esc.ladder.l3
            held = LayerReading(
                l3.stall_count,
                l3.best_composite_fitness_at_entry,
                l3.best_theta_at_entry,
                None,
            )
            _record_trigger(
                ctx,
                opt.l3_patience,
                held,
                best,
                kind=PotterCheckpointKind.L3_ESCALATION_TRIGGER,
                layer="l3",
                counter_round=l3.fires,
                fired=True,
                rule=L1_LAYOUT_REFUSED,
                heal=True,
                l2_guard_breaches=[o.validator_id for o in result.l2_guard_breaches],
            )
            await _run_transition(
                _fired(ctx, heal.next_action), ctx, state, _Fire(L1_LAYOUT_REFUSED, None, best)
            )
        return None

    _record_trigger(
        ctx,
        opt.l3_patience,
        ask.l3,
        best,
        kind=PotterCheckpointKind.L3_ESCALATION_TRIGGER,
        layer="l3",
        counter_round=esc.ladder.l3.fires,
        fired=ask.next_action == NextAction.FIRE_L3,
        rule=L2_PATIENCE_SPENT,
        heal=False,
        l2_guard_breaches=[],
    )

    if ask.next_action == NextAction.FIRE_L3:
        await _run_transition(
            _fired(ctx, ask.next_action), ctx, state, _Fire(L2_PATIENCE_SPENT, ask, best)
        )
        return None

    return ask.stop_reason


__all__ = [
    "FORCED_BY_DIAG",
    "L2",
    "L3",
    "escalate_l2",
]
