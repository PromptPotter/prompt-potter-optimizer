"""L2/L3 transition runner + `escalate_l2` cascade + per-layer parse/apply.

V1 contract:
* L2 writes `task_context` + `action` + optional `axis_targeted` / `l1_layout` / `l1_overrides`.
* L3 writes `plan` (required) + optional `note` (sticky L3→L2; wholesale-replaced on each L3 fire).

`TransitionResult` (one fire's output) + `LayerStrategy` (static per-layer spec) are
defined below; the L2/L3 instances + their parse/apply/enter/exit are the module-level
constants `L2` and `L3`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from promptpotter.application.mask.backprop import select_rewind_round
from promptpotter.application.mask.load import load_mask_record
from promptpotter.application.optimization.dispatch.hub import (
    DispatchHub,
    build_bundle,
)
from promptpotter.application.optimization.dispatch.llm_call import (
    LLMCallContext,
    load_optimizer_prompt,
    resolve_node_layout,
    run_optimizer_node,
)
from promptpotter.application.optimization.dispatch.schemas import (
    ForkProposal,
    L2ContextOutput,
    L3PlanOutput,
    OptimizerAction,
    TerminateProposal,
)
from promptpotter.application.optimization.escalation.state import NextAction
from promptpotter.application.optimization.resume_and_fork import (
    ResumeCheckpointKind,
    record_decision,
)
from promptpotter.application.optimization.validators.l2_output import (
    L2_TASK_CONTEXT_STALE_REPEAT,
    run_l2_output_validators,
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
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.domain.validators import ValidatorOutcome
from promptpotter.infrastructure.llm.json_parse import MetaPromptParseError
from promptpotter.infrastructure.llm.models import emit_round_warning
from promptpotter.infrastructure.tracing import LayerApplied, observed_node
from promptpotter.shared import truncate
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.application.config import CampaignConfig
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.infrastructure.tracing import ObservabilityBridge

logger = logging.getLogger(__name__)


# L2-output validator breaches whose remedy is ALREADY applied by the validator itself
# (the malformed output is discarded/dropped before it can reach L1), so a SOLE breach of
# this kind is self-correcting and must NOT force-trigger an L3 strategic replan — forcing
# L3 on an inert output just layers escalations (the fork_f13331ff cascade: L3 short plan →
# L1 malformed plan → empty L1). Breaches OUTSIDE this set (e.g. l2_duplicate_insert — the
# genuine exhausted-refinement-surface signal) still force L3. Kept next to the consumer that
# reads it; built from the validator ``.id`` attributes so a mistyped id fails loud at import.
_L2_SOFT_REJECT_VALIDATOR_IDS: frozenset[str] = frozenset(
    {
        L2_TASK_CONTEXT_STALE_REPEAT.id,  # no-op / paraphrase merge — prior framing kept
    }
)


# ---------------------------------------------------------------------------
# Transition types — one L2/L3 fire's output + the static per-layer spec this
# module fills. Provider/model/temperature are sourced from the layer's
# optimizer node config (``datasets/_optimizer/pipeline.json``) inside
# ``llm_call``, not held here.
# ---------------------------------------------------------------------------


@dataclass
class TransitionResult:
    """L2/L3 transition result.

    L2 writes ``task_context``/``l1_layout``/``l1_overrides`` + ``action``
    (``normal_round`` default | ``probe_round`` for warned-query re-run on
    the same OSP). L3 writes ``plan``, optional ``l3_note`` (sticky until
    next L3 fire). Both layers may emit ``fork_proposal`` — the
    ``_run_transition`` post-apply hook stashes it on ``cycle.rebase_request``
    and raises ``StopLoop(StopReason.REBASED)``; ``runner.entry`` resolves
    the request post-finalize into an automatic ``_mint_fork`` +
    observer rebuild + loop re-entry on the new fork. ``axis_targeted``
    is required prose when ``action='probe_round'``.
    """

    opt_search_point: OptSearchPoint
    task_context: TaskDecomposition | None = None
    l3_note: str = ""
    action: OptimizerAction = "normal_round"
    axis_targeted: str = ""
    l1_layout: L1Layout | None = None
    l2_guard_breaches: list[ValidatorOutcome] = field(default_factory=list)
    l3_guard_breaches: list[ValidatorOutcome] = field(default_factory=list)
    fork_proposal: ForkProposal | None = None
    terminate_proposal: TerminateProposal | None = None
    debug_prompt: str = ""
    debug_response: dict[str, Any] | None = None


# Per-layer callable slots carried on ``LayerStrategy``.
ParseFn = Callable[[Any, OptSearchPoint, str], TransitionResult]
ApplyFn = Callable[["Cycle", TransitionResult, int], None]
PayloadFn = Callable[["Cycle"], dict[str, Any]]
ExitFn = Callable[["Cycle", TransitionResult], dict[str, Any]]


@dataclass(frozen=True)
class LayerStrategy:
    """Static per-layer spec for one escalation layer (L2 or L3).

    Pure data read by ``_run_transition``; the ``L2``/``L3`` instances and
    their parse/apply/enter/exit callables are the module-level constants below.
    """

    layer_id: Literal["L2", "L3"]
    template_name: str
    phase: CampaignPhase
    parse: ParseFn
    apply: ApplyFn
    enter_payload_fn: PayloadFn
    exit_payload_fn: ExitFn


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
    if proposed_tc and not opt_sp.memory.task_context.merge_changes_nothing(proposed_tc):
        new_task_context = opt_sp.memory.task_context.merge(proposed_tc)

    proposed_layout = coerce_l1_layout(raw.l1_layout)
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

    failures = run_l2_output_validators(
        {
            "task_context_proposed": proposed_tc,
            "task_context_applied": new_task_context,
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
        l2_guard_breaches=failures,
        fork_proposal=raw.fork_proposal,
        terminate_proposal=raw.terminate_proposal,
        debug_prompt=prompt,
        debug_response=raw.model_dump(),
    )


def _apply_l2(cycle: Cycle, result: TransitionResult, round_num: int) -> None:
    osp = cycle.opt_sp
    if result.task_context:
        osp.memory.task_context = result.task_context
    if result.l1_layout is not None:
        osp.memory.l1_layout = result.l1_layout
    osp.memory.wounds.l2_guard_breaches = list(result.l2_guard_breaches)
    cycle.escalation.record_l2_fired(
        best_composite_fitness=cycle.tracking.best_composite_fitness,
        best_theta=cycle.tracking.best_theta,
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
        "l2_best_composite_fitness_at_entry": cycle.escalation.l2_best_composite_fitness_at_entry,
        "l2_best_theta_at_entry": cycle.escalation.l2_best_theta_at_entry,
        "param_changes_count": len(result.opt_search_point.memory.l1_overrides),
        "task_context_changed": result.task_context is not None,
        "l1_layout_changed": result.l1_layout is not None,
        "changes_description": result.opt_search_point.lineage.changes_description or "",
        "action": result.action,
        "axis_targeted": result.axis_targeted,
        "warned_samples": len(cycle.warned_queries),
        "l2_prompt": result.debug_prompt,
        "l2_response": result.debug_response,
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
        terminate_proposal=raw.terminate_proposal,
        debug_prompt=prompt,
        debug_response=raw.model_dump(),
    )


def _apply_l3(cycle: Cycle, result: TransitionResult, round_num: int) -> None:
    # Order matters: ``_run_transition``'s ``copy_memory_to`` carried prior l3_note onto the new OSP;
    # overwrite with L3's output (may be ``""``) — the "cleared only when L3 fires again" contract.
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
    # ``l3_*_at_entry`` read by ``EscalationFSM.fold`` on resume; ``record_l3_fired`` resets L2 state to these.
    payload: dict[str, Any] = {
        "l3_round": cycle.escalation.l3_round,
        "l3_stall_count": cycle.escalation.l3_stall_count,
        "l3_best_composite_fitness_at_entry": cycle.escalation.l3_best_composite_fitness_at_entry,
        "l3_best_theta_at_entry": cycle.escalation.l3_best_theta_at_entry,
        "new_plan_preview": str(result.opt_search_point.plan)[:120],
        "changes_description": result.opt_search_point.lineage.changes_description or "",
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


# ---------------------------------------------------------------------------
# Fork payload — applies operator/LLM-issued OSP deltas at fork mint time.
# ---------------------------------------------------------------------------


def apply_fork_payload_to_osp(opt_sp: OptSearchPoint, payload: ForkSpec) -> None:
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
        result = validate_l1_layout(
            layout, spec=NODE_LAYOUTS["l1_generate"], prior_layout=opt_sp.memory.l1_layout
        )
        if not result.is_valid:
            ids = sorted({o.validator_id for o in result.outcomes})
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
        template, prompt_vars = DispatchHub.fill(
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
        except MetaPromptParseError as parse_err:
            # A refinement that never parsed costs a REFINEMENT, not a MEASUREMENT. Left
            # unhandled this kills the whole cycle — and under L4 that voids an entire outer
            # sample, so one flaky provider response is scored as "this meta-prompt is bad".
            # `l1_generate` has always survived the same failure (`l1/generate.py`); L2/L3
            # simply never got the same treatment. Prior `task_context`/`plan` stays adopted
            # (identical to a soft-reject), the round is a stall, and the loop continues.
            logger.error(
                "%s: meta-prompt parse failure — refinement discarded, prior framing kept. [%s]",
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

    # Same adoption seam as an L1 win: identity advances (new_opt carries a fresh
    # lineage, parent = the outgoing incumbent) and the persistent memory carries
    # forward. The frame surfaces L2/L3 own (task_context / l1_layout) are then
    # installed by `transition.apply` below, so no `advanced` overlay is passed here.
    new_opt = result.opt_search_point
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

    # Terminate outranks rebase: an unrecoverable-fault judgment ("stop") is more final
    # than a rewind ("try again from earlier"). Both ride the same post-apply seam as
    # fork_proposal, so the layer's normal output is fully adopted and the exit-phase event
    # (which carries ``terminate_proposal`` to the ledger) is emitted before the raise. Reuses
    # the existing HALTED-class StopReason.ABORT — no new stop reason, no sidecar (R-48/R-09).
    if result.terminate_proposal is not None:
        if not cycle.config.optimization.terminate_capability:
            # Same shape as the fork gate below: off ⇒ the prompt carried no
            # terminate guidance, and a volunteered field must not ABORT the run.
            logger.warning(
                "%s emitted terminate_proposal while terminate_capability is off — ignored",
                transition.layer_id,
            )
        else:
            logger.info(
                "%s emitted terminate_proposal — cycle will exit HALTED (ABORT): %s",
                transition.layer_id,
                result.terminate_proposal.reason or "unrecoverable fault",
            )
            raise StopLoop(StopReason.ABORT)

    if result.fork_proposal is not None:
        if not cycle.config.optimization.rebase_capability:
            # The capability is OFF, so the prompt carried no fork guidance — but a
            # model can still volunteer the field. Without this the gate would be
            # prompt-side only: a no-rebase ablation run could silently fork anyway,
            # and its whole point is that it cannot.
            logger.warning(
                "%s emitted fork_proposal while rebase_capability is off — ignored",
                transition.layer_id,
            )
        elif _stash_rebase_request(cycle, transition.layer_id, result.fork_proposal, round_num):
            raise StopLoop(StopReason.REBASED)


def _stash_rebase_request(cycle: Cycle, layer_id: str, proposal: Any, round_num: int) -> bool:
    """Stash an L2/L3 fork_proposal as a Cycle.rebase_request. False ⇒ do not fork.

    **The layer decides WHETHER to rewind; UCB decides WHERE.** The layer sees that its
    subtree is exhausted — a judgment no rule makes well — and says so. Which ancestor
    to re-expand from is then a statistical question with a right answer, and it is
    answered by :func:`select_rewind_round`: UCB1 over the backpropagated lineage,
    balancing each ancestor's mean θ against how little it has been explored. That the
    layer no longer names a round is the point: it never had the evidence to. No panel
    ever enumerated the ancestors and their fitness, so a free-form ``round_offset`` was
    an unanchored guess wearing the costliest decision in the loop.

    The runner resolves the request post-finalize: ``_mint_fork`` then rebuilds observers
    around the new fork's ledger and re-enters the optimize loop (capped at
    ``MAX_AUTO_REBASES`` per CLI invocation). Stashing here keeps the old cycle's
    finalize using the un-retargeted ``session.state.cycle_id`` — a crash mid-finalize
    leaves the old cycle clean and the rebase un-minted, which the operator can
    re-trigger by re-invoking ``resume``.

    An ``unlock_schema_field_rename`` request widens into the fork's ``ConfigOverrides``
    here — the unlock rides the rewind and cannot travel without it, so the parent keeps
    its frozen config and its comparability. Re-requesting a lock that is already open is
    dropped: it would change nothing and still cost a whole sibling cycle.
    """
    session = cycle.session
    record = load_mask_record(session.store, session.campaign_id)
    target_round = select_rewind_round(
        record, cycle_id=session.state.cycle_id or "", current_round=round_num
    )
    if target_round is None:
        # Nothing above the current node (round 0 of a root). A rewind to nowhere would
        # mint a duplicate of this cycle and burn a whole run, so decline the fork and
        # let the loop stop on its own terms.
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
        "best_theta_at_entry": getattr(esc, f"{layer}_best_theta_at_entry"),
        "best_theta_this_round": cycle.tracking.best_theta,
    }
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
    Called via `escalate_or_stop` (`runner/round.py`) on the `FIRE_L2` decision.
    """
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
        # Wound 4: post-L2 validator failure → L3 force-trigger. Deterministic from L2 output,
        # so resume reproduces without a separate decision record.
        # Exception: when EVERY breach is a soft-reject (self-correcting — the validator already
        # discarded the output: a stale task_context repeat kept the prior framing), L3 is skipped.
        # See _L2_SOFT_REJECT_VALIDATOR_IDS — forcing L3 on an inert breach only layers escalations.
        breaches = cycle.opt_sp.memory.wounds.l2_guard_breaches
        if breaches and not all(b.validator_id in _L2_SOFT_REJECT_VALIDATOR_IDS for b in breaches):
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

    # STOP_L3_PATIENCE — the reason rides the event (``_NEXT_ACTION_TO_STOP``), the one
    # NextAction→StopReason table; re-spelling it here is how the two drift apart.
    return event.stop_reason


__all__ = [
    "L2",
    "L3",
    "apply_fork_payload_to_osp",
    "escalate_l2",
]
