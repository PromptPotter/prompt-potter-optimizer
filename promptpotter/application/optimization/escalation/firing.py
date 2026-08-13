"""L2/L3 transition runner + the `escalate_l2` cascade + per-layer parse/apply. What each layer may
write, and why the framing is not among it: ``application/optimization/CLAUDE.md``."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from promptpotter.application.mask.backprop import select_rewind_round
from promptpotter.application.mask.load import load_lineage_spine
from promptpotter.application.optimization.dispatch.facade import DispatchHub, build_bundle
from promptpotter.application.optimization.dispatch.llm_call.call import (
    LLMCallContext,
    run_optimizer_node,
)
from promptpotter.application.optimization.dispatch.llm_call.prompts import (
    load_optimizer_prompt,
    resolve_node_layout,
)
from promptpotter.application.optimization.dispatch.schemas import (
    ForkProposal,
    L2ContextOutput,
    L3PlanOutput,
    TerminateProposal,
)
from promptpotter.application.optimization.escalation.state import NextAction
from promptpotter.application.optimization.resume_and_fork.decisions import (
    ResumeCheckpointKind,
    record_decision,
)
from promptpotter.application.optimization.validators.l3_output import run_l3_output_validators
from promptpotter.domain.l1_layout import (
    NODE_LAYOUTS,
    L1Layout,
    coerce_l1_layout,
    validate_l1_layout,
)
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.phases import CampaignPhase, PhaseEvent, StopLoop, StopReason, emit_phase
from promptpotter.domain.run_records import (
    ConfigOverrides,
    ForkSpec,
    ForkTrigger,
    RebaseRequest,
)
from promptpotter.domain.validators import ValidatorOutcome
from promptpotter.infrastructure.llm.json_parse import OptimizerPromptParseError
from promptpotter.infrastructure.llm.telemetry import emit_round_warning
from promptpotter.infrastructure.tracing.bridge import observed_node
from promptpotter.infrastructure.tracing.events import LayerApplied
from promptpotter.shared import truncate
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.infrastructure.tracing.bridge import ObservabilityBridge

logger = logging.getLogger(__name__)


# Provider/model/temperature are sourced from the layer's optimizer node config
# (``promptpotter/assets/optimizer/pipeline.yaml``) inside ``llm_call``, never held here.


@dataclass
class TransitionResult:
    """One fire's output. Either layer may emit a ``fork_proposal``: the post-apply hook stashes it on
    ``cycle.rebase_request`` and raises ``StopLoop(REBASED)``, which ``runner.entry`` resolves to a fork."""

    opt_sp: OptSearchPoint
    l3_note: str = ""
    axis_targeted: str = ""
    l1_layout: L1Layout | None = None
    l2_guard_breaches: list[ValidatorOutcome] = field(default_factory=list)
    l3_guard_breaches: list[ValidatorOutcome] = field(default_factory=list)
    fork_proposal: ForkProposal | None = None
    terminate_proposal: TerminateProposal | None = None
    debug_prompt: str = ""
    debug_response: dict[str, Any] | None = None


ParseFn = Callable[[Any, OptSearchPoint, str], TransitionResult]
ApplyFn = Callable[["Cycle", TransitionResult, int], None]
PayloadFn = Callable[["Cycle"], dict[str, Any]]
ExitFn = Callable[["Cycle", TransitionResult], dict[str, Any]]


@dataclass(frozen=True)
class LayerStrategy:
    layer_id: Literal["L2", "L3"]
    template_name: str
    phase: CampaignPhase
    parse: ParseFn
    apply: ApplyFn
    enter_payload_fn: PayloadFn
    exit_payload_fn: ExitFn


def _parse_l2(raw: L2ContextOutput, opt_sp: OptSearchPoint, prompt: str) -> TransitionResult:
    # An absent reason is REPORTED, never replaced. The placeholder that stood here read as a
    # sentence L2 had written, so the one surface carrying the fire forward said "refine_strategy
    # transition" whether L2 had diagnosed anything or not — and the empty state survived only as a
    # decimal in `review.md`'s l2_behavior_pass_rate, which is where it was eventually found.
    rationale = truncate(raw.rationale, 80) if raw.rationale else "(no rationale given)"
    changes: dict[str, Any] = {"changes_description": f"L2: {rationale}"}
    if raw.l1_overrides:
        changes["l1_overrides"] = {**opt_sp.memory.l1_overrides, **raw.l1_overrides}

    proposed_layout = coerce_l1_layout(raw.l1_layout, base=opt_sp.memory.l1_layout)
    layout_outcomes: list[ValidatorOutcome] = []
    accepted_layout: L1Layout | None = None
    if proposed_layout is not None:
        layout_result = validate_l1_layout(
            proposed_layout,
            spec=NODE_LAYOUTS["l1_generate"],
            prior_layout=opt_sp.memory.l1_layout,
        )
        layout_outcomes = list(layout_result.outcomes)
        if layout_result.is_valid:
            accepted_layout = proposed_layout
    elif raw.l1_layout:
        # `{}` is "no layout edit"; a non-empty dict that coerces to nothing is L2 asking for one
        # in a shape no slot can hold. Both reach here as None, and treating them alike is what let
        # a signals-as-keys layout cost a whole fire in silence — no breach, so nothing reached the
        # `guard_breaches` panel and L2's next fire read no evidence that its shape was the problem.
        layout_outcomes = [
            ValidatorOutcome(
                validator_id="l1_layout_unparseable",
                evidence={"keys": sorted(raw.l1_layout)},
            )
        ]

    failures = list(layout_outcomes)
    if failures:
        failed_ids = ", ".join(o.validator_id for o in failures)
        logger.warning(
            "L2 output failed %d validator(s): %s",
            len(failures),
            failed_ids,
        )

    return TransitionResult(
        opt_sp=opt_sp.mutate(source="l2_context", **changes),
        axis_targeted=raw.axis_targeted,
        l1_layout=accepted_layout,
        l2_guard_breaches=failures,
        fork_proposal=raw.fork_proposal,
        terminate_proposal=raw.terminate_proposal,
        debug_prompt=prompt,
        debug_response=raw.model_dump(),
    )


def _apply_l2(cycle: Cycle, result: TransitionResult, round_num: int) -> None:
    opt_sp = cycle.opt_sp
    if result.l1_layout is not None:
        opt_sp.memory.l1_layout = result.l1_layout
    opt_sp.memory.wounds.l2_guard_breaches = list(result.l2_guard_breaches)
    cycle.escalation.record_l2_fired(
        best_composite_fitness=cycle.tracking.best_composite_fitness,
        best_theta=cycle.tracking.best_theta,
    )


def _l2_enter(cycle: Cycle) -> dict[str, Any]:
    return {
        "l2_round": cycle.escalation.l2_round,
        "l1_stall_count": cycle.escalation.l1_stall_count,
        "l1_overrides": cycle.opt_sp.memory.l1_overrides,
        "current_accuracy": cycle.tracking.current_accuracy,
        "best_accuracy": cycle.tracking.best_accuracy,
    }


def _l2_exit(cycle: Cycle, result: TransitionResult) -> dict[str, Any]:
    # ``l2_*_at_entry`` are read by ``EscalationFSM.fold`` on resume, carried onto
    # ``L2RefineExitView`` — the persisted half. No prompt/response: the call is already this
    # ledger's `l2_context` LLMCallRecord and the audit twin's `nodes.l2_context`.
    payload: dict[str, Any] = {
        "l2_round": cycle.escalation.l2_round,
        "l2_stall_count": cycle.escalation.l2_stall_count,
        "l2_best_composite_fitness_at_entry": cycle.escalation.l2_best_composite_fitness_at_entry,
        "l2_best_theta_at_entry": cycle.escalation.l2_best_theta_at_entry,
        "param_changes_count": len(result.opt_sp.memory.l1_overrides),
        "l1_layout_changed": result.l1_layout is not None,
        "changes_description": result.opt_sp.lineage.changes_description or "",
        "axis_targeted": result.axis_targeted,
    }
    if result.fork_proposal is not None:
        payload["fork_proposal"] = result.fork_proposal.model_dump()
    if result.terminate_proposal is not None:
        payload["terminate_proposal"] = result.terminate_proposal.model_dump()
    return payload


L2 = LayerStrategy(
    layer_id="L2",
    template_name="l2_context",
    phase=CampaignPhase.REFINE_STRATEGY,
    parse=_parse_l2,
    apply=_apply_l2,
    enter_payload_fn=_l2_enter,
    exit_payload_fn=_l2_exit,
)


def _parse_l3(raw: L3PlanOutput, opt_sp: OptSearchPoint, prompt: str) -> TransitionResult:
    new_plan = raw.plan or opt_sp.plan
    rationale = truncate(raw.rationale, 80) if raw.rationale else "(no rationale given)"
    failures = run_l3_output_validators({"plan": new_plan}, opt_sp)
    if failures:
        logger.warning(
            "L3 output failed %d validator(s): %s",
            len(failures),
            ", ".join(o.validator_id for o in failures),
        )
    return TransitionResult(
        opt_sp=opt_sp.mutate(
            plan=new_plan, changes_description=f"L3: {rationale}", source="l3_plan"
        ),
        l3_note=raw.note,
        l3_guard_breaches=failures,
        fork_proposal=raw.fork_proposal,
        terminate_proposal=raw.terminate_proposal,
        debug_prompt=prompt,
        debug_response=raw.model_dump(),
    )


def _apply_l3(cycle: Cycle, result: TransitionResult, round_num: int) -> None:
    # Order matters: ``copy_memory_to`` carried the prior l3_note onto the new OSP, and this
    # overwrite (possibly with ``""``) is the "cleared only when L3 fires again" contract.
    cycle.opt_sp.memory.wounds.l3_note = result.l3_note
    cycle.opt_sp.memory.wounds.l3_guard_breaches = list(result.l3_guard_breaches)
    cycle.escalation.record_l3_fired(
        best_composite_fitness=cycle.tracking.best_composite_fitness,
        best_theta=cycle.tracking.best_theta,
    )


def _l3_enter(cycle: Cycle) -> dict[str, Any]:
    return {
        "l3_round": cycle.escalation.l3_round,
        "l2_stall_count": cycle.escalation.l2_stall_count,
        "current_plan_preview": str(cycle.opt_sp.plan)[:120],
    }


def _l3_exit(cycle: Cycle, result: TransitionResult) -> dict[str, Any]:
    # ``l3_*_at_entry`` are read by ``EscalationFSM.fold`` on resume, carried onto
    # ``PlanExitView``; ``record_l3_fired`` resets L2 state to these.
    payload: dict[str, Any] = {
        "l3_round": cycle.escalation.l3_round,
        "l3_stall_count": cycle.escalation.l3_stall_count,
        "l3_best_composite_fitness_at_entry": cycle.escalation.l3_best_composite_fitness_at_entry,
        "l3_best_theta_at_entry": cycle.escalation.l3_best_theta_at_entry,
        "new_plan_preview": str(result.opt_sp.plan)[:120],
        "changes_description": result.opt_sp.lineage.changes_description or "",
    }
    if result.fork_proposal is not None:
        payload["fork_proposal"] = result.fork_proposal.model_dump()
    if result.terminate_proposal is not None:
        payload["terminate_proposal"] = result.terminate_proposal.model_dump()
    return payload


L3 = LayerStrategy(
    layer_id="L3",
    template_name="l3_plan",
    phase=CampaignPhase.MODIFY_PLAN,
    parse=_parse_l3,
    apply=_apply_l3,
    enter_payload_fn=_l3_enter,
    exit_payload_fn=_l3_exit,
)


def apply_fork_payload_to_opt_sp(opt_sp: OptSearchPoint, payload: ForkSpec) -> None:
    """Stamp a fork payload's L1-surface deltas on the OSP — the same shape L2 writes."""
    if payload.l1_layout is not None:
        layout = coerce_l1_layout(payload.l1_layout, base=opt_sp.memory.l1_layout)
        if layout is None:
            raise ValueError(
                f"Fork payload l1_layout is unparseable: {payload.l1_layout!r}. "
                "Expect a dict whose keys are L1 layout slots and values are lists of "
                "placeholder names."
            )
        result = validate_l1_layout(
            layout, spec=NODE_LAYOUTS["l1_generate"], prior_layout=opt_sp.memory.l1_layout
        )
        if not result.is_valid:
            ids = sorted({o.validator_id for o in result.outcomes})
            raise ValueError(f"Fork payload l1_layout failed hard validators: {ids}")
        opt_sp.memory.l1_layout = layout


async def _run_transition(
    transition: LayerStrategy,
    cycle: Cycle,
    config: CampaignConfig,
    pipeline_schema: PipelineSchema,
    round_num: int,
    on_phase: Callable[[PhaseEvent], None] | None,
    *,
    obs: ObservabilityBridge | None,
    tracing_campaign_id: str,
) -> None:
    """enter → LLM → parse → adopt → LayerApplied → side-effects → exit.
    Layer-agnostic — everything layer-specific reads off the `LayerStrategy` spec."""
    assert cycle.tracking.current_sp is not None
    current_pp = cycle.tracking.current_sp.pipeline_params

    emit_phase(
        on_phase, transition.phase, "enter", round=round_num, **transition.enter_payload_fn(cycle)
    )
    async with observed_node(
        f"{transition.template_name}_r{round_num}",
        "llm/optimizer",
        obs=obs,
        campaign_id=tracing_campaign_id,
        round_num=round_num,
    ):
        template, prompt_vars, _ = DispatchHub.fill(
            load_optimizer_prompt(transition.template_name),
            resolve_node_layout(transition.template_name),
            build_bundle(cycle),
        )
        try:
            raw, prompt, _ = await run_optimizer_node(
                template_name=transition.template_name,
                prompt_vars=prompt_vars,
                template=template,
                context=LLMCallContext(
                    ledger=cycle.session.state.ledger,
                    round_num=round_num,
                    cache=cycle.session.store.optimizer_calls,
                ),
            )
            result = transition.parse(raw, cycle.opt_sp, prompt)
        except OptimizerPromptParseError as parse_err:
            # A refinement that never parsed costs a REFINEMENT, not a MEASUREMENT. Unhandled
            # it kills the cycle — and under L4 that voids a whole outer sample, scoring one
            # flaky provider response as "this optimizer prompt is bad". Prior
            # `task_context`/`plan` stays adopted, the round is a stall, the loop continues.
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
            emit_phase(
                on_phase,
                transition.phase,
                "exit",
                round=round_num,
                view=None,
                data={"action": "parse_failure", "node": transition.template_name},
            )
            return

    # Same adoption seam as an L1 win: identity advances (fresh lineage, parent = the outgoing
    # incumbent) and the persistent memory carries forward. The frame surfaces L2/L3 own are
    # installed by `transition.apply` below, so no `advanced` overlay is passed here.
    new_opt = result.opt_sp
    cycle.adopt(new_opt, advanced={})
    cycle.tracking.current_sp = new_opt.to_job_search_point(
        base_pipeline_params=current_pp, schema=pipeline_schema
    )
    if obs is not None:
        with graceful(f"LayerApplied({transition.layer_id}) emit failed"):
            obs.emit_write_point(
                LayerApplied,
                layer=transition.layer_id,
                campaign_id=tracing_campaign_id,
                round_num=round_num,
                changes_description=result.opt_sp.lineage.changes_description or "",
            )
    transition.apply(cycle, result, round_num)
    emit_phase(
        on_phase,
        transition.phase,
        "exit",
        round=round_num,
        **transition.exit_payload_fn(cycle, result),
    )

    # Terminate outranks rebase: "stop" is more final than "try again from earlier". Both ride
    # this post-apply seam, so the layer's normal output is adopted and the exit-phase event
    # (which carries the proposal to the ledger) is emitted before the raise.
    if result.terminate_proposal is not None:
        reason = result.terminate_proposal.reason.strip()
        if not cycle.config.optimization.terminate_capability:
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
            raise StopLoop(StopReason.ABORT)

    if result.fork_proposal is not None:
        if not cycle.config.optimization.rebase_capability:
            # A model can volunteer the field even though the prompt carried no fork guidance.
            # Without this the gate is prompt-side only, and a no-rebase ablation — whose
            # whole point is that it cannot fork — silently forks anyway.
            logger.warning(
                "%s emitted fork_proposal while rebase_capability is off — ignored",
                transition.layer_id,
            )
        elif _stash_rebase_request(cycle, transition.layer_id, result.fork_proposal, round_num):
            raise StopLoop(StopReason.REBASED)


def _stash_rebase_request(
    cycle: Cycle, layer_id: str, proposal: ForkProposal, round_num: int
) -> bool:
    """Stash an L2/L3 ``fork_proposal`` as a ``Cycle.rebase_request``. **The layer decides WHETHER to
    rewind; UCB decides WHERE** — :func:`select_rewind_round` over the backpropagated lineage."""
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
        return False

    trigger = ForkTrigger.L2_REBASE if layer_id == "L2" else ForkTrigger.L3_REBASE
    unlock = bool(proposal.unlock_schema_field_rename) and not (
        cycle.config.optimization.schema_field_rename
    )
    cycle.rebase_request = RebaseRequest(
        fork_from_round=target_round,
        trigger=trigger,
        reason=str(proposal.reason or f"{layer_id} fork_proposal"),
        issued_by=f"{layer_id}/round_{round_num}",
        config_overrides=ConfigOverrides(schema_field_rename=True) if unlock else None,
    )
    logger.info(
        "%s emitted fork_proposal; UCB selected round %d of %d as the rewind target%s "
        "— cycle will exit with REBASED",
        layer_id,
        target_round,
        round_num,
        ", unlocking schema_field_rename" if unlock else "",
    )
    return True


def _trigger_payload(
    cycle: Cycle,
    round_num: int,
    patience: int | None,
    *,
    layer: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    esc = cycle.escalation
    counter_round = getattr(esc, f"{layer}_round")
    inputs_ref = {
        "round_num": round_num,
        f"{layer}_patience": patience,
        "entry_round": counter_round if counter_round > 0 else -1,
    }
    data: dict[str, Any] = {
        f"{layer}_round": counter_round,
        "stall_count": getattr(esc, f"{layer}_stall_count"),
        "best_composite_fitness_at_entry": getattr(esc, f"{layer}_best_composite_fitness_at_entry"),
        "best_composite_fitness_this_round": cycle.tracking.best_composite_fitness,
        "best_theta_at_entry": getattr(esc, f"{layer}_best_theta_at_entry"),
        "best_theta_this_round": cycle.tracking.best_theta,
    }
    return inputs_ref, data


async def escalate_l2(
    cycle: Cycle,
    config: CampaignConfig,
    pipeline_schema: PipelineSchema,
    round_num: int,
    on_phase: Callable[[PhaseEvent], None] | None = None,
    obs: ObservabilityBridge | None = None,
    tracing_campaign_id: str = "",
) -> StopReason | None:
    opt = config.optimization
    esc = cycle.escalation

    event = esc.observe_l2_escalation(
        current_composite_fitness=cycle.tracking.best_composite_fitness,
        current_theta=cycle.tracking.best_theta,
        l2_patience=opt.l2_patience,
        l3_patience=opt.l3_patience,
    )

    # L2 trigger decision is replayed for divergence — record fired-or-not.
    l2_inputs, l2_data = _trigger_payload(cycle, round_num, opt.l2_patience, layer="l2")
    record_decision(
        cycle.pending_decisions,
        ResumeCheckpointKind.L2_ESCALATION_TRIGGER,
        l2_inputs,
        event.next_action == NextAction.FIRE_L2,
        data=l2_data,
        round=round_num,
    )

    if event.next_action == NextAction.FIRE_L2:
        await _run_transition(
            L2,
            cycle,
            config,
            pipeline_schema,
            round_num,
            on_phase,
            obs=obs,
            tracing_campaign_id=tracing_campaign_id,
        )
        # Wound 4: post-L2 validator failure → L3 force-trigger, deterministic from L2 output
        # so resume reproduces it without a decision record. Every breach reaching here is a
        # HARD l1_layout failure — a real signal that L2 is thrashing inside the plan — so
        # there is no inert breach to except.
        breaches = cycle.opt_sp.memory.wounds.l2_guard_breaches
        if breaches:
            logger.warning(
                "L3 force-triggered by %d L2-output validator failure(s) at round %d",
                len(breaches),
                round_num,
            )
            await _run_transition(
                L3,
                cycle,
                config,
                pipeline_schema,
                round_num,
                on_phase,
                obs=obs,
                tracing_campaign_id=tracing_campaign_id,
            )
        return None

    # FIRE_L3 or STOP_L3_PATIENCE — record L3 trigger decision either way.
    l3_inputs, l3_data = _trigger_payload(cycle, round_num, opt.l3_patience, layer="l3")
    record_decision(
        cycle.pending_decisions,
        ResumeCheckpointKind.L3_ESCALATION_TRIGGER,
        l3_inputs,
        event.next_action == NextAction.FIRE_L3,
        data=l3_data,
        round=round_num,
    )

    if event.next_action == NextAction.FIRE_L3:
        await _run_transition(
            L3,
            cycle,
            config,
            pipeline_schema,
            round_num,
            on_phase,
            obs=obs,
            tracing_campaign_id=tracing_campaign_id,
        )
        return None

    # STOP_L3_PATIENCE — the reason rides the event (``_NEXT_ACTION_TO_STOP``), the one
    # NextAction→StopReason table; re-spelling it here is how the two drift apart.
    return event.stop_reason


__all__ = [
    "L2",
    "L3",
    "apply_fork_payload_to_opt_sp",
    "escalate_l2",
]
