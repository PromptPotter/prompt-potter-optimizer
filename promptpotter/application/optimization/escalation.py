"""Layer-escalation driver — L2 / L3 strategies + L1→L2→L3 fan-out.

One-way arrow: this module imports from ``cycle.py``; the reverse is
forbidden by ``tests/test_layer_imports.py``. Both layers share the same
``LayerStrategy`` shape — per-layer differences live in module-level
``_parse_*`` / ``_apply_*`` / ``_*_enter`` / ``_*_exit`` callables wired
into the ``L2`` and ``L3`` instances.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.application.optimization.cycle import NextAction
from promptpotter.application.optimization.dispatch import (
    Layer,
    build_dispatch_state,
    compile_prompt_vars,
    get_layer_configs,
)
from promptpotter.application.optimization.formatting import warning_summary
from promptpotter.application.optimization.l2_surface import compile_l2_extras
from promptpotter.application.optimization.l2_validators import run_l2_output_validators
from promptpotter.application.optimization.transitions import (
    OptimizerAction,
    TransitionResult,
    compile_l3_extras,
    run_layer_transition,
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


def apply_sweep_payload_to_osp(opt_sp: OptSearchPoint, payload: SweepPayload) -> None:
    """Merge sweep-supplied L1 surface deltas onto the OSP — same shape L2 writes."""
    if payload.l1_section_overrides:
        opt_sp.l1_section_overrides = {
            **opt_sp.l1_section_overrides,
            **payload.l1_section_overrides,
        }
    if payload.l1_section_overrides_text:
        opt_sp.l1_section_overrides_text = {
            **opt_sp.l1_section_overrides_text,
            **payload.l1_section_overrides_text,
        }
    if payload.l1_template_override:
        opt_sp.l1_template_override = payload.l1_template_override
    if payload.directive:
        opt_sp.l2_directive = payload.directive


# ---------------------------------------------------------------------------
# LayerStrategy — one shape for L2 and L3.
# ---------------------------------------------------------------------------


ExtrasFn = Callable[["Cycle", dict | None], dict[str, str]]
ParseFn = Callable[[dict, OptSearchPoint, str, dict | None], TransitionResult]
ApplyFn = Callable[["Cycle", TransitionResult, int], None]
PayloadFn = Callable[["Cycle"], dict[str, Any]]
ExitFn = Callable[["Cycle", TransitionResult], dict[str, Any]]


@dataclass(frozen=True)
class LayerStrategy:
    layer: Layer
    template_name: str
    default_temperature: float
    phase: CampaignPhase
    extras: ExtrasFn
    parse: ParseFn
    apply: ApplyFn
    enter_payload_fn: PayloadFn
    exit_payload_fn: ExitFn
    needs_candidate_scores: bool = False  # L2 reads cycle.rounds[-1].candidate_scores

    def build_compile_vars(
        self,
        cycle: Cycle,
        *,
        pipeline_params: dict | None,
        round_num: int,
        escalation_check_result: dict | None,
    ) -> dict:
        scores = (
            cycle.rounds[-1].candidate_scores
            if self.needs_candidate_scores and cycle.rounds
            else None
        )
        state = build_dispatch_state(
            self.layer,
            cycle,
            round_num=round_num,
            pipeline_schema=cycle.session.pipeline_schema,
            pipeline_params=pipeline_params,
            candidate_scores=scores,
            escalation_check_result=escalation_check_result,
        )
        return compile_prompt_vars(
            self.layer, state, cycle.opt_sp, extras=self.extras(cycle, pipeline_params)
        )

    def build_result(
        self,
        raw: dict,
        opt_sp: OptSearchPoint,
        prompt: str,
        *,
        pipeline_params: dict | None,
    ) -> TransitionResult:
        return self.parse(raw, opt_sp, prompt, pipeline_params)

    def apply_side_effects(self, cycle: Cycle, result: TransitionResult, round_num: int) -> None:
        self.apply(cycle, result, round_num)

    def enter_payload(self, cycle: Cycle) -> dict[str, Any]:
        return self.enter_payload_fn(cycle)

    def exit_payload(self, cycle: Cycle, result: TransitionResult) -> dict[str, Any]:
        return self.exit_payload_fn(cycle, result)


# ---------------------------------------------------------------------------
# L2 — refine strategy via OSP mutations.
# ---------------------------------------------------------------------------


def _parse_l2(
    raw: dict, opt_sp: OptSearchPoint, prompt: str, pipeline_params: dict | None
) -> TransitionResult:
    section_names = set(get_layer_configs()[Layer.L1_GENERATE].sections.keys())

    changes: dict[str, Any] = {
        "changes_description": f"L2: {raw.get('rationale', 'L2 refine_strategy transition')[:80]}"
    }
    if raw.get("optimizer_params"):
        changes["optimizer_params"] = {**opt_sp.optimizer_params, **raw["optimizer_params"]}

    new_task_context = None
    if isinstance(raw.get("task_context"), dict) and raw["task_context"]:
        merged = opt_sp.task_context.merge(raw["task_context"])
        if merged.to_dict() != opt_sp.task_context.to_dict():
            new_task_context = merged

    try:
        action = OptimizerAction(raw.get("action", "normal_round"))
    except ValueError:
        action = OptimizerAction.NORMAL_ROUND

    directive = raw.get("directive", "") if isinstance(raw.get("directive"), str) else ""

    def _filter_known(d: Any, kind: str) -> dict:
        if not isinstance(d, dict):
            return {}
        out: dict = {}
        for k, v in d.items():
            if k in section_names:
                out[k] = v
            else:
                logger.warning("L2 %s: ignoring unknown section %r", kind, k)
        return out

    scheme_overrides = {
        k: bool(v)
        for k, v in _filter_known(raw.get("scheme_overrides"), "scheme_overrides").items()
    }
    text_overrides = {
        k: str(v) for k, v in _filter_known(raw.get("text_overrides"), "text_overrides").items()
    }
    template_override = (
        raw.get("template_override", "") if isinstance(raw.get("template_override"), str) else ""
    )

    failures = run_l2_output_validators(
        {
            "directive": directive,
            "template_override": template_override,
            "text_overrides": dict(text_overrides),
        },
        opt_sp,
    )
    if failures:
        logger.warning(
            "L2 output failed %d validator(s): %s",
            len(failures),
            ", ".join(o.validator_id for o in failures),
        )

    return TransitionResult(
        opt_search_point=opt_sp.mutate(source="l2_context", **changes),
        task_context=new_task_context,
        l2_directive=directive,
        action=action,
        scheme_overrides=scheme_overrides,
        text_overrides=text_overrides,
        template_override=template_override,
        l2_output_failures=failures,
        debug_prompt=prompt,
        debug_response=raw,
    )


def _apply_l2(cycle: Cycle, result: TransitionResult, round_num: int) -> None:
    osp = cycle.opt_sp
    if result.task_context:
        osp.task_context = result.task_context
    osp.l2_directive = result.l2_directive
    if result.scheme_overrides:
        osp.l1_section_overrides = {**osp.l1_section_overrides, **result.scheme_overrides}
    if result.text_overrides:
        osp.l1_section_overrides_text = {**osp.l1_section_overrides_text, **result.text_overrides}
    if result.template_override:
        osp.l1_template_override = result.template_override
    osp.l2_output_failures = list(result.l2_output_failures)
    cycle.escalation.record_l2_fired(
        best_accuracy=cycle.tracking.best_accuracy,
        best_composite=cycle.tracking.best_composite,
    )

    is_probe = result.action == OptimizerAction.PROBE_ROUND
    record_decision(
        cycle.pending_decisions,
        DecisionKind.PROBE_ROUND_COMMITMENT,
        {"round_num": round_num, "l2_round": cycle.escalation.l2.round},
        is_probe,
        data={
            "action": str(result.action),
            "l2_directive_preview": (result.l2_directive or "")[:200],
            "changes_description": result.opt_search_point.lineage.changes_description or "",
        },
        round=round_num,
    )
    if is_probe:
        cycle.probe_next_round = True


def _l2_enter(cycle: Cycle) -> dict[str, Any]:
    return {
        "l2_round": cycle.escalation.l2.round,
        "l1_stall_count": cycle.escalation.l1_stall_count,
        "current_params": cycle.opt_sp.optimizer_params,
        "current_accuracy": cycle.tracking.current_accuracy,
        "best_accuracy": cycle.tracking.best_accuracy,
    }


def _l2_exit(cycle: Cycle, result: TransitionResult) -> dict[str, Any]:
    warned, top = warning_summary(cycle.opt_sp.warning_inventory)
    # ``l2_*_at_entry`` fields are read by ``EscalationState.fold`` on resume —
    # they're the canonical post-fire L2 state captured by ``record_l2_fired``.
    return {
        "l2_round": cycle.escalation.l2.round,
        "l2_stall_count": cycle.escalation.l2.stall_count,
        "l2_best_accuracy_at_entry": cycle.escalation.l2.best_accuracy_at_entry,
        "l2_best_composite_at_entry": cycle.escalation.l2.best_composite_at_entry,
        "param_changes_count": len(result.opt_search_point.optimizer_params),
        "task_context_changed": result.task_context is not None,
        "scheme_overrides_count": len(result.scheme_overrides),
        "text_overrides_count": len(result.text_overrides),
        "template_override_changed": bool(result.template_override),
        "changes_description": result.opt_search_point.lineage.changes_description or "",
        "pipeline_params_changed": result.pipeline_params is not None,
        "pipeline_params": result.pipeline_params,
        "action": result.action,
        "warned_queries": warned,
        "top_warning": top,
        "l2_prompt": result.debug_prompt,
        "l2_response": result.debug_response,
    }


L2 = LayerStrategy(
    layer=Layer.L2,
    template_name="l2_context",
    default_temperature=0.3,
    phase=CampaignPhase.REFINE_STRATEGY,
    extras=lambda c, _: compile_l2_extras(c.opt_sp),
    parse=_parse_l2,
    apply=_apply_l2,
    enter_payload_fn=_l2_enter,
    exit_payload_fn=_l2_exit,
    needs_candidate_scores=True,
)


# ---------------------------------------------------------------------------
# L3 — modify the strategic plan + optional pipeline_params deltas.
# ---------------------------------------------------------------------------


def _parse_l3(
    raw: dict, opt_sp: OptSearchPoint, prompt: str, pipeline_params: dict | None
) -> TransitionResult:
    new_plan = raw.get("plan", opt_sp.plan)
    rationale = raw.get("rationale", "L3 modify_plan transition")

    new_pp: dict | None = None
    pp_changes = raw.get("pipeline_params")
    if isinstance(pp_changes, dict) and pp_changes:
        merged = dict(pipeline_params or {})
        for key, value in pp_changes.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = {**merged[key], **value}
            else:
                merged[key] = value
        new_pp = merged

    return TransitionResult(
        opt_search_point=opt_sp.mutate(
            plan=new_plan, changes_description=f"L3: {rationale[:80]}", source="l3_plan"
        ),
        pipeline_params=new_pp,
        debug_prompt=prompt,
        debug_response=raw,
    )


def _apply_l3(cycle: Cycle, result: TransitionResult, round_num: int) -> None:
    cycle.escalation.record_l3_fired(
        best_accuracy=cycle.tracking.best_accuracy,
        best_composite=cycle.tracking.best_composite,
    )


def _l3_enter(cycle: Cycle) -> dict[str, Any]:
    return {
        "l3_round": cycle.escalation.l3.round,
        "l2_stall_count": cycle.escalation.l2.stall_count,
        "current_plan_preview": str(cycle.opt_sp.plan)[:120],
    }


def _l3_exit(cycle: Cycle, result: TransitionResult) -> dict[str, Any]:
    # ``l3_*_at_entry`` are read by ``EscalationState.fold`` on resume —
    # ``record_l3_fired`` also resets L2 state to these same baselines.
    return {
        "l3_round": cycle.escalation.l3.round,
        "l3_stall_count": cycle.escalation.l3.stall_count,
        "l3_best_accuracy_at_entry": cycle.escalation.l3.best_accuracy_at_entry,
        "l3_best_composite_at_entry": cycle.escalation.l3.best_composite_at_entry,
        "new_plan_preview": str(result.opt_search_point.plan)[:120],
        "changes_description": result.opt_search_point.lineage.changes_description or "",
        "pipeline_params_changed": result.pipeline_params is not None,
    }


L3 = LayerStrategy(
    layer=Layer.L3,
    template_name="l3_plan",
    default_temperature=0.5,
    phase=CampaignPhase.MODIFY_PLAN,
    extras=lambda c, pp: compile_l3_extras(c, pp),
    parse=_parse_l3,
    apply=_apply_l3,
    enter_payload_fn=_l3_enter,
    exit_payload_fn=_l3_exit,
)


# ---------------------------------------------------------------------------
# Action driver — escalate_l2 + _run_transition.
# ---------------------------------------------------------------------------


_TEMP_ATTR: dict[Layer, str] = {Layer.L2: "l2_temperature", Layer.L3: "l3_temperature"}


async def _run_transition(
    transition: LayerStrategy,
    cycle: Cycle,
    config: CampaignConfig,
    pipeline_schema: Any,
    round_num: int,
    on_phase: Callable[[PhaseEvent], None] | None,
    *,
    obs: ObservabilityBridge | None,
    obs_campaign_id: str,
    escalation_check_result: dict | None = None,
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
        campaign_id=obs_campaign_id,
        round_num=round_num,
    ):
        result = await run_layer_transition(
            transition,
            cycle,
            client,
            model=config.optimizer_llm.model,
            temperature=getattr(config.optimization, _TEMP_ATTR[transition.layer]),
            pipeline_params=current_pp,
            round_num=round_num,
            escalation_check_result=escalation_check_result,
        )
    new_opt = result.opt_search_point
    cycle.opt_sp.copy_memory_to(new_opt)
    cycle.opt_sp = new_opt
    cycle.tracking.current_sp = new_opt.to_job_search_point(
        base_pipeline_params=result.pipeline_params or current_pp, schema=pipeline_schema
    )
    if obs is not None:
        with graceful(f"LayerApplied({transition.layer}) emit failed"):
            obs.emit_write_point(
                LayerApplied,
                layer=transition.layer,
                campaign_id=obs_campaign_id,
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
    counter: Any,
    round_num: int,
    patience: int | None,
    *,
    layer: str,
    track_accuracy: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """(inputs_ref, data) for an L2/L3 escalation-trigger decision."""
    inputs_ref = {
        "round_num": round_num,
        f"{layer}_patience": patience,
        "entry_round": counter.round if counter.round > 0 else -1,
    }
    data: dict[str, Any] = {
        f"{layer}_round": counter.round,
        "stall_count": counter.stall_count,
        "best_composite_at_entry": counter.best_composite_at_entry,
        "best_composite_this_round": cycle.tracking.best_composite,
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
    obs_campaign_id: str = "",
    escalation_check_result: dict | None = None,
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
        current_composite=cycle.tracking.best_composite,
        l2_patience=opt.l2_patience,
        l3_patience=opt.l3_patience,
        enable_l3=opt.enable_l3,
    )

    # L2 trigger decision is replayed for divergence — record fired-or-not.
    l2_inputs, l2_data = _trigger_payload(
        cycle, esc.l2, round_num, opt.l2_patience, layer="l2", track_accuracy=True
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
            obs_campaign_id=obs_campaign_id,
            escalation_check_result=escalation_check_result,
        )
        # Loop 4: post-L2 validator failure force-triggers L3 to heal L2's
        # output. Trigger is deterministic from L2's output (rides on trial
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
                obs_campaign_id=obs_campaign_id,
            )
        return None

    if event.next_action == NextAction.STOP_L2_PATIENCE:
        return StopReason.L2_PATIENCE

    # FIRE_L3 or STOP_L3_PATIENCE — record L3 trigger decision either way.
    l3_inputs, l3_data = _trigger_payload(cycle, esc.l3, round_num, opt.l3_patience, layer="l3")
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
            obs_campaign_id=obs_campaign_id,
        )
        return None

    return StopReason.L3_PATIENCE
