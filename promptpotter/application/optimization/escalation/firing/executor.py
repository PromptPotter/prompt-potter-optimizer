"""L2/L3 transition runner + `escalate_l2` cascade + per-layer parse/apply.

V1 contract:
* L2 writes `task_context` + `action` + optional `axis_targeted` / `l1_layout` / `l1_overrides`.
* L3 writes `plan` (required) + optional `note` (sticky L3→L2; wholesale-replaced on each L3 fire).

`LayerStrategy` spec lives in `optimization/transitions.py`; the L2/L3 instances + their
parse/apply/enter/exit are the module-level constants `L2` and `L3` below.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from promptpotter.application.optimization.dispatch.hub import (
    DispatchHub,
    build_bundle,
)
from promptpotter.application.optimization.dispatch.llm_call import (
    LLMCallContext,
    load_optimizer_prompt,
    run_optimizer_node,
)
from promptpotter.application.optimization.dispatch.schemas import L2ContextOutput, L3PlanOutput
from promptpotter.application.optimization.escalation.state import NextAction
from promptpotter.application.optimization.resume_and_fork import (
    ResumeCheckpointKind,
    record_decision,
)
from promptpotter.application.optimization.transitions import LayerStrategy, TransitionResult
from promptpotter.application.optimization.validators.l2_output import run_l2_output_validators
from promptpotter.application.optimization.validators.l3_output import run_l3_output_validators
from promptpotter.domain.l1_layout import L1Layout, coerce_l1_layout, validate_l1_layout
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.phases import CampaignPhase, PhaseEvent, StopLoop, StopReason, emit_phase
from promptpotter.domain.run_records import ForkPayload, ForkTrigger, RebaseRequest
from promptpotter.domain.validators import ValidatorOutcome
from promptpotter.infrastructure import llm as _llm_client
from promptpotter.infrastructure.llm.models import emit_round_warning
from promptpotter.infrastructure.tracing import LayerApplied, observed_node
from promptpotter.shared import truncate
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.application.config import CampaignConfig
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.infrastructure.tracing import ObservabilityBridge

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# L2 — parse + apply + enter/exit payloads + strategy constant.
# L2 refines task_context (broadcast framing) + optionally edits l1_layout / l1_overrides.
# No pipeline_params deltas — those belong to L1.
# ---------------------------------------------------------------------------


def _parse_l2(raw: L2ContextOutput, opt_sp: OptSearchPoint, prompt: str) -> TransitionResult:
    rationale = raw.rationale or "L2 refine_strategy transition"
    changes: dict[str, Any] = {"changes_description": f"L2: {truncate(rationale, 80)}"}
    if raw.l1_overrides:
        changes["l1_overrides"] = {**opt_sp.memory.l1_overrides, **raw.l1_overrides}

    new_task_context = None
    proposed_tc = raw.task_context if raw.task_context else None
    if proposed_tc:
        merged = opt_sp.memory.task_context.merge(proposed_tc)
        if merged.to_dict() != opt_sp.memory.task_context.to_dict():
            new_task_context = merged

    proposed_layout = coerce_l1_layout(raw.l1_layout)
    layout_outcomes: list[ValidatorOutcome] = []
    accepted_layout: L1Layout | None = None
    if proposed_layout is not None:
        layout_result = validate_l1_layout(proposed_layout, prior_layout=opt_sp.memory.l1_layout)
        layout_outcomes = list(layout_result.outcomes)
        if layout_result.is_valid:
            accepted_layout = proposed_layout

    # Supplemental rules + situational examples: full-replace; non-empty ⇒ replace L2-authored layer; empty ⇒ keep current.
    new_supplemental = list(raw.l1_supplemental_rules) if raw.l1_supplemental_rules else None
    new_examples = list(raw.l1_situational_examples) if raw.l1_situational_examples else None

    failures = run_l2_output_validators(
        {
            "task_context_proposed": proposed_tc,
            "task_context_applied": new_task_context,
            "l1_supplemental_rules_proposed": new_supplemental,
            "l1_situational_examples_proposed": new_examples,
        },
        opt_sp,
    )
    failures.extend(layout_outcomes)
    if failures:
        failed_ids = ", ".join(o.validator_id for o in failures)
        logger.warning(
            "L2 output failed %d validator(s): %s",
            len(failures),
            failed_ids,
        )
        emit_round_warning(
            kind="l2_validator_soft_reject",
            severity="warning",
            message=(
                f"L2 framing update soft-rejected by {len(failures)} check(s) "
                f"({failed_ids}) — the prior task_context was kept and L1 continues."
            ),
            detail={"validator_ids": [o.validator_id for o in failures]},
        )

    return TransitionResult(
        opt_search_point=opt_sp.mutate(source="l2_context", **changes),
        task_context=new_task_context,
        action=raw.action,
        axis_targeted=raw.axis_targeted,
        l1_layout=accepted_layout,
        l1_supplemental_rules=new_supplemental,
        l1_situational_examples=new_examples,
        l2_guard_breaches=failures,
        fork_proposal=raw.fork_proposal,
        debug_prompt=prompt,
        debug_response=raw.model_dump(),
    )


def _apply_l2(cycle: Cycle, result: TransitionResult, round_num: int) -> None:
    osp = cycle.opt_sp
    if result.task_context:
        osp.memory.task_context = result.task_context
    if result.l1_layout is not None:
        osp.memory.l1_layout = result.l1_layout
    if result.l1_supplemental_rules is not None:
        osp.memory.l1_supplemental_rules = result.l1_supplemental_rules
    if result.l1_situational_examples is not None:
        osp.memory.l1_situational_examples = result.l1_situational_examples
    osp.memory.wounds.l2_guard_breaches = list(result.l2_guard_breaches)
    cycle.escalation.record_l2_fired(
        best_accuracy=cycle.tracking.best_accuracy,
        best_composite_fitness=cycle.tracking.best_composite_fitness,
    )
    # Don't clobber prior axis when LLM omits it — stale axis beats empty for the next probe-outcome render.
    if result.axis_targeted:
        cycle.last_l2_axis = result.axis_targeted

    is_probe = result.action == "probe_round"
    record_decision(
        cycle.pending_decisions,
        ResumeCheckpointKind.PROBE_ROUND_COMMITMENT,
        {"round_num": round_num, "l2_round": cycle.escalation.l2_round},
        is_probe,
        data={
            "action": result.action,
            "task_context_changed": result.task_context is not None,
            "changes_description": result.opt_search_point.lineage.changes_description or "",
            "axis_targeted": result.axis_targeted,
        },
        round=round_num,
    )
    if is_probe:
        cycle.probe_next_round = True


def _l2_enter(cycle: Cycle) -> dict[str, Any]:
    return {
        "l2_round": cycle.escalation.l2_round,
        "l1_stall_count": cycle.escalation.l1_stall_count,
        "l1_overrides": cycle.opt_sp.memory.l1_overrides,
        "current_accuracy": cycle.tracking.current_accuracy,
        "best_accuracy": cycle.tracking.best_accuracy,
    }


def _l2_exit(cycle: Cycle, result: TransitionResult) -> dict[str, Any]:
    # ``l2_*_at_entry`` are read by ``EscalationFSM.fold`` on resume (canonical post-fire L2 state from ``record_l2_fired``).
    payload: dict[str, Any] = {
        "l2_round": cycle.escalation.l2_round,
        "l2_stall_count": cycle.escalation.l2_stall_count,
        "l2_best_accuracy_at_entry": cycle.escalation.l2_best_accuracy_at_entry,
        "l2_best_composite_fitness_at_entry": cycle.escalation.l2_best_composite_fitness_at_entry,
        "param_changes_count": len(result.opt_search_point.memory.l1_overrides),
        "task_context_changed": result.task_context is not None,
        "l1_layout_changed": result.l1_layout is not None,
        "l1_supplemental_rules_n": (
            len(result.l1_supplemental_rules) if result.l1_supplemental_rules is not None else None
        ),
        "l1_situational_examples_n": (
            len(result.l1_situational_examples)
            if result.l1_situational_examples is not None
            else None
        ),
        "changes_description": result.opt_search_point.lineage.changes_description or "",
        "action": result.action,
        "axis_targeted": result.axis_targeted,
        "warned_samples": len(cycle.warned_queries),
        "l2_prompt": result.debug_prompt,
        "l2_response": result.debug_response,
    }
    if result.fork_proposal is not None:
        payload["fork_proposal"] = result.fork_proposal.model_dump()
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


# ---------------------------------------------------------------------------
# L3 — parse + apply + enter/exit payloads + strategy constant.
# L3 rewrites ``plan`` (strategic frame). Optional ``note`` is the sticky L3→L2 pointer
# (survives L2 fires, replaced wholesale on each L3 fire). No ``pipeline_params`` deltas.
# ---------------------------------------------------------------------------


def _parse_l3(raw: L3PlanOutput, opt_sp: OptSearchPoint, prompt: str) -> TransitionResult:
    new_plan = raw.plan or opt_sp.plan
    rationale = raw.rationale or "L3 modify_plan transition"
    failures = run_l3_output_validators({"plan": new_plan}, opt_sp)
    if failures:
        logger.warning(
            "L3 output failed %d validator(s): %s",
            len(failures),
            ", ".join(o.validator_id for o in failures),
        )
    return TransitionResult(
        opt_search_point=opt_sp.mutate(
            plan=new_plan, changes_description=f"L3: {truncate(rationale, 80)}", source="l3_plan"
        ),
        l3_note=raw.note,
        l3_guard_breaches=failures,
        fork_proposal=raw.fork_proposal,
        debug_prompt=prompt,
        debug_response=raw.model_dump(),
    )


def _apply_l3(cycle: Cycle, result: TransitionResult, round_num: int) -> None:
    # Order matters: ``_run_transition``'s ``copy_memory_to`` carried prior l3_note onto the new OSP;
    # overwrite with L3's output (may be ``""``) — the "cleared only when L3 fires again" contract.
    cycle.opt_sp.memory.wounds.l3_note = result.l3_note
    cycle.opt_sp.memory.wounds.l3_guard_breaches = list(result.l3_guard_breaches)
    cycle.escalation.record_l3_fired(
        best_accuracy=cycle.tracking.best_accuracy,
        best_composite_fitness=cycle.tracking.best_composite_fitness,
    )


def _l3_enter(cycle: Cycle) -> dict[str, Any]:
    return {
        "l3_round": cycle.escalation.l3_round,
        "l2_stall_count": cycle.escalation.l2_stall_count,
        "current_plan_preview": str(cycle.opt_sp.plan)[:120],
    }


def _l3_exit(cycle: Cycle, result: TransitionResult) -> dict[str, Any]:
    # ``l3_*_at_entry`` read by ``EscalationFSM.fold`` on resume; ``record_l3_fired`` resets L2 state to these.
    payload: dict[str, Any] = {
        "l3_round": cycle.escalation.l3_round,
        "l3_stall_count": cycle.escalation.l3_stall_count,
        "l3_best_accuracy_at_entry": cycle.escalation.l3_best_accuracy_at_entry,
        "l3_best_composite_fitness_at_entry": cycle.escalation.l3_best_composite_fitness_at_entry,
        "new_plan_preview": str(result.opt_search_point.plan)[:120],
        "changes_description": result.opt_search_point.lineage.changes_description or "",
    }
    if result.fork_proposal is not None:
        payload["fork_proposal"] = result.fork_proposal.model_dump()
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


# ---------------------------------------------------------------------------
# Fork payload — applies operator/LLM-issued OSP deltas at fork mint time.
# ---------------------------------------------------------------------------


def apply_fork_payload_to_osp(opt_sp: OptSearchPoint, payload: ForkPayload) -> None:
    """Stamp a fork payload's L1-surface deltas on the OSP — same shape L2 writes. Assumes
    `payload.l1_layout is not None` (callers without deltas guard at the call site).
    """
    if payload.l1_layout is not None:
        layout = coerce_l1_layout(payload.l1_layout)
        if layout is None:
            raise ValueError(
                f"Fork payload l1_layout is unparseable: {payload.l1_layout!r}. "
                "Expect a dict whose keys are L1 layout slots and values are lists of "
                "placeholder names."
            )
        result = validate_l1_layout(layout, prior_layout=opt_sp.memory.l1_layout)
        if not result.is_valid:
            ids = sorted({o.validator_id for o in result.outcomes if not o.passed})
            raise ValueError(f"Fork payload l1_layout failed hard validators: {ids}")
        opt_sp.memory.l1_layout = layout


# ---------------------------------------------------------------------------
# Cascade — escalate_l2 walks the L2/L3 patience ladder + wound-4 force-L3.
# ---------------------------------------------------------------------------


async def _run_transition(
    transition: LayerStrategy,
    cycle: Cycle,
    config: CampaignConfig,
    pipeline_schema: Any,
    round_num: int,
    on_phase: Callable[[PhaseEvent], None] | None,
    *,
    obs: ObservabilityBridge | None,
    tracing_campaign_id: str,
) -> None:
    """enter → LLM → parse → adopt → LayerApplied → side-effects → exit.
    Layer-agnostic — everything layer-specific reads off the `LayerStrategy` spec.
    """
    assert cycle.tracking.current_sp is not None
    client = _llm_client.get_llm_client(config.optimizer_llm.provider)
    current_pp = cycle.tracking.current_sp.pipeline_params

    emit_phase(
        on_phase, transition.phase, "enter", round=round_num, **transition.enter_payload_fn(cycle)
    )
    async with observed_node(
        f"{transition.template_name}_r{round_num}",
        "llm/meta",
        obs=obs,
        campaign_id=tracing_campaign_id,
        round_num=round_num,
    ):
        template = load_optimizer_prompt(transition.template_name)
        prompt_vars = DispatchHub.fill_fixed(template, build_bundle(cycle))
        raw, prompt = await run_optimizer_node(
            template_name=transition.template_name,
            prompt_vars=prompt_vars,
            llm_client=client,
            model=config.optimizer_llm.model,
            temperature=config.optimizer_llm.temperature_for(transition.layer_id),
            context=LLMCallContext(
                ledger=cycle.session.state.ledger,
                round_num=round_num,
                cache=cycle.session.store.optimizer_calls,
            ),
        )
        result = transition.parse(raw, cycle.opt_sp, prompt)

    new_opt = result.opt_search_point
    cycle.opt_sp.copy_memory_to(new_opt)
    cycle.opt_sp = new_opt
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
                changes_description=result.opt_search_point.lineage.changes_description or "",
            )
    transition.apply(cycle, result, round_num)
    emit_phase(
        on_phase,
        transition.phase,
        "exit",
        round=round_num,
        **transition.exit_payload_fn(cycle, result),
    )

    if result.fork_proposal is not None:
        _stash_rebase_request(cycle, transition.layer_id, result.fork_proposal, round_num)
        raise StopLoop(StopReason.REBASED)


def _stash_rebase_request(cycle: Cycle, layer_id: str, proposal: Any, round_num: int) -> None:
    """Stash an L2/L3 fork_proposal as a Cycle.rebase_request.

    The runner resolves the request post-finalize: ``_mint_fork`` then
    rebuilds observers around the new fork's ledger and re-enters the
    optimize loop (capped at ``MAX_AUTO_REBASES`` per CLI invocation).
    Stashing here keeps the old cycle's finalize using the un-retargeted
    ``session.state.cycle_id`` — a crash mid-finalize leaves the old
    cycle clean and the rebase un-minted, which the operator can
    re-trigger by re-invoking ``resume``.
    """
    trigger = ForkTrigger.L2_REBASE if layer_id == "L2" else ForkTrigger.L3_REBASE
    target_round = max(0, round_num + int(proposal.round_offset))
    cycle.rebase_request = RebaseRequest(
        fork_from_round=target_round,
        trigger=trigger,
        reason=str(proposal.reason or f"{layer_id} fork_proposal: rewind {proposal.round_offset}"),
        issued_by=f"{layer_id}/round_{round_num}",
    )
    logger.info(
        "%s emitted fork_proposal: rewind to round %d (offset=%d) — cycle will exit with REBASED",
        layer_id,
        target_round,
        proposal.round_offset,
    )


def _trigger_payload(
    cycle: Cycle,
    round_num: int,
    patience: int | None,
    *,
    layer: str,
    track_accuracy: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """(inputs_ref, data) for an L2/L3 escalation-trigger decision."""
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
    }
    if track_accuracy:
        data["best_accuracy"] = cycle.tracking.best_accuracy
    return inputs_ref, data


async def escalate_l2(
    cycle: Cycle,
    config: CampaignConfig,
    pipeline_schema: Any,
    round_num: int,
    on_phase: Callable[[PhaseEvent], None] | None = None,
    obs: ObservabilityBridge | None = None,
    tracing_campaign_id: str = "",
) -> StopReason | None:
    """Run the L2/L3 patience cascade + L3 force-trigger heal on L2 validator failures.
    Called from `FIRE_L2` dispatch and the mid-round DegradationCheck path.
    """
    opt = config.optimization
    esc = cycle.escalation

    event = esc.observe_l2_escalation(
        current_composite_fitness=cycle.tracking.best_composite_fitness,
        l2_patience=opt.l2_patience,
        l3_patience=opt.l3_patience,
    )

    # L2 trigger decision is replayed for divergence — record fired-or-not.
    l2_inputs, l2_data = _trigger_payload(
        cycle, round_num, opt.l2_patience, layer="l2", track_accuracy=True
    )
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
        # Wound 4: post-L2 validator failure → L3 force-trigger. Deterministic from L2 output,
        # so resume reproduces without a separate decision record.
        # Exception: a SOLE soft-reject (paraphrase/verbatim repeat — already discarded by the
        # validator) skips L3. The fork_f13331ff cascade — L3 short plan → L1 malformed plan →
        # empty L1 — showed firing L3 on a single soft-reject layers escalations instead of
        # letting the loop self-correct.
        breaches = cycle.opt_sp.memory.wounds.l2_guard_breaches
        SOFT_REJECT_IDS = {
            "l2_task_context_verbatim_repeat",
            "l2_task_context_paraphrase_repeat",
        }
        if breaches and not all(b.validator_id in SOFT_REJECT_IDS for b in breaches):
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
        elif breaches:
            logger.info(
                "L2 produced %d soft-reject breach(es) at round %d (%s) — skipping L3 "
                "force-trigger; prior task_context retained, L1 continues next round",
                len(breaches),
                round_num,
                ", ".join(b.validator_id for b in breaches),
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

    return StopReason.L3_PATIENCE


__all__ = [
    "L2",
    "L3",
    "_apply_l2",
    "_apply_l3",
    "_parse_l2",
    "_parse_l3",
    "apply_fork_payload_to_osp",
    "escalate_l2",
]
