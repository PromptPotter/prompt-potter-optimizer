from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Literal, NamedTuple, Protocol

from promptpotter.application.bench.llm_call import run_optimizer_node
from promptpotter.application.bench.node_context import Calls, Lineage, Progress, Replay, Ruler
from promptpotter.application.optimizers.nodes import OptimizerPhase
from promptpotter.application.optimizers.potter.dispatch.facade import (
    DispatchHub,
    build_bundle,
)
from promptpotter.application.optimizers.potter.dispatch.prompts import load_optimizer_prompt
from promptpotter.application.optimizers.potter.dispatch.schemas import (
    ForkProposal,
    TerminateProposal,
)
from promptpotter.application.optimizers.potter.escalation.rules import (
    L1_LAYOUT_REFUSED,
    L2_PATIENCE_SPENT,
    LadderInputs,
    NextAction,
    decide_heal,
)
from promptpotter.application.optimizers.potter.escalation.state import (
    HighWater,
    LadderAsk,
    LayerReading,
    as_read_by,
    ask_l2_escalation,
)
from promptpotter.application.optimizers.potter.knobs import EscalationKnobs, L1GenerateKnobs
from promptpotter.application.optimizers.potter.records import (
    L1Layout,
    Ladder,
    PotterCheckpointKind,
    Recombination,
)
from promptpotter.domain.arm_reading import order_floor
from promptpotter.domain.phase_views import OptimizerStepExitView
from promptpotter.domain.phases import StopLoop, StopReason
from promptpotter.domain.pipeline_schema import ManifestNodeOverlay
from promptpotter.domain.run_records import (
    MAX_AUTO_REBASES,
    ConfigOverrides,
    ForkTrigger,
    RebaseRequest,
)
from promptpotter.domain.validators import ValidatorOutcome
from promptpotter.infrastructure.llm.json_parse import OptimizerPromptParseError
from promptpotter.infrastructure.llm.telemetry import emit_round_warning
from promptpotter.shared.identity import Principal

if TYPE_CHECKING:
    from pydantic import BaseModel

    from promptpotter.application.bench.node_context import NodeContext
    from promptpotter.application.optimizers.potter.dispatch.bundle import InjectionBundle
    from promptpotter.domain.phase_views import OptimizerStepEnterView

logger = logging.getLogger(__name__)


@dataclass
class TransitionResult:
    described: str
    steer: dict[str, Any] = field(default_factory=dict)
    plan: str = ""
    l3_note: str = ""
    axis_targeted: str = ""
    l1_layout: L1Layout | None = None
    # The L3 force-trigger reads this, never the L2 guard breaches, which also carry SOFT reports.
    l1_layout_refused: bool = False
    l2_guard_breaches: list[ValidatorOutcome] = field(default_factory=list)
    l3_guard_breaches: list[ValidatorOutcome] = field(default_factory=list)
    fork_proposal: ForkProposal | None = None
    terminate_proposal: TerminateProposal | None = None
    # L2's as a recombination host: the children the next round's L1 adds, and that round.
    recombinations: list[Recombination] = field(default_factory=list)
    for_round: int | None = None


def _high_water(ctx: NodeContext) -> HighWater:
    rounds = ctx.read(Lineage).rounds
    # `max` keeps the first of equals, so a later round takes the peak only by beating it.
    best = max(
        (rr.fold for rr in rounds),
        key=lambda fold: order_floor(None if fold is None else fold.composite_fitness),
    )
    series = ctx.read(Ruler).level_series
    peak: int | None = None
    top: float | None = None
    for rnd in series.rounds:
        reading = series.level(rnd)
        if reading is not None and (top is None or reading.theta > top):
            peak, top = rnd, reading.theta
    return HighWater(
        composite_fitness=None if best is None else best.composite_fitness,
        series=series,
        theta_round=peak,
    )


FORCED_BY_DIAG = "diag"


@dataclass(frozen=True)
class Fire:
    cause: str
    # ``None`` is a heal (`state.py::l3_fired`).
    ask: LadderAsk | None
    best: HighWater


class Layer(Protocol):
    """Fired by the `escalation` controller, under its own node context: it writes its own memory, never the ladder; its patience is the controller's knob."""

    name: ClassVar[str]
    layer_id: ClassVar[Literal["L2", "L3"]]
    phase: ClassVar[OptimizerPhase]

    def reply_model(self, bundle: InjectionBundle) -> type[BaseModel]: ...

    def parse(self, ctx: NodeContext, raw: Any) -> TransitionResult: ...

    def land(self, ctx: NodeContext, result: TransitionResult) -> None:
        """Puts its memory and restates it onto the closed round, since a resume or fork starts from there."""

    def record(self, ladder: Ladder, fire: Fire) -> Ladder: ...

    def enter_view(self, ctx: NodeContext, fire: Fire) -> OptimizerStepEnterView: ...

    def exit_view(self, ctx: NodeContext, result: TransitionResult) -> OptimizerStepExitView: ...


class LayerCall(NamedTuple):
    """A layer the controller fires, with the context it runs under (`NodeContext.fire`)."""

    layer: Layer
    ctx: NodeContext


async def _run_transition(fired: LayerCall, fire: Fire) -> TransitionResult | None:
    """``None`` when the layer's output never parsed."""
    layer, ctx = fired
    ctx.read(Progress).phase(layer.phase.phase, "enter", layer.enter_view(ctx, fire))
    ladder = ctx.slice(Ladder)
    # The prompt reports the verdict that asked for it, which lands only if this fire does.
    bundle = build_bundle(ctx, ladder if fire.ask is None else as_read_by(ladder, fire.ask))
    filled = DispatchHub.fill(load_optimizer_prompt(layer.name), bundle, node=layer.name)
    try:
        raw, _, _ = await run_optimizer_node(
            template_name=layer.name,
            prompt_vars=filled.injection_vars,
            template=filled.template,
            response_model=layer.reply_model(bundle),
            context=ctx.read(Calls).call_context(filled.breakdown),
        )
        result = layer.parse(ctx, raw)
    except OptimizerPromptParseError as parse_err:
        # Unhandled it kills the cycle, and under L4 that voids a whole outer sample.
        logger.error(
            "%s: optimizer prompt parse failure — refinement discarded, prior framing kept. [%s]",
            layer.name,
            parse_err.diagnosis(),
        )
        emit_round_warning(
            kind="layer_parse_failure",
            severity="error",
            message=(
                f"{layer.name} returned unusable output "
                f"({'empty/truncated' if parse_err.is_empty else 'schema-noncompliant'}) "
                "— the prior framing was kept and the loop continues."
            ),
            detail={
                "node": layer.name,
                "layer": layer.layer_id,
                **parse_err.warning_detail(),
            },
        )
        ctx.read(Progress).phase(
            layer.phase.phase, "exit", OptimizerStepExitView(headline="", details=(), audit=None)
        )
        return None

    layer.land(ctx, result)
    ctx.read(Progress).phase(layer.phase.phase, "exit", layer.exit_view(ctx, result))
    return result


def _layer_control(fired: LayerCall, result: TransitionResult) -> None:
    """The layer-control channel, read after the ladder banked the fire: terminate outranks rebase."""
    layer, ctx = fired
    round_num = ctx.round_num
    capability = ctx.knobs(EscalationKnobs)
    if result.terminate_proposal is not None:
        reason = result.terminate_proposal.reason.strip()
        if not capability.terminate_capability:
            # A model can volunteer the field with no guidance in the prompt; it must not abort the run.
            logger.warning(
                "%s emitted terminate_proposal while terminate_capability is off — ignored",
                layer.layer_id,
            )
        elif not reason:
            # Never substitute a stand-in reason: a blank stop is not a decision.
            logger.warning(
                "%s emitted a blank terminate_proposal — ignored, cycle continues",
                layer.layer_id,
            )
            emit_round_warning(
                kind="layer_terminate_blank",
                message=(
                    f"{layer.layer_id} asked to stop the cycle but named no reason, so the "
                    "request was ignored and the run continued. Nothing is wrong with this "
                    "cycle; the layer's own output schema let it fill the stop field with an "
                    "empty string."
                ),
                detail={"layer": layer.layer_id, "round": round_num},
            )
        else:
            logger.info(
                "%s emitted terminate_proposal — cycle will exit HALTED (ABORT): %s",
                layer.layer_id,
                reason,
            )
            emit_round_warning(
                kind="layer_terminated_cycle",
                message=(
                    f"{layer.layer_id} stopped this cycle: {reason} Nothing here resumes on "
                    "its own — fix what that names, then `python -m promptpotter resume` picks "
                    "the cycle up where it halted."
                ),
                severity="error",
                detail={"layer": layer.layer_id, "reason": reason, "round": round_num},
            )
            raise StopLoop(StopReason.OPTIMIZER_ABORT)

    if result.fork_proposal is not None:
        if not capability.rebase_capability:
            logger.warning(
                "%s emitted fork_proposal while rebase_capability is off — ignored",
                layer.layer_id,
            )
        elif fork := _rebase_request(ctx, layer.layer_id, result.fork_proposal):
            raise StopLoop(StopReason.REBASED, fork=fork)


def _rebase_request(
    ctx: NodeContext, layer_id: str, proposal: ForkProposal
) -> RebaseRequest | None:
    round_num = ctx.round_num
    lineage = ctx.read(Lineage)
    if lineage.rebases >= MAX_AUTO_REBASES:
        emit_round_warning(
            kind="layer_rebase_dropped",
            message=(
                f"{layer_id} asked to rewind this cycle, but this launch has already rebased "
                f"{lineage.rebases} times, the most one launch may. The request was dropped and "
                "the run continued on this line."
            ),
            detail={"layer": layer_id, "round": round_num, "caveats": []},
        )
        return None
    target_round = lineage.rewind_round()
    if target_round is None:
        # A rewind to nowhere mints a duplicate of this cycle.
        series = ctx.read(Ruler).level_series
        withheld = sorted(
            {
                read.caveat
                for rnd in series.rounds
                if rnd < round_num
                and (read := series.reading(rnd)) is not None
                and read.caveat is not None
            }
        )
        why = (
            f"θ is withheld there under {', '.join(c.value for c in withheld)}"
            if withheld
            else "none holds a level"
        )
        emit_round_warning(
            kind="layer_rebase_dropped",
            message=(
                f"{layer_id} asked to rewind this cycle, but no earlier round can be chosen: "
                f"{why}. The request was dropped and the run continued on this line."
            ),
            detail={
                "layer": layer_id,
                "round": round_num,
                "caveats": [c.value for c in withheld],
            },
        )
        return None

    unlock = bool(proposal.unlock_schema_field_rename) and not (
        ctx.knobs(L1GenerateKnobs).schema_field_rename
    )
    request = RebaseRequest(
        fork_from_round=target_round,
        trigger=ForkTrigger.OPTIMIZER_REBASE,
        reason=str(proposal.reason or f"{layer_id} fork_proposal"),
        issued_by=Principal(f"{layer_id}/round_{round_num}", layer_id),
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


async def _fire_layer(ctx: NodeContext, fired: LayerCall, fire: Fire) -> TransitionResult | None:
    """The ladder is the controller's: it banks the fire once the layer landed, before the layer's control channel."""
    result = await _run_transition(fired, fire)
    if result is None:
        return None
    ctx.put(fired.layer.record(ctx.slice(Ladder), fire))
    ctx.restate(Ladder)
    _layer_control(fired, result)
    return result


def _record_trigger(
    ctx: NodeContext,
    patience: int | None,
    reading: LayerReading,
    best: HighWater,
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
    ctx.read(Replay).decide(
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
            "best_theta_at_entry": best.theta(reading.theta_peak_round_at_entry),
            "best_theta_this_round": best.theta(best.theta_round),
            "comparator": reading.comparator,
            **asked,
        },
    )


async def escalate_l2(
    ctx: NodeContext, *, cause: str, l2: LayerCall, l3: LayerCall
) -> StopReason | None:
    opt = ctx.knobs(EscalationKnobs)
    best = _high_water(ctx)

    ask = ask_l2_escalation(
        ctx.slice(Ladder),
        best=best,
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
        counter_round=ctx.slice(Ladder).l2.fires,
        fired=ask.next_action == NextAction.FIRE_L2,
        rule=cause,
    )

    if ask.next_action == NextAction.FIRE_L2:
        result = await _fire_layer(ctx, l2, Fire(cause, ask, best))
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
            held_l3 = ctx.slice(Ladder).l3
            held = LayerReading(
                held_l3.stall_count,
                held_l3.best_composite_fitness_at_entry,
                held_l3.theta_peak_round_at_entry,
                None,
            )
            _record_trigger(
                ctx,
                opt.l3_patience,
                held,
                best,
                kind=PotterCheckpointKind.L3_ESCALATION_TRIGGER,
                layer="l3",
                counter_round=held_l3.fires,
                fired=True,
                rule=L1_LAYOUT_REFUSED,
                heal=True,
                l2_guard_breaches=[o.validator_id for o in result.l2_guard_breaches],
            )
            await _fire_layer(ctx, l3, Fire(L1_LAYOUT_REFUSED, None, best))
        return None
    if ask.next_action is NextAction.CONTINUE:
        return None

    _record_trigger(
        ctx,
        opt.l3_patience,
        ask.l3,
        best,
        kind=PotterCheckpointKind.L3_ESCALATION_TRIGGER,
        layer="l3",
        counter_round=ctx.slice(Ladder).l3.fires,
        fired=ask.next_action == NextAction.FIRE_L3,
        rule=L2_PATIENCE_SPENT,
        heal=False,
        l2_guard_breaches=[],
    )

    if ask.next_action == NextAction.FIRE_L3:
        await _fire_layer(ctx, l3, Fire(L2_PATIENCE_SPENT, ask, best))
        return None

    return ask.stop_reason
