"""A predicate is False when its signal is unavailable, so an early cycle falls through, never fires blind."""

from __future__ import annotations

import enum
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal, NamedTuple, Protocol, Self

from pydantic import BaseModel

from promptpotter.application.bench.node_context import (
    Calls,
    Lineage,
    NodeContext,
    Progress,
    Replay,
)
from promptpotter.application.mask.backprop import ForkDecline
from promptpotter.application.optimizers.nodes import OptimizerPhase
from promptpotter.application.optimizers.potter.dispatch.facade import (
    DispatchHub,
    PotterView,
)
from promptpotter.application.optimizers.potter.dispatch.schemas import (
    ForkProposal,
    StandingForkProposal,
    TerminateProposal,
)
from promptpotter.application.optimizers.potter.knobs import (
    EscalationKnobs,
    EscalationLadder,
    L1GenerateKnobs,
)
from promptpotter.application.optimizers.potter.records import (
    L1Layout,
    Ladder,
    LayerCounters,
    Recombination,
)
from promptpotter.application.run_stop import StopLoop
from promptpotter.domain.layer_fold import UNBOUNDED, Unbounded
from promptpotter.domain.origin_panel import StallEffect
from promptpotter.domain.paired_reading import RoundArm
from promptpotter.domain.phase_acts import StepClosed, StepOpened, TextFact
from promptpotter.domain.phases import StopReason
from promptpotter.domain.pipeline_schema import ManifestNodeOverlay
from promptpotter.domain.records.run_records import (
    ConfigOverrides,
    DecisionInputs,
    ForkTrigger,
    RebaseRequest,
    RoundStatement,
    RoundWarningRecord,
)
from promptpotter.domain.results import Reelection, RoundResult
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.domain.validators import ValidatorOutcome
from promptpotter.infrastructure.llm.json_parse import OptimizerPromptParseError
from promptpotter.infrastructure.llm.telemetry import append_record, current_round
from promptpotter.shared.identity import Principal


class NextAction(enum.StrEnum):
    CONTINUE = "continue"
    FIRE_L2 = "fire_l2"
    FIRE_L3 = "fire_l3"
    STOP_L3_PATIENCE = "stop_l3_patience"


NEXT_ACTION_STOP: dict[NextAction, StopReason] = {
    NextAction.STOP_L3_PATIENCE: StopReason.OPTIMIZER_EXHAUSTED,
}


@dataclass(frozen=True)
class EscalationEvent:
    next_action: NextAction
    # ``None`` where the FSM decided alone.
    rule: str | None = None

    @property
    def stop_reason(self) -> StopReason | None:
        return NEXT_ACTION_STOP.get(self.next_action)


@dataclass(frozen=True)
class EscalationInputs:
    l1_stall_count: int
    l1_patience: int
    escalation_ladder: EscalationLadder
    l1_mandatory_breach: bool = False
    l1_zero_candidates: bool = False
    # NEVER stops the loop here: the stop authority stays with the LLM tier.
    evidence_starved: bool = False


@dataclass(frozen=True)
class LadderInputs:
    escalation_ladder: EscalationLadder
    l2_stall_count: int
    l2_patience: int
    l3_stall_count: int
    l3_patience: int | Unbounded
    l1_layout_refused: bool = False


L2_PATIENCE_SPENT = "l2_patience_spent"
L1_LAYOUT_REFUSED = "l1_layout_refused"

# Every name a decision below stamps on its `EscalationEvent`, ten in all.
ESCALATION_RULES: tuple[str, ...] = (
    "l1_only_ladder",
    "l1_generate_unusable",
    "l1_evidence_starved",
    "l1_continue",
    "l1_to_l2",
    "l2_within_patience",
    L2_PATIENCE_SPENT,
    "l3_patience_spent",
    L1_LAYOUT_REFUSED,
    "l2_landed",
)


def decide_escalation(s: EscalationInputs) -> EscalationEvent:
    """Every rule above `l1_continue` preempts patience; `l1_only_ladder` preempts every FIRE_L2 below it."""
    if not s.escalation_ladder.fires_l2:
        return EscalationEvent(NextAction.CONTINUE, "l1_only_ladder")
    if s.l1_mandatory_breach or s.l1_zero_candidates:
        return EscalationEvent(NextAction.FIRE_L2, "l1_generate_unusable")
    if s.evidence_starved:
        return EscalationEvent(NextAction.FIRE_L2, "l1_evidence_starved")
    if s.l1_stall_count < s.l1_patience:
        return EscalationEvent(NextAction.CONTINUE, "l1_continue")
    return EscalationEvent(NextAction.FIRE_L2, "l1_to_l2")


def decide_climb(s: LadderInputs) -> EscalationEvent:
    if not s.escalation_ladder.fires_l3 or s.l2_stall_count < s.l2_patience:
        return EscalationEvent(NextAction.FIRE_L2, "l2_within_patience")
    if s.l3_patience == UNBOUNDED or s.l3_stall_count < s.l3_patience:
        return EscalationEvent(NextAction.FIRE_L3, L2_PATIENCE_SPENT)
    return EscalationEvent(NextAction.STOP_L3_PATIENCE, "l3_patience_spent")


def decide_heal(s: LadderInputs) -> EscalationEvent:
    if s.escalation_ladder.fires_l3 and s.l1_layout_refused:
        return EscalationEvent(NextAction.FIRE_L3, L1_LAYOUT_REFUSED)
    return EscalationEvent(NextAction.CONTINUE, "l2_landed")


class PotterPhase(enum.StrEnum):
    REFINE_STRATEGY = "refine_strategy"
    MODIFY_PLAN = "modify_plan"


@dataclass(frozen=True)
class LayerReading:
    stall_count: int
    # ``None`` only on a heal's record, which reads a layer that was never seeded.
    entered_round: int | None

    def onto(self, held: LayerCounters, *, fired: bool = False) -> LayerCounters:
        return LayerCounters(
            fires=held.fires + fired,
            stall_count=self.stall_count,
            entered_round=self.entered_round,
        )


@dataclass(frozen=True, kw_only=True)
class LadderAsk:
    """L3's gate is read on every ask and binds only past L2's patience."""

    next_action: NextAction
    l2: LayerReading
    l3: LayerReading

    @property
    def stop_reason(self) -> StopReason | None:
        return NEXT_ACTION_STOP.get(self.next_action)


def bank_round(ladder: Ladder, stall: StallEffect) -> Ladder:
    match stall:
        case StallEffect.RESETS:
            return ladder.model_copy(update={"l1_stall_count": 0})
        case StallEffect.COUNTS:
            return ladder.model_copy(update={"l1_stall_count": ladder.l1_stall_count + 1})
        case StallEffect.STARTS | StallEffect.SKIPS:
            return ladder


def _read_layer(layer: LayerCounters, rounds: Sequence[RoundResult]) -> LayerReading:
    """The stall stream L1 patience and the lives read: a layer's first read only seeds it, a RESETS round past its entry resets it, any other ask stalls it."""
    now = max(rr.round for rr in rounds)
    entered = layer.entered_round
    if entered is None:
        return LayerReading(layer.stall_count, now)
    if any(rr.round > entered and rr.overlap.stall is StallEffect.RESETS for rr in rounds):
        return LayerReading(0, now)
    return LayerReading(layer.stall_count + 1, entered)


def ask_l2_escalation(
    ladder: Ladder,
    *,
    rounds: Sequence[RoundResult],
    escalation_ladder: EscalationLadder,
    l2_patience: int,
    l3_patience: int | Unbounded,
) -> LadderAsk:
    """A fire that never parsed leaves the counters where the ledger has them."""
    l2 = _read_layer(ladder.l2, rounds)
    l3 = _read_layer(ladder.l3, rounds)
    climb = decide_climb(
        LadderInputs(
            escalation_ladder=escalation_ladder,
            l2_stall_count=l2.stall_count,
            l2_patience=l2_patience,
            l3_stall_count=l3.stall_count,
            l3_patience=l3_patience,
        )
    )
    return LadderAsk(next_action=climb.next_action, l2=l2, l3=l3)


def as_read_by(ladder: Ladder, ask: LadderAsk) -> Ladder:
    read = {"l2": ask.l2.onto(ladder.l2)}
    if ask.next_action is not NextAction.FIRE_L2:
        read["l3"] = ask.l3.onto(ladder.l3)
    return ladder.model_copy(update=read)


def bank_and_route(
    ladder: Ladder,
    *,
    stall: StallEffect,
    l1_patience: int,
    escalation_ladder: EscalationLadder,
    l1_mandatory_breach: bool = False,
    l1_zero_candidates: bool = False,
    evidence_starved: bool = False,
) -> tuple[Ladder, EscalationEvent]:
    banked = bank_round(ladder, stall)
    inputs = EscalationInputs(
        l1_stall_count=banked.l1_stall_count,
        l1_patience=l1_patience,
        escalation_ladder=escalation_ladder,
        l1_mandatory_breach=l1_mandatory_breach,
        l1_zero_candidates=l1_zero_candidates,
        evidence_starved=evidence_starved,
    )
    return banked, decide_escalation(inputs)


def l2_fired(ladder: Ladder, reading: LayerReading) -> Ladder:
    return ladder.model_copy(
        update={"l1_stall_count": 0, "l2": reading.onto(ladder.l2, fired=True)}
    )


def l3_fired(ladder: Ladder, reading: LayerReading | None) -> Ladder:
    """``None`` is a heal: it answers a refused L2 edit, not a stall, and leaves L3's ratchet alone."""
    l3 = (
        ladder.l3.model_copy(update={"fires": ladder.l3.fires + 1})
        if reading is None
        else reading.onto(ladder.l3, fired=True)
    )
    return Ladder(l3=l3)


@dataclass
class TransitionResult:
    # The layer's own, whole; empty where it gave none.
    rationale: str
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


@dataclass(frozen=True)
class Fire:
    cause: str
    # ``None`` is a heal (`escalation.py::l3_fired`).
    ask: LadderAsk | None


class EscalationLayer(Protocol):
    """Fired by the `escalation` controller, under its own node context: it writes its own memory, never the ladder; its patience is the controller's knob."""

    name: ClassVar[str]
    layer_id: ClassVar[Literal["L2", "L3"]]
    phase: ClassVar[OptimizerPhase]

    def reply_model(self, view: PotterView) -> type[BaseModel]: ...

    def parse(self, ctx: NodeContext, raw: Any) -> TransitionResult: ...

    def land(self, ctx: NodeContext, result: TransitionResult) -> None:
        """Puts its memory and restates it onto the closed round, since a resume or fork starts from there."""

    def record(self, ladder: Ladder, fire: Fire) -> Ladder: ...

    def enter_act(self, ctx: NodeContext, fire: Fire) -> StepOpened: ...

    def exit_act(self, ctx: NodeContext, result: TransitionResult) -> StepClosed: ...


class LayerCall(NamedTuple):
    """A layer the controller fires, with the context it runs under (`NodeContext.fire`)."""

    layer: EscalationLayer
    ctx: NodeContext


async def _run_transition(
    fired: LayerCall, fire: Fire
) -> tuple[TransitionResult, RebaseRequest | ForkDecline | None] | None:
    """With the fork its proposal asks, or why none may; ``None`` when the output never parsed."""
    layer, ctx = fired
    ctx.read(Progress).phase(layer.phase.phase, "enter", layer.enter_act(ctx, fire))
    ladder = ctx.slice(Ladder)
    # The prompt reports the verdict that asked for it, which lands only if this fire does.
    view = PotterView(ctx, ladder if fire.ask is None else as_read_by(ladder, fire.ask))
    calls = ctx.read(Calls)
    filled = DispatchHub.fill(calls.prompt(), view)
    try:
        result = layer.parse(ctx, await calls.answer(layer.reply_model(view), filled))
    except OptimizerPromptParseError as parse_err:
        # Unhandled it kills the cycle, and under L4 that voids a whole outer sample.
        append_record(
            RoundWarningRecord(
                kind="layer_parse_failure",
                severity="error",
                message=(
                    f"{layer.name} returned unusable output "
                    f"({'empty/truncated' if parse_err.is_empty else 'schema-noncompliant'}) "
                    "— the prior framing was kept and the loop continues."
                ),
                round=current_round(),
                detail={
                    "node": layer.name,
                    "layer": layer.layer_id,
                    **parse_err.warning_detail(),
                },
            )
        )
        ctx.read(Progress).phase(layer.phase.phase, "exit", StepClosed(facts={}, audit=None))
        return None

    layer.land(ctx, result)
    proposal = result.fork_proposal
    fork = (
        _rebase_request(ctx, layer.layer_id, proposal, view.reelect_offer)
        if proposal is not None
        else None
    )
    closed = layer.exit_act(ctx, result)
    if isinstance(fork, ForkDecline):
        assert proposal is not None
        # The step's own record names the proposal and why no node took it; the readout prints it.
        declined = TextFact(value=f"{proposal.reason} — {fork.value}")
        closed = StepClosed(facts={**closed.facts, "fork declined": declined}, audit=closed.audit)
    ctx.read(Progress).phase(layer.phase.phase, "exit", closed)
    return result, fork


def _layer_control(
    fired: LayerCall, result: TransitionResult, fork: RebaseRequest | ForkDecline | None
) -> None:
    """The layer-control channel, read after the ladder banked the fire: terminate outranks rebase."""
    # Present only where the reply schema offered it (`PotterView.terminate_offer`); never a
    # stand-in reason: a blank stop is not a decision.
    if result.terminate_proposal is not None and (
        reason := result.terminate_proposal.reason.strip()
    ):
        raise StopLoop(StopReason.OPTIMIZER_ABORT, detail=f"{fired.layer.layer_id}: {reason}")
    if isinstance(fork, RebaseRequest):
        # The runner words its stop once the fork is cut (`fork_siblings.py::ForkCut.said`).
        raise StopLoop(StopReason.REBASED, fork=fork)


def _rebase_request(
    ctx: NodeContext, layer_id: str, proposal: ForkProposal, offer: Mapping[str, RoundArm]
) -> RebaseRequest | ForkDecline:
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
        branch: int | ForkDecline = at.round
        held = next(rr for rr in lineage.rounds if rr.round == at.round)
        assert held.election is not None, "ELECTIONS lists only rounds that elected"
        if held.election.selected != at.arm:
            hangs_from = lineage.reelected(at, Reelection(by=issued_by, reason=reason))
    else:
        branch = lineage.branch_round(changing=unlock)
    if isinstance(branch, ForkDecline):
        return branch

    return RebaseRequest(
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


async def _fire_layer(ctx: NodeContext, fired: LayerCall, fire: Fire) -> TransitionResult | None:
    """The ladder is the controller's: it banks the fire once the layer landed, before the layer's control channel."""
    landed = await _run_transition(fired, fire)
    if landed is None:
        return None
    result, fork = landed
    ctx.put(fired.layer.record(ctx.slice(Ladder), fire))
    ctx.restate(Ladder)
    _layer_control(fired, result, fork)
    return result


class EscalationReading(FrozenModel):
    patience: int | Unbounded
    fires: int
    stall_count: int
    entered_round: int | None
    rule: str

    @classmethod
    def read(
        cls, reading: LayerReading, *, patience: int | Unbounded, fires: int, rule: str
    ) -> Self:
        return cls(
            patience=patience,
            fires=fires,
            stall_count=reading.stall_count,
            entered_round=reading.entered_round,
            rule=rule,
        )


# Archived, never replayed: each folds the cycle's escalation history, which no replay holds.
class L2EscalationTrigger(DecisionInputs, kind="l2_escalation_trigger"):
    reading: EscalationReading


class L3EscalationTrigger(DecisionInputs, kind="l3_escalation_trigger"):
    reading: EscalationReading
    heal: bool
    l2_guard_breaches: list[str]


async def escalate_l2(
    ctx: NodeContext, *, cause: str, l2: LayerCall, l3: LayerCall
) -> StopReason | None:
    opt = ctx.knobs(EscalationKnobs)

    ask = ask_l2_escalation(
        ctx.slice(Ladder),
        rounds=ctx.read(Lineage).rounds,
        escalation_ladder=opt.escalation_ladder,
        l2_patience=opt.l2_patience,
        l3_patience=opt.l3_patience,
    )

    # Recorded fired or not.
    replay = ctx.read(Replay)
    replay.decide(
        L2EscalationTrigger(
            reading=EscalationReading.read(
                ask.l2, patience=opt.l2_patience, fires=ctx.slice(Ladder).l2.fires, rule=cause
            )
        ),
        ask.next_action == NextAction.FIRE_L2,
    )

    if ask.next_action == NextAction.FIRE_L2:
        result = await _fire_layer(ctx, l2, Fire(cause, ask))
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
            # A heal reads no gate, so its record banks the counters the ladder holds.
            held_l3 = ctx.slice(Ladder).l3
            held = LayerReading(held_l3.stall_count, held_l3.entered_round)
            replay.decide(
                L3EscalationTrigger(
                    reading=EscalationReading.read(
                        held,
                        patience=opt.l3_patience,
                        fires=held_l3.fires,
                        rule=L1_LAYOUT_REFUSED,
                    ),
                    heal=True,
                    l2_guard_breaches=[o.validator_id for o in result.l2_guard_breaches],
                ),
                True,
            )
            await _fire_layer(ctx, l3, Fire(L1_LAYOUT_REFUSED, None))
        return None
    if ask.next_action is NextAction.CONTINUE:
        return None

    replay.decide(
        L3EscalationTrigger(
            reading=EscalationReading.read(
                ask.l3,
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
        await _fire_layer(ctx, l3, Fire(L2_PATIENCE_SPENT, ask))
        return None

    return ask.stop_reason
