"""Layer-escalation driver — L2 / L3 strategies + L1→L2→L3 fan-out.

One-way arrow: this module imports from ``cycle.py``; the reverse is
forbidden by ``tests/test_layer_imports.py``. Both layers share the same
``LayerStrategy`` shape — per-layer differences live in module-level
``_parse_*`` / ``_apply_*`` / ``_*_enter`` / ``_*_exit`` callables wired
into the ``L2`` and ``L3`` instances.

V1 contract:
* L2 writes ``task_context`` (broadcast framing refinement, the L2→all
  channel) + ``action`` (``normal_round`` or ``probe_round``) + optional
  ``axis_targeted`` / ``l1_layout`` / ``l1_config``. No
  L1-surface scheme/text/template overrides.
* L3 writes ``plan`` (required) + optional ``note`` (sticky L3→L2
  pointer; survives L2 fires, replaced wholesale on each L3 fire). No
  ``pipeline_params`` deltas.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from promptpotter.application.optimization.cycle import NextAction
from promptpotter.application.optimization.dispatch_hub import (
    DispatchHub,
    build_bundle,
)
from promptpotter.application.optimization.formatting import warning_summary
from promptpotter.application.optimization.l2_validators import (
    run_l2_output_validators,
    run_l3_output_validators,
)
from promptpotter.application.optimization.llm_call import load_optimizer_prompt
from promptpotter.application.optimization.transitions import (
    OptimizerAction,
    TransitionResult,
    run_layer_transition,
)
from promptpotter.domain.l1_layout import (
    L1_LAYOUT_SLOTS,
    L1Layout,
    default_l1_layout,
    validate_l1_layout,
)
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.phases import CampaignPhase, PhaseEvent, StopReason, emit_phase
from promptpotter.domain.run_records import DecisionKind, SweepPayload, record_decision
from promptpotter.infrastructure import llm as _llm_client
from promptpotter.infrastructure.tracing import LayerApplied, observed_node
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.application.config import CampaignConfig
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.infrastructure.tracing import ObservabilityBridge

logger = logging.getLogger(__name__)

__all__ = [
    "L2",
    "L3",
    "LayerStrategy",
    "apply_sweep_payload_to_osp",
    "escalate_l2",
]


def _coerce_l1_layout(raw_layout: Any) -> L1Layout | None:
    """Best-effort coerce ``{slot: [placeholder, …]}`` → :class:`L1Layout`.

    Returns ``None`` when the input is empty or shaped wrong; lets the
    validator surface mandatory-presence/unknown-name failures uniformly
    rather than crashing on a Pydantic validation error here.
    """
    if not isinstance(raw_layout, dict) or not raw_layout:
        return None
    sanitised: dict[str, list[str]] = {}
    for slot in L1_LAYOUT_SLOTS:
        vals = raw_layout.get(slot)
        if isinstance(vals, list) and all(isinstance(v, str) for v in vals):
            sanitised[slot] = list(vals)
    if not sanitised:
        return None
    try:
        return L1Layout(**sanitised)
    except Exception:
        return None


def apply_sweep_payload_to_osp(opt_sp: OptSearchPoint, payload: SweepPayload) -> None:
    """Stamp operator's L1-surface deltas on the OSP — same shape L2 writes."""
    if payload.l1_layout is not None:
        layout = _coerce_l1_layout(payload.l1_layout)
        if layout is None:
            raise ValueError(
                f"Sweep payload l1_layout is unparseable: {payload.l1_layout!r}. "
                "Expect a dict whose keys are L1 layout slots and values are lists of "
                "placeholder names."
            )
        result = validate_l1_layout(layout, prior_layout=opt_sp.l1_layout)
        if not result.is_valid:
            ids = sorted({o.validator_id for o in result.outcomes if not o.passed})
            raise ValueError(f"Sweep payload l1_layout failed hard validators: {ids}")
        opt_sp.l1_layout = layout


# ---------------------------------------------------------------------------
# LayerStrategy — one shape for L2 and L3.
# ---------------------------------------------------------------------------


ParseFn = Callable[[dict, OptSearchPoint, str], TransitionResult]
ApplyFn = Callable[["Cycle", TransitionResult, int], None]
PayloadFn = Callable[["Cycle"], dict[str, Any]]
ExitFn = Callable[["Cycle", TransitionResult], dict[str, Any]]


@dataclass(frozen=True)
class LayerStrategy:
    layer_id: Literal["L2", "L3"]
    template_name: str
    default_temperature: float
    phase: CampaignPhase
    parse: ParseFn
    apply: ApplyFn
    enter_payload_fn: PayloadFn
    exit_payload_fn: ExitFn

    def build_prompt_vars(self, cycle: Cycle) -> dict[str, str]:
        bundle = build_bundle(cycle)
        template = load_optimizer_prompt(self.template_name)
        return DispatchHub.fill_fixed(template, bundle)

    def build_result(self, raw: dict, opt_sp: OptSearchPoint, prompt: str) -> TransitionResult:
        return self.parse(raw, opt_sp, prompt)

    def apply_side_effects(self, cycle: Cycle, result: TransitionResult, round_num: int) -> None:
        self.apply(cycle, result, round_num)

    def enter_payload(self, cycle: Cycle) -> dict[str, Any]:
        return self.enter_payload_fn(cycle)

    def exit_payload(self, cycle: Cycle, result: TransitionResult) -> dict[str, Any]:
        return self.exit_payload_fn(cycle, result)


# ---------------------------------------------------------------------------
# L2 — refine strategy via OSP mutations.
# ---------------------------------------------------------------------------


def _parse_l2(raw: dict, opt_sp: OptSearchPoint, prompt: str) -> TransitionResult:
    changes: dict[str, Any] = {
        "changes_description": f"L2: {raw.get('rationale', 'L2 refine_strategy transition')[:80]}"
    }
    if isinstance(raw.get("l1_config"), dict) and raw["l1_config"]:
        changes["l1_config"] = {**opt_sp.l1_config, **raw["l1_config"]}

    new_task_context = None
    proposed_tc = raw.get("task_context") if isinstance(raw.get("task_context"), dict) else None
    if proposed_tc:
        merged = opt_sp.task_context.merge(proposed_tc)
        if merged.to_dict() != opt_sp.task_context.to_dict():
            new_task_context = merged

    raw_action = raw.get("action")
    action: OptimizerAction = (
        raw_action if raw_action in ("normal_round", "probe_round") else "normal_round"
    )
    raw_axis = raw.get("axis_targeted")
    axis_targeted = raw_axis if isinstance(raw_axis, str) else ""

    proposed_layout = _coerce_l1_layout(raw.get("l1_layout"))
    layout_outcomes: list = []
    accepted_layout: L1Layout | None = None
    if proposed_layout is not None:
        result = validate_l1_layout(proposed_layout, prior_layout=opt_sp.l1_layout)
        layout_outcomes = list(result.outcomes)
        if result.is_valid:
            accepted_layout = proposed_layout

    failures = run_l2_output_validators(
        {"task_context_proposed": proposed_tc, "task_context_applied": new_task_context},
        opt_sp,
    )
    failures.extend(layout_outcomes)
    if failures:
        logger.warning(
            "L2 output failed %d validator(s): %s",
            len(failures),
            ", ".join(o.validator_id for o in failures),
        )

    return TransitionResult(
        opt_search_point=opt_sp.mutate(source="l2_context", **changes),
        task_context=new_task_context,
        action=action,
        axis_targeted=axis_targeted,
        l1_layout=accepted_layout,
        l2_output_failures=failures,
        debug_prompt=prompt,
        debug_response=raw,
    )


def _apply_l2(cycle: Cycle, result: TransitionResult, round_num: int) -> None:
    osp = cycle.opt_sp
    if result.task_context:
        osp.task_context = result.task_context
    if result.l1_layout is not None:
        osp.l1_layout = result.l1_layout
    osp.l2_output_failures = list(result.l2_output_failures)
    cycle.escalation.record_l2_fired(
        best_accuracy=cycle.tracking.best_accuracy,
        best_composite_fitness=cycle.tracking.best_composite_fitness,
    )
    # Don't clobber prior axis when the LLM omits it — a stale axis is more
    # informative than an empty one for the next probe-outcome render.
    if result.axis_targeted:
        cycle.last_l2_axis = result.axis_targeted

    is_probe = result.action == "probe_round"
    record_decision(
        cycle.pending_decisions,
        DecisionKind.PROBE_ROUND_COMMITMENT,
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
        "l1_config": cycle.opt_sp.l1_config,
        "current_accuracy": cycle.tracking.current_accuracy,
        "best_accuracy": cycle.tracking.best_accuracy,
    }


def _l2_exit(cycle: Cycle, result: TransitionResult) -> dict[str, Any]:
    warned, top = warning_summary(cycle.opt_sp.warning_inventory)
    # ``l2_*_at_entry`` fields are read by ``EscalationState.fold`` on resume —
    # they're the canonical post-fire L2 state captured by ``record_l2_fired``.
    return {
        "l2_round": cycle.escalation.l2_round,
        "l2_stall_count": cycle.escalation.l2_stall_count,
        "l2_best_accuracy_at_entry": cycle.escalation.l2_best_accuracy_at_entry,
        "l2_best_composite_fitness_at_entry": cycle.escalation.l2_best_composite_fitness_at_entry,
        "param_changes_count": len(result.opt_search_point.l1_config),
        "task_context_changed": result.task_context is not None,
        "l1_layout_changed": result.l1_layout is not None,
        "changes_description": result.opt_search_point.lineage.changes_description or "",
        "action": result.action,
        "axis_targeted": result.axis_targeted,
        "warned_queries": warned,
        "top_warning": top,
        "l2_prompt": result.debug_prompt,
        "l2_response": result.debug_response,
    }


L2 = LayerStrategy(
    layer_id="L2",
    template_name="l2_context",
    default_temperature=0.3,
    phase=CampaignPhase.REFINE_STRATEGY,
    parse=_parse_l2,
    apply=_apply_l2,
    enter_payload_fn=_l2_enter,
    exit_payload_fn=_l2_exit,
)


# ---------------------------------------------------------------------------
# L3 — modify the strategic plan (V1 minimal — plan only).
# ---------------------------------------------------------------------------


def _parse_l3(raw: dict, opt_sp: OptSearchPoint, prompt: str) -> TransitionResult:
    new_plan = raw.get("plan", opt_sp.plan) if isinstance(raw.get("plan"), str) else opt_sp.plan
    rationale = raw.get("rationale", "L3 modify_plan transition")
    raw_note = raw.get("note")
    note = raw_note if isinstance(raw_note, str) else ""
    failures = run_l3_output_validators({"plan": new_plan}, opt_sp)
    if failures:
        logger.warning(
            "L3 output failed %d validator(s): %s",
            len(failures),
            ", ".join(o.validator_id for o in failures),
        )
    return TransitionResult(
        opt_search_point=opt_sp.mutate(
            plan=new_plan, changes_description=f"L3: {rationale[:80]}", source="l3_plan"
        ),
        l3_note=note,
        l3_output_failures=failures,
        debug_prompt=prompt,
        debug_response=raw,
    )


def _apply_l3(cycle: Cycle, result: TransitionResult, round_num: int) -> None:
    # Order matters: ``_run_transition`` already ran ``copy_memory_to``
    # which carried the *prior* l3_note onto the new OSP. We overwrite it
    # with the L3 fire's output (possibly ``""`` when the LLM omitted
    # ``note``) — that's the "cleared only when L3 fires again" contract.
    cycle.opt_sp.l3_note = result.l3_note
    cycle.opt_sp.l3_output_failures = list(result.l3_output_failures)
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
    # ``l3_*_at_entry`` are read by ``EscalationState.fold`` on resume —
    # ``record_l3_fired`` also resets L2 state to these same baselines.
    return {
        "l3_round": cycle.escalation.l3_round,
        "l3_stall_count": cycle.escalation.l3_stall_count,
        "l3_best_accuracy_at_entry": cycle.escalation.l3_best_accuracy_at_entry,
        "l3_best_composite_fitness_at_entry": cycle.escalation.l3_best_composite_fitness_at_entry,
        "new_plan_preview": str(result.opt_search_point.plan)[:120],
        "changes_description": result.opt_search_point.lineage.changes_description or "",
    }


L3 = LayerStrategy(
    layer_id="L3",
    template_name="l3_plan",
    default_temperature=0.5,
    phase=CampaignPhase.MODIFY_PLAN,
    parse=_parse_l3,
    apply=_apply_l3,
    enter_payload_fn=_l3_enter,
    exit_payload_fn=_l3_exit,
)


# ---------------------------------------------------------------------------
# Action driver — escalate_l2 + _run_transition.
# ---------------------------------------------------------------------------


_TEMP_ATTR: dict[str, str] = {"L2": "l2_temperature", "L3": "l3_temperature"}


# Module-default L1 layout so apply_sweep_payload_to_osp can be invoked
# before L2 fires for the first time without importing default_l1_layout
# at every callsite.
_ = default_l1_layout  # keep import live for type-checker reachability


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
) -> Any:
    """enter → call → adopt → LayerApplied → side-effects → exit."""
    assert cycle.tracking.current_sp is not None
    client = _llm_client.get_llm_client(config.optimizer_llm.provider)
    current_pp = cycle.tracking.current_sp.pipeline_params

    emit_phase(
        on_phase, transition.phase, "enter", round=round_num, **transition.enter_payload(cycle)
    )
    async with observed_node(
        f"{transition.template_name}_r{round_num}",
        "llm/meta",
        obs=obs,
        campaign_id=tracing_campaign_id,
        round_num=round_num,
    ):
        result = await run_layer_transition(
            transition,
            cycle,
            client,
            model=config.optimizer_llm.model,
            temperature=getattr(config.optimization, _TEMP_ATTR[transition.layer_id]),
            round_num=round_num,
        )
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
    transition.apply_side_effects(cycle, result, round_num)
    emit_phase(
        on_phase,
        transition.phase,
        "exit",
        round=round_num,
        **transition.exit_payload(cycle, result),
    )
    return result


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
    """Drive an L2 (or cascading L3) escalation: observe state, fire layer,
    record divergence-gated decisions, return a stop reason or None.

    Called from the round-loop's ``FIRE_L2`` dispatch and from the mid-round
    DegradationCheck path. The decision to escalate already happened
    upstream; this driver runs the L2/L3 patience cascade (via
    ``EscalationState.observe_l2_escalation``) and the L3 force-trigger heal
    on L2 validator failures.
    """
    opt = config.optimization
    esc = cycle.escalation

    event = esc.observe_l2_escalation(
        current_composite_fitness=cycle.tracking.best_composite_fitness,
        l2_patience=opt.l2_patience,
        l3_patience=opt.l3_patience,
        enable_l3=opt.enable_l3,
    )

    # L2 trigger decision is replayed for divergence — record fired-or-not.
    l2_inputs, l2_data = _trigger_payload(
        cycle, round_num, opt.l2_patience, layer="l2", track_accuracy=True
    )
    record_decision(
        cycle.pending_decisions,
        DecisionKind.L2_ESCALATION_TRIGGER,
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
        # Loop 4: post-L2 validator failure force-triggers L3 to heal L2's
        # output. Trigger is deterministic from L2's output (rides on round_data
        # JSON), so resume reproduces it without a separate decision record.
        if cycle.opt_sp.l2_output_failures and opt.enable_l3:
            logger.info(
                "L3 force-triggered by %d L2-output validator failure(s) at round %d",
                len(cycle.opt_sp.l2_output_failures),
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

    if event.next_action == NextAction.STOP_L2_PATIENCE:
        return StopReason.L2_PATIENCE

    # FIRE_L3 or STOP_L3_PATIENCE — record L3 trigger decision either way.
    l3_inputs, l3_data = _trigger_payload(cycle, round_num, opt.l3_patience, layer="l3")
    record_decision(
        cycle.pending_decisions,
        DecisionKind.L3_ESCALATION_TRIGGER,
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
