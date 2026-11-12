from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Literal, NamedTuple, Protocol, Self

from promptpotter.application.bench.node_context import Calls, Lineage, Progress, Replay, Ruler
from promptpotter.application.optimizers.nodes import OptimizerPhase
from promptpotter.application.optimizers.potter.dispatch.facade import (
    DispatchHub,
    build_bundle,
)
from promptpotter.application.optimizers.potter.dispatch.schemas import (
    ForkProposal,
    StandingForkProposal,
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
from promptpotter.application.optimizers.potter.records import L1Layout, Ladder, Recombination
from promptpotter.domain.arm_reading import order_floor
from promptpotter.domain.phase_views import OptimizerStepExitView
from promptpotter.domain.phases import StopLoop, StopReason
from promptpotter.domain.pipeline_schema import ManifestNodeOverlay
from promptpotter.domain.results import Reelection
from promptpotter.domain.run_records import (
    CheckpointInputs,
    ConfigOverrides,
    ForkTrigger,
    RebaseRequest,
)
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.domain.validators import ValidatorOutcome
from promptpotter.infrastructure.llm.json_parse import OptimizerPromptParseError
from promptpotter.infrastructure.llm.telemetry import emit_round_warning
from promptpotter.shared.identity import Principal

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import BaseModel

    from promptpotter.application.bench.node_context import NodeContext
    from promptpotter.application.optimizers.potter.dispatch.bundle import InjectionBundle
    from promptpotter.domain.paired_reading import RoundArm
    from promptpotter.domain.phase_views import OptimizerStepEnterView
    from promptpotter.domain.run_records import RoundStatement

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


async def _run_transition(
    fired: LayerCall, fire: Fire
) -> tuple[TransitionResult, InjectionBundle] | None:
    """With the bundle it was composed from; ``None`` when the layer's output never parsed."""
    layer, ctx = fired
    ctx.read(Progress).phase(layer.phase.phase, "enter", layer.enter_view(ctx, fire))
    ladder = ctx.slice(Ladder)
    # The prompt reports the verdict that asked for it, which lands only if this fire does.
    bundle = build_bundle(ctx, ladder if fire.ask is None else as_read_by(ladder, fire.ask))
    calls = ctx.read(Calls)
    filled = DispatchHub.fill(calls.prompt(), bundle, node=ctx.node)
    try:
        result = layer.parse(ctx, await calls.answer(layer.reply_model(bundle), filled))
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
    return result, bundle


def _layer_control(fired: LayerCall, result: TransitionResult, bundle: InjectionBundle) -> None:
    """The layer-control channel, read after the ladder banked the fire: terminate outranks rebase."""
    layer, ctx = fired
    capability = ctx.knobs(EscalationKnobs)
    if result.terminate_proposal is not None:
        # Present only where the reply schema offered it (`InjectionBundle.terminate_offer`).
        reason = result.terminate_proposal.reason.strip()
        if not reason:
            # Never substitute a stand-in reason: a blank stop is not a decision.
            logger.warning(
                "%s emitted a blank terminate_proposal — ignored, cycle continues",
                layer.layer_id,
            )
        else:
            logger.info(
                "%s emitted terminate_proposal — cycle will exit HALTED (ABORT): %s",
                layer.layer_id,
                reason,
            )
            raise StopLoop(StopReason.OPTIMIZER_ABORT)

    if result.fork_proposal is not None:
        if not capability.rebase_capability:
            logger.warning(
                "%s emitted fork_proposal while rebase_capability is off — ignored",
                layer.layer_id,
            )
        elif fork := _rebase_request(
            ctx, layer.layer_id, result.fork_proposal, bundle.reelect_offer
        ):
            raise StopLoop(StopReason.REBASED, fork=fork)


def _rebase_request(
    ctx: NodeContext, layer_id: str, proposal: ForkProposal, offer: Mapping[str, RoundArm]
) -> RebaseRequest | None:
    round_num = ctx.round_num
    unlock = proposal.unlock_schema_field_rename and not (
        ctx.knobs(L1GenerateKnobs).schema_field_rename
    )
    reason = proposal.reason or f"{layer_id} fork_proposal"
    issued_by = Principal(f"{layer_id}/round_{round_num}", layer_id)
    lineage = ctx.read(Lineage)
    hangs_from: RoundStatement | None = None
    # The parse closed `stand_on` on the offer (`schemas.py::fork_proposal_model`).
    stand_on = proposal.stand_on if isinstance(proposal, StandingForkProposal) else ""
    if stand_on:
        at = offer[stand_on]
        branch: int | None = at.round
        held = next(rr for rr in lineage.rounds if rr.round == at.round)
        assert held.election is not None, "ELECTIONS lists only rounds that elected"
        if held.election.selected != at.arm:
            hangs_from = lineage.reelected(at, Reelection(by=issued_by, reason=reason))
    else:
        branch = lineage.branch_round(changing=unlock)
    if branch is None:
        series = ctx.read(Ruler).level_series
        withheld = sorted(
            {
                read.caveat
                for rnd in series.rounds
                if rnd <= round_num
                and (read := series.reading(rnd)) is not None
                and read.caveat is not None
            }
        )
        logger.warning(
            "%s emitted fork_proposal changing %s, and no round it may stand on holds a level%s "
            "— dropped",
            layer_id,
            "schema_field_rename"
            if unlock
            else "nothing (only a promoted individual the line left may take it)",
            f" (θ withheld under {', '.join(c.value for c in withheld)})" if withheld else "",
        )
        return None

    request = RebaseRequest(
        fork_from_round=branch + 1,
        trigger=ForkTrigger.OPTIMIZER_REBASE,
        reason=reason,
        issued_by=issued_by,
        config_overrides=(
            ConfigOverrides(
                nodes={"l1_generate": ManifestNodeOverlay(config={"schema_field_rename": True})}
            )
            if unlock
            else None
        ),
        hangs_from=hangs_from,
    )
    logger.info(
        "%s emitted fork_proposal; %s stands the fork on round %d of %d%s%s "
        "— cycle will exit with REBASED",
        layer_id,
        "it" if stand_on else "UCB",
        branch,
        round_num,
        f", re-electing {stand_on}" if hangs_from is not None else "",
        ", unlocking schema_field_rename" if unlock else "",
    )
    return request


async def _fire_layer(ctx: NodeContext, fired: LayerCall, fire: Fire) -> TransitionResult | None:
    """The ladder is the controller's: it banks the fire once the layer landed, before the layer's control channel."""
    landed = await _run_transition(fired, fire)
    if landed is None:
        return None
    result, bundle = landed
    ctx.put(fired.layer.record(ctx.slice(Ladder), fire))
    ctx.restate(Ladder)
    _layer_control(fired, result, bundle)
    return result


class EscalationReading(FrozenModel):
    patience: int | None
    fires: int
    stall_count: int
    best_composite_fitness_at_entry: float | None
    best_composite_fitness_this_round: float | None
    best_theta_at_entry: float | None
    best_theta_this_round: float | None
    comparator: str | None
    rule: str

    @classmethod
    def read(
        cls,
        reading: LayerReading,
        best: HighWater,
        *,
        patience: int | None,
        fires: int,
        rule: str,
    ) -> Self:
        return cls(
            patience=patience,
            fires=fires,
            stall_count=reading.stall_count,
            best_composite_fitness_at_entry=reading.best_composite_fitness_at_entry,
            best_composite_fitness_this_round=best.composite_fitness,
            best_theta_at_entry=best.theta(reading.theta_peak_round_at_entry),
            best_theta_this_round=best.theta(best.theta_round),
            comparator=reading.comparator,
            rule=rule,
        )


# Archived, never replayed: each folds the cycle's escalation history, which no replay holds.
class L2EscalationTrigger(CheckpointInputs, kind="l2_escalation_trigger"):
    reading: EscalationReading


class L3EscalationTrigger(CheckpointInputs, kind="l3_escalation_trigger"):
    reading: EscalationReading
    heal: bool
    l2_guard_breaches: list[str]


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

    # Recorded fired or not.
    replay = ctx.read(Replay)
    replay.decide(
        L2EscalationTrigger(
            reading=EscalationReading.read(
                ask.l2, best, patience=opt.l2_patience, fires=ctx.slice(Ladder).l2.fires, rule=cause
            )
        ),
        ask.next_action == NextAction.FIRE_L2,
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
            replay.decide(
                L3EscalationTrigger(
                    reading=EscalationReading.read(
                        held,
                        best,
                        patience=opt.l3_patience,
                        fires=held_l3.fires,
                        rule=L1_LAYOUT_REFUSED,
                    ),
                    heal=True,
                    l2_guard_breaches=[o.validator_id for o in result.l2_guard_breaches],
                ),
                True,
            )
            await _fire_layer(ctx, l3, Fire(L1_LAYOUT_REFUSED, None, best))
        return None
    if ask.next_action is NextAction.CONTINUE:
        return None

    replay.decide(
        L3EscalationTrigger(
            reading=EscalationReading.read(
                ask.l3,
                best,
                patience=opt.l3_patience,
                fires=ctx.slice(Ladder).l3.fires,
                rule=L2_PATIENCE_SPENT,
            ),
            heal=False,
            l2_guard_breaches=[],
        ),
        ask.next_action == NextAction.FIRE_L3,
    )

    if ask.next_action == NextAction.FIRE_L3:
        await _fire_layer(ctx, l3, Fire(L2_PATIENCE_SPENT, ask, best))
        return None

    return ask.stop_reason
