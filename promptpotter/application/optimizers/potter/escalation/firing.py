"""L2/L3 transition runner + the `escalate_l2` cascade + per-layer parse/apply.

Two tracks, decided by the round's evidence. A healthy analysable round leaves L1 critique to do
its job — critique is the prompt-improvement surface and stays concentrated, so it is NOT the
issue-router and carries no backend-fault diagnostics. Accumulated evidence of a systemic fault
instead routes to L2 as a weak preemptor, bypassing ``l1_patience`` so the loop stops grinding
dead rounds, and L2 judges recoverability.

``_parse_l2`` coerces and validates the layout and merges ``l1_overrides`` over the cycle's;
``_apply_l2`` installs both into ``PotterState.memory``. A control output fires after the layer's
normal output is adopted and the exit-phase event emitted.

What each layer may write, and why the framing is not among it:
``application/optimizers/potter/CLAUDE.md``."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Literal

from promptpotter.application.bench.llm_call import (
    LLMCallContext,
    run_optimizer_node,
)
from promptpotter.application.bench.resume_and_fork.decisions import (
    record_decision,
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
    coerce_l1_layout,
    unplaceable_edit,
    validate_l1_layout,
)
from promptpotter.application.optimizers.potter.dispatch.prompts import (
    load_optimizer_prompt,
)
from promptpotter.application.optimizers.potter.dispatch.schemas import (
    ForkProposal,
    L2ContextOutput,
    L3PlanOutput,
    TerminateProposal,
    build_l2_response_model,
)
from promptpotter.application.optimizers.potter.escalation.state import (
    LadderAsk,
    LayerReading,
    NextAction,
    PotterPhase,
    l1_stall_depth,
)
from promptpotter.application.optimizers.potter.knobs import potter_knobs
from promptpotter.application.optimizers.potter.records import (
    L1Layout,
    L2L3Memory,
    PotterCheckpointKind,
)
from promptpotter.application.optimizers.potter.validators.l3_output import run_l3_output_validators
from promptpotter.application.views.render.optimizer_prompt_text import fmt_pct
from promptpotter.application.views.render.primitives import fmt_fitness
from promptpotter.application.views.view_models import (
    OptimizerStepEnterView,
    OptimizerStepExitView,
)
from promptpotter.domain.phases import PhaseEvent, StopLoop, StopReason, emit_phase
from promptpotter.domain.pipeline_schema import ManifestNodeOverlay
from promptpotter.domain.results import merge_known_outcomes, order_floor
from promptpotter.domain.run_records import (
    ConfigOverrides,
    ForkTrigger,
    RebaseRequest,
)
from promptpotter.domain.validators import ValidatorOutcome
from promptpotter.infrastructure.llm.json_parse import OptimizerPromptParseError
from promptpotter.infrastructure.llm.telemetry import emit_round_warning
from promptpotter.infrastructure.tracing.bridge import observed_node
from promptpotter.shared import truncate

if TYPE_CHECKING:
    from pydantic import BaseModel

    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.application.optimizers.potter.dispatch.bundle import InjectionBundle
    from promptpotter.application.optimizers.potter.state import PotterState
    from promptpotter.domain.ruler import AbilityReading
    from promptpotter.infrastructure.tracing.bridge import ObservabilityBridge

logger = logging.getLogger(__name__)


# Provider/model/temperature are sourced from the layer's optimizer node config
# (``promptpotter/assets/optimizers/potter/pipeline.yaml``) inside ``llm_call``, never held here.


@dataclass
class TransitionResult:
    """One fire's output. Either layer may emit a ``fork_proposal``: the post-apply hook raises it as
    ``StopLoop(REBASED, fork=...)``, which ``runner.entry`` resolves to a fork."""

    # A fire mints no individual: it writes `PotterState.memory`, and the parent stays the parent.
    described: str
    # The whole post-fire map, or ``None`` when the fire left the cycle's untouched.
    l1_overrides: dict[str, Any] | None = None
    plan: str = ""
    l3_note: str = ""
    axis_targeted: str = ""
    l1_layout: L1Layout | None = None
    # L2 proposed a layout and it was REFUSED — the L3 force-trigger, and the reason the trigger
    # does not read ``l2_guard_breaches``: that stream is prompt evidence and carries SOFT reports.
    l1_layout_refused: bool = False
    l2_guard_breaches: list[ValidatorOutcome] = field(default_factory=list)
    l3_guard_breaches: list[ValidatorOutcome] = field(default_factory=list)
    fork_proposal: ForkProposal | None = None
    terminate_proposal: TerminateProposal | None = None


@dataclass(frozen=True)
class _HighWater:
    """The best the cycle's closed rounds reached — the peak the stall ladder differences against.
    The θ pair carries its SE, because a θ advance is only one relative to its own error."""

    composite_fitness: float | None
    theta: float | None
    theta_se: float | None


def _high_water(cycle: Cycle) -> _HighWater:
    """Derived off the round documents, never banked beside them. A round stamped on another δ scale
    than the cycle's (a round file written before the ruler warmed) is re-read on the cycle's."""
    # `max` keeps the first of equals, so a later round takes the peak only by beating it.
    best = max(cycle.rounds, key=lambda rr: order_floor(rr.composite_fitness))
    view = cycle.difficulty
    peak: AbilityReading | None = None
    frontier: list[dict[str, Any]] = []
    for rr in cycle.rounds:
        frontier = merge_known_outcomes(frontier, list(rr.results))
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


# What fires a layer where no `DEFAULT_ESCALATION_RULES` member did; each stands on the trigger
# record where a rule's name would.
FORCED_BY_DIAG = "diag"
L2_PATIENCE_SPENT = "l2_patience_spent"
L1_LAYOUT_REFUSED = "l1_layout_refused"


@dataclass(frozen=True)
class _Fire:
    """Why a layer fires, the ask whose reading its landing commits, and the peak that ask read."""

    cause: str
    # ``None`` is a heal: L3 answering a refused L2 edit, adopted without spending `l3_patience`.
    ask: LadderAsk | None
    best: _HighWater


ParseFn = Callable[[Any, L2L3Memory], TransitionResult]
ApplyFn = Callable[["PotterState", TransitionResult, _Fire], None]
EnterFn = Callable[["Cycle", "PotterState", _Fire], OptimizerStepEnterView]
ExitFn = Callable[["PotterState", TransitionResult], OptimizerStepExitView]


@dataclass(frozen=True)
class LayerStrategy:
    layer_id: Literal["L2", "L3"]
    template_name: str
    phase: PotterPhase
    # What a surface names the layer's phase by while it runs.
    activity: str
    response_model: Callable[[InjectionBundle], type[BaseModel]]
    parse: ParseFn
    apply: ApplyFn
    enter_view: EnterFn
    exit_view: ExitFn

    @property
    def declared(self) -> OptimizerPhase:
        return OptimizerPhase(phase=self.phase, node=self.template_name, activity=self.activity)


def _parse_l2(raw: L2ContextOutput, memory: L2L3Memory) -> TransitionResult:
    # An absent reason is REPORTED, never replaced. The placeholder that stood here read as a
    # sentence L2 had written, so the one surface carrying the fire forward said "refine_strategy
    # transition" whether L2 had diagnosed anything or not — and the empty state survived only as a
    # decimal in `review.md`'s l2_behavior_pass_rate, which is where it was eventually found.
    rationale = truncate(raw.rationale, 80) if raw.rationale else "(no rationale given)"
    overrides = {**memory.l1_overrides, **raw.l1_overrides} if raw.l1_overrides else None

    layout_outcomes: list[ValidatorOutcome] = []
    accepted_layout: L1Layout | None = None
    layout_refused = False
    if breach := unplaceable_edit(raw.l1_layout):
        layout_outcomes = [breach]
        layout_refused = True
    elif proposed_layout := coerce_l1_layout(raw.l1_layout, base=memory.l1_layout):
        layout_result = validate_l1_layout(
            proposed_layout,
            spec=NODE_LAYOUTS["l1_generate"],
            prior_layout=memory.l1_layout,
        )
        layout_outcomes = list(layout_result.outcomes)
        if layout_result.is_valid:
            accepted_layout = proposed_layout
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
        l1_overrides=overrides,
        axis_targeted=raw.axis_targeted,
        l1_layout=accepted_layout,
        l1_layout_refused=layout_refused,
        l2_guard_breaches=layout_outcomes,
        fork_proposal=raw.fork_proposal,
        terminate_proposal=raw.terminate_proposal,
    )


def _apply_l2(state: PotterState, result: TransitionResult, fire: _Fire) -> None:
    memory = state.memory
    if result.l1_overrides is not None:
        memory.l1_overrides = result.l1_overrides
    if result.l1_layout is not None:
        memory.l1_layout = result.l1_layout
    memory.wounds.l2_guard_breaches = list(result.l2_guard_breaches)
    assert fire.ask is not None, "only L3 heals"
    state.escalation.record_l2_fired(fire.ask.l2)


def _l2_enter(cycle: Cycle, state: PotterState, fire: _Fire) -> OptimizerStepEnterView:
    items = [(k, str(v)) for k, v in state.memory.l1_overrides.items()]
    if items:
        shown = [f"{k}={s if len(s) <= 30 else s[:27] + '...'}" for k, s in items[:5]]
        more = f", +{len(items) - 5} more" if len(items) > 5 else ""
        overrides = f"l1_overrides: {', '.join(shown)}{more}"
    else:
        overrides = "l1_overrides: (none)"
    esc = state.escalation
    return OptimizerStepEnterView(
        node=L2.template_name,
        activity=L2.activity,
        title="L2 REFINE CONTEXT",
        tag=f"L2 fire {esc.l2_round + 1}",
        lines=(
            f"rule={fire.cause}  |  "
            f"L1 stalled {l1_stall_depth(cycle.rounds)} rounds  |  "
            f"acc={fmt_pct(cycle.tracking.current_accuracy)}  "
            f"peak fitness={fmt_fitness(fire.best.composite_fitness)}",
            overrides,
            "LLM analyzing failure patterns...",
        ),
    )


def _l2_exit(state: PotterState, result: TransitionResult) -> OptimizerStepExitView:
    # The two L1 surfaces a fire can touch — the pair `l2_targets_l1_surface` scores it on.
    layout = ", l1_layout edited" if result.l1_layout is not None else ""
    axis = f", axis={result.axis_targeted}" if result.axis_targeted else ""
    esc = state.escalation
    return OptimizerStepExitView(
        headline=f"L2 decision: {len(state.memory.l1_overrides)} param changes{layout}{axis}",
        details=(result.described,),
        # No prompt/response: the call is this ledger's `l2_context` LLMCallRecord already.
        audit=("L2 call", L2.template_name),
        state={
            "l2_round": esc.l2_round,
            "l2_stall_count": esc.l2_stall_count,
            "l2_best_composite_fitness_at_entry": esc.l2_best_composite_fitness_at_entry,
            "l2_best_theta_at_entry": esc.l2_best_theta_at_entry,
            "memory": state.memory.fire_writes(),
        },
    )


L2 = LayerStrategy(
    layer_id="L2",
    template_name="l2_context",
    phase=PotterPhase.REFINE_STRATEGY,
    activity="refining strategy",
    response_model=lambda bundle: build_l2_response_model(bundle.silent_l1_panels),
    parse=_parse_l2,
    apply=_apply_l2,
    enter_view=_l2_enter,
    exit_view=_l2_exit,
)


def _parse_l3(raw: L3PlanOutput, memory: L2L3Memory) -> TransitionResult:
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


def _apply_l3(state: PotterState, result: TransitionResult, fire: _Fire) -> None:
    state.memory.plan = result.plan
    # The overwrite, possibly with ``""``, is the "cleared only when L3 fires again" contract.
    state.memory.wounds.l3_note = result.l3_note
    state.memory.wounds.l3_guard_breaches = list(result.l3_guard_breaches)
    state.escalation.record_l3_fired(None if fire.ask is None else fire.ask.l3)


def _l3_enter(cycle: Cycle, state: PotterState, fire: _Fire) -> OptimizerStepEnterView:
    esc = state.escalation
    return OptimizerStepEnterView(
        node=L3.template_name,
        activity=L3.activity,
        title="L3 MODIFY PLAN",
        tag=f"L3 fire {esc.l3_round + 1}",
        lines=(
            "Healing L2's refused l1_layout edit"
            if fire.ask is None
            else f"L2 stalled {fire.ask.l2.stall_count} rounds",
            f"Current plan: {truncate(state.memory.plan[:120], 55, '...')}",
            "LLM designing new strategy...",
        ),
    )


def _l3_exit(state: PotterState, result: TransitionResult) -> OptimizerStepExitView:
    # `record_l3_fired` clears L2's counters and its entry pair, so a resume folds both.
    esc = state.escalation
    return OptimizerStepExitView(
        headline=f"New plan: {truncate(result.plan[:120], 55, '...')}",
        details=(result.described,),
        audit=None,
        state={
            "l3_round": esc.l3_round,
            "l3_stall_count": esc.l3_stall_count,
            "l3_best_composite_fitness_at_entry": esc.l3_best_composite_fitness_at_entry,
            "l3_best_theta_at_entry": esc.l3_best_theta_at_entry,
            "memory": state.memory.fire_writes(),
        },
    )


L3 = LayerStrategy(
    layer_id="L3",
    template_name="l3_plan",
    phase=PotterPhase.MODIFY_PLAN,
    activity="replanning",
    response_model=lambda _bundle: L3PlanOutput,
    parse=_parse_l3,
    apply=_apply_l3,
    enter_view=_l3_enter,
    exit_view=_l3_exit,
)


async def _run_transition(
    transition: LayerStrategy,
    cycle: Cycle,
    state: PotterState,
    round_num: int,
    on_phase: Callable[[PhaseEvent], None] | None,
    fire: _Fire,
    *,
    obs: ObservabilityBridge | None,
    tracing_campaign_id: str,
) -> TransitionResult | None:
    """enter → LLM → parse → apply → exit; ``None`` when the layer's output never parsed.
    Layer-agnostic — everything layer-specific reads off the `LayerStrategy` spec."""
    emit_phase(
        on_phase,
        transition.phase,
        "enter",
        round=round_num,
        step=transition.enter_view(cycle, state, fire),
    )
    async with observed_node(
        f"{transition.template_name}_r{round_num}",
        "llm",
        obs=obs,
        campaign_id=tracing_campaign_id,
        round_num=round_num,
    ):
        # The prompt reports the verdict that asked for it, which lands only if this fire does.
        seen = (
            state
            if fire.ask is None
            else replace(state, escalation=state.escalation.as_read_by(fire.ask))
        )
        bundle = build_bundle(cycle, seen)
        filled = DispatchHub.fill(
            load_optimizer_prompt(transition.template_name), bundle, node=transition.template_name
        )
        try:
            raw, _, _ = await run_optimizer_node(
                template_name=transition.template_name,
                prompt_vars=filled.injection_vars,
                template=filled.template,
                response_model=transition.response_model(bundle),
                context=LLMCallContext(
                    ledger=cycle.session.state.ledger,
                    round_num=round_num,
                    cache=cycle.session.store.optimizer_reuse,
                    injections=filled.breakdown,
                ),
            )
            result = transition.parse(raw, state.memory)
        except OptimizerPromptParseError as parse_err:
            # A refinement that never parsed costs a REFINEMENT, not a MEASUREMENT. Unhandled
            # it kills the cycle — and under L4 that voids a whole outer sample, scoring one
            # flaky provider response as "this optimizer prompt is bad". Prior `l1_layout` /
            # `l1_overrides` / `plan` stay adopted, the round is a stall, the loop continues.
            logger.error(
                "%s: optimizer prompt parse failure — refinement discarded, prior framing kept. [%s]",
                transition.template_name,
                parse_err.diagnosis(),
            )
            emit_round_warning(
                kind="layer_parse_failure",
                severity="error",
                message=(
                    f"{transition.template_name} returned unusable output "
                    f"({'empty/truncated' if parse_err.is_empty else 'schema-noncompliant'}) "
                    "— the prior framing was kept and the loop continues."
                ),
                detail={
                    "node": transition.template_name,
                    "layer": transition.layer_id,
                    **parse_err.warning_detail(),
                },
            )
            # Closes the bracket and adopts nothing; the round warning above is its readout.
            emit_phase(
                on_phase,
                transition.phase,
                "exit",
                round=round_num,
                step=OptimizerStepExitView(headline="", details=(), audit=None, state=None),
            )
            return None

    transition.apply(state, result, fire)
    emit_phase(
        on_phase,
        transition.phase,
        "exit",
        round=round_num,
        step=transition.exit_view(state, result),
    )
    knobs = potter_knobs(cycle.optimizer)

    # Terminate outranks rebase: "stop" is more final than "try again from earlier". Both ride
    # this post-apply seam, so the layer's normal output is adopted and the exit-phase event is
    # emitted before the raise.
    if result.terminate_proposal is not None:
        reason = result.terminate_proposal.reason.strip()
        if not knobs.escalation.terminate_capability:
            # Same shape as the fork gate below: off ⇒ the prompt carried no terminate
            # guidance, and a volunteered field must not ABORT the run.
            logger.warning(
                "%s emitted terminate_proposal while terminate_capability is off — ignored",
                transition.layer_id,
            )
        elif not reason:
            # A stop with nothing to act on is not a decision. The field is OPTIONAL, so a model
            # that fills it with "" has volunteered it exactly as a capability-off model does —
            # and this branch is the one that was missing: presence alone ended a cycle whose
            # fitness was still climbing, then the cell was re-measured from scratch. Never
            # substitute a stand-in reason here; a stop nobody can act on must read as one.
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
            # A model can volunteer the field even though the prompt carried no fork guidance.
            # Without this the gate is prompt-side only, and a no-rebase ablation — whose
            # whole point is that it cannot fork — silently forks anyway.
            logger.warning(
                "%s emitted fork_proposal while rebase_capability is off — ignored",
                transition.layer_id,
            )
        elif fork := _rebase_request(cycle, transition.layer_id, result.fork_proposal, round_num):
            raise StopLoop(StopReason.REBASED, fork=fork)

    return result


def _rebase_request(
    cycle: Cycle, layer_id: str, proposal: ForkProposal, round_num: int
) -> RebaseRequest | None:
    """An L2/L3 ``fork_proposal`` as the fork it asks for. **The layer decides WHETHER to rewind;
    UCB decides WHERE** — :func:`select_rewind_round` over the backpropagated lineage."""
    session = cycle.session
    spine = load_lineage_spine(session.store, session.campaign_id)
    target_round = select_rewind_round(
        spine, cycle_id=session.state.cycle_id or "", current_round=round_num
    )
    if target_round is None:
        # Nothing above the current node (round 0 of a root). A rewind to nowhere mints a
        # duplicate of this cycle and burns a whole run, so decline and let the loop stop.
        logger.warning(
            "%s emitted fork_proposal at round %d but no ancestor is available to rewind to — ignored",
            layer_id,
            round_num,
        )
        return None

    unlock = bool(proposal.unlock_schema_field_rename) and not (
        potter_knobs(cycle.optimizer).l1_generate.schema_field_rename
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
    cycle: Cycle,
    round_num: int,
    patience: int | None,
    reading: LayerReading,
    best: _HighWater,
    *,
    kind: PotterCheckpointKind,
    layer: Literal["l2", "l3"],
    counter_round: int,
    node: str,
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
    record_decision(
        cycle.pending_decisions,
        kind,
        {
            "round_num": round_num,
            f"{layer}_patience": patience,
            "entry_round": counter_round if counter_round > 0 else -1,
        },
        fired,
        node=node,
        data={
            f"{layer}_round": counter_round,
            "stall_count": reading.stall_count,
            "best_composite_fitness_at_entry": reading.best_composite_fitness_at_entry,
            "best_composite_fitness_this_round": best.composite_fitness,
            "best_theta_at_entry": reading.best_theta_at_entry,
            "best_theta_this_round": best.theta,
            # Which of the two pairs above the stall verdict actually read.
            "comparator": reading.comparator,
            **asked,
        },
        round=round_num,
    )


async def escalate_l2(
    cycle: Cycle,
    state: PotterState,
    round_num: int,
    on_phase: Callable[[PhaseEvent], None] | None = None,
    obs: ObservabilityBridge | None = None,
    tracing_campaign_id: str = "",
    *,
    node: str,
    cause: str,
) -> StopReason | None:
    """``cause`` is what asked: the rule that matched the round, or the force no rule made."""
    opt = potter_knobs(cycle.optimizer).escalation
    esc = state.escalation
    best = _high_water(cycle)

    ask = esc.ask_l2_escalation(
        current_composite_fitness=best.composite_fitness,
        current_theta=best.theta,
        current_theta_se=best.theta_se,
        escalation_ladder=opt.escalation_ladder,
        l2_patience=opt.l2_patience,
        l3_patience=opt.l3_patience,
    )

    # Archival, fired or not: the verdict is a fold over the cycle, which no replayer holds.
    _record_trigger(
        cycle,
        round_num,
        opt.l2_patience,
        ask.l2,
        best,
        kind=PotterCheckpointKind.L2_ESCALATION_TRIGGER,
        layer="l2",
        counter_round=esc.l2_round,
        node=node,
        fired=ask.next_action == NextAction.FIRE_L2,
        rule=cause,
    )

    if ask.next_action == NextAction.FIRE_L2:
        result = await _run_transition(
            L2,
            cycle,
            state,
            round_num,
            on_phase,
            _Fire(cause, ask, best),
            obs=obs,
            tracing_campaign_id=tracing_campaign_id,
        )
        # Wound 4: L2's layout edit was REFUSED → L3 heals it. The trigger reads the refusal and
        # not `wounds.l2_guard_breaches`: that stream is prompt EVIDENCE and two of its members are
        # inert — `l1_layout_voids_prefix` is a cache-cost report and
        # `l1_layout_unchanged_from_prior` a no-op; neither may replan the cycle.
        if opt.escalation_ladder.fires_l3 and result is not None and result.l1_layout_refused:
            logger.warning(
                "L3 force-triggered — L2's l1_layout edit was refused at round %d", round_num
            )
            # A heal reads no gate, so its record banks the counters the FSM holds.
            held = LayerReading(
                esc.l3_stall_count,
                esc.l3_best_composite_fitness_at_entry,
                esc.l3_best_theta_at_entry,
                None,
            )
            _record_trigger(
                cycle,
                round_num,
                opt.l3_patience,
                held,
                best,
                kind=PotterCheckpointKind.L3_ESCALATION_TRIGGER,
                layer="l3",
                counter_round=esc.l3_round,
                node=node,
                fired=True,
                rule=L1_LAYOUT_REFUSED,
                heal=True,
                l2_guard_breaches=[o.validator_id for o in result.l2_guard_breaches],
            )
            await _run_transition(
                L3,
                cycle,
                state,
                round_num,
                on_phase,
                _Fire(L1_LAYOUT_REFUSED, None, best),
                obs=obs,
                tracing_campaign_id=tracing_campaign_id,
            )
        return None

    # FIRE_L3 or STOP_L3_PATIENCE — record L3 trigger decision either way.
    _record_trigger(
        cycle,
        round_num,
        opt.l3_patience,
        ask.l3,
        best,
        kind=PotterCheckpointKind.L3_ESCALATION_TRIGGER,
        layer="l3",
        counter_round=esc.l3_round,
        node=node,
        fired=ask.next_action == NextAction.FIRE_L3,
        rule=L2_PATIENCE_SPENT,
        heal=False,
        l2_guard_breaches=[],
    )

    if ask.next_action == NextAction.FIRE_L3:
        await _run_transition(
            L3,
            cycle,
            state,
            round_num,
            on_phase,
            _Fire(L2_PATIENCE_SPENT, ask, best),
            obs=obs,
            tracing_campaign_id=tracing_campaign_id,
        )
        return None

    # STOP_L3_PATIENCE — the reason rides the event (``_NEXT_ACTION_TO_STOP``), the one
    # NextAction→StopReason table; re-spelling it here is how the two drift apart.
    return ask.stop_reason


__all__ = [
    "FORCED_BY_DIAG",
    "L2",
    "L3",
    "escalate_l2",
]
