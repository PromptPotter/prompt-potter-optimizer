"""Layer-escalation driver — L1→L2→L3 fan-out + the L2/L3 strategy classes.

Three responsibilities, one direction of dependency (this module imports
``cycle.py``; ``cycle.py`` does not import back):

1. **Strategy classes** (``L2RefineStrategy``, ``L3ModifyPlan``) — bundle
   per-layer ``build_compile_vars`` (call ``compile_l2_surface`` /
   L3 extras), ``build_result`` (parse the LLM response into a
   :class:`TransitionResult`), ``apply_side_effects`` (mutate the cycle:
   write to ``cycle.opt_sp.*`` + ``cycle.escalation.*`` + push decisions),
   and ``enter_payload`` / ``exit_payload`` (audit-trail summaries).

2. **Action driver** (``escalate_l2``, ``_run_transition``,
   ``apply_sweep_payload_to_osp``) — fires L2 (and optionally L3) when
   patience exhausts; threads the strategy through ``run_layer_transition``,
   adopts the result onto the cycle, emits a ``LayerApplied`` write-point,
   then calls ``apply_side_effects``.

3. **Sweep payload application** (``apply_sweep_payload_to_osp``) — merges
   operator-supplied L1-surface deltas onto a fresh OSP at fork start
   (same shape as ``L2RefineStrategy.apply_side_effects`` writes).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from promptpotter.application.optimization import cycle as _cycle_mod
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
from promptpotter.domain.run_records import DecisionKind, SweepPayload
from promptpotter.infrastructure import llm as _llm_client
from promptpotter.infrastructure.tracing import LayerApplied, observed_node
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.application.config import CampaignConfig
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.infrastructure.tracing import ObservabilityBridge

logger = logging.getLogger(__name__)

__all__ = [
    "L2RefineStrategy",
    "L3ModifyPlan",
    "apply_sweep_payload_to_osp",
    "escalate_l2",
]


# ---------------------------------------------------------------------------
# Sweep payload — operator-driven L1 surface delta applied at fork start.
# ---------------------------------------------------------------------------


def apply_sweep_payload_to_osp(opt_sp: OptSearchPoint, payload: SweepPayload) -> None:
    """Merge sweep-supplied L1 surface deltas onto the OSP — same shape ``L2RefineStrategy.apply_side_effects`` writes."""
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
# L2 strategy — refine the optimizer's strategy by writing onto OptSearchPoint.
# ---------------------------------------------------------------------------


class L2RefineStrategy:
    """L2: refine the optimizer's strategy by writing onto ``OptSearchPoint``.

    L2 fires on L1 stall (per ``l1_patience``). When it fires it can write
    any combination of: a strategic directive, optimizer params, task
    context, L1-surface section/text/template overrides. It also chooses
    whether the next round is a normal round or a probe round
    (:class:`OptimizerAction`). Fields not set in L2's output are left
    untouched on the OSP.
    """

    layer: ClassVar[Literal["L2", "L3"]] = "L2"
    template_name: ClassVar[str] = "l2_context"
    default_temperature: ClassVar[float] = 0.3
    phase: ClassVar[CampaignPhase] = CampaignPhase.REFINE_STRATEGY

    def build_compile_vars(
        self,
        cycle: Cycle,
        *,
        pipeline_params: dict | None,
        round_num: int,
        escalation_check_result: dict | None,
    ) -> dict:
        candidate_scores = cycle.rounds[-1].candidate_scores if cycle.rounds else []
        state = build_dispatch_state(
            Layer.L2,
            cycle,
            round_num=round_num,
            pipeline_schema=cycle.session.pipeline_schema,
            pipeline_params=pipeline_params,
            candidate_scores=candidate_scores,
            escalation_check_result=escalation_check_result,
        )
        return compile_prompt_vars(
            Layer.L2,
            state,
            cycle.opt_sp,
            extras=compile_l2_extras(cycle.opt_sp),
        )

    def build_result(
        self,
        raw: dict,
        opt_sp: OptSearchPoint,
        prompt: str,
        *,
        pipeline_params: dict | None,
    ) -> TransitionResult:
        section_names = set(get_layer_configs()[Layer.L1_GENERATE].sections.keys())

        changes: dict[str, Any] = {}
        if raw.get("optimizer_params"):
            new_params = {**opt_sp.optimizer_params, **raw["optimizer_params"]}
            changes["optimizer_params"] = new_params
        rationale = raw.get("rationale", "L2 refine_strategy transition")
        changes["changes_description"] = f"L2: {rationale[:80]}"

        new_task_context = None
        if raw.get("task_context") and isinstance(raw["task_context"], dict):
            merged = opt_sp.task_context.merge(raw["task_context"])
            if merged.to_dict() != opt_sp.task_context.to_dict():
                new_task_context = merged

        try:
            action = OptimizerAction(raw.get("action", "normal_round"))
        except ValueError:
            action = OptimizerAction.NORMAL_ROUND

        l2_directive = raw.get("directive", "")
        if not isinstance(l2_directive, str):
            l2_directive = ""

        scheme_overrides_raw = raw.get("scheme_overrides") or {}
        text_overrides_raw = raw.get("text_overrides") or {}
        scheme_overrides: dict[str, bool] = {}
        text_overrides: dict[str, str] = {}
        if isinstance(scheme_overrides_raw, dict):
            for k, v in scheme_overrides_raw.items():
                if k in section_names:
                    scheme_overrides[k] = bool(v)
                else:
                    logger.warning("L2 scheme_overrides: ignoring unknown section %r", k)
        if isinstance(text_overrides_raw, dict):
            for k, v in text_overrides_raw.items():
                if k in section_names:
                    text_overrides[k] = str(v)
                else:
                    logger.warning("L2 text_overrides: ignoring unknown section %r", k)

        template_override = raw.get("template_override", "")
        if not isinstance(template_override, str):
            template_override = ""

        # Loop 4 — validate L2's parsed output. Build a normalized snapshot
        # so validators see the cleaned, type-checked values (not raw LLM
        # JSON which may contain stray non-string entries that would crash
        # text scans).
        validator_input: dict[str, Any] = {
            "directive": l2_directive,
            "template_override": template_override,
            "text_overrides": dict(text_overrides),
        }
        l2_output_failures = run_l2_output_validators(validator_input, opt_sp)
        if l2_output_failures:
            logger.warning(
                "L2 output failed %d validator(s): %s",
                len(l2_output_failures),
                ", ".join(o.validator_id for o in l2_output_failures),
            )

        logger.debug(
            "L2 refine_strategy: %d param changes, task_context %s, action=%s, "
            "directive=%d chars, scheme_overrides=%d, text_overrides=%d, template_override=%d chars",
            len(raw.get("optimizer_params", {})),
            "updated" if new_task_context else "unchanged",
            action,
            len(l2_directive),
            len(scheme_overrides),
            len(text_overrides),
            len(template_override),
        )

        new_opt_sp = opt_sp.mutate(source="l2_context", **changes) if changes else opt_sp
        return TransitionResult(
            opt_search_point=new_opt_sp,
            task_context=new_task_context,
            l2_directive=l2_directive,
            action=action,
            scheme_overrides=scheme_overrides,
            text_overrides=text_overrides,
            template_override=template_override,
            l2_output_failures=l2_output_failures,
            debug_prompt=prompt,
            debug_response=raw,
        )

    def apply_side_effects(self, cycle: Cycle, result: TransitionResult, round_num: int) -> None:
        if result.task_context:
            cycle.opt_sp.task_context = result.task_context
        cycle.opt_sp.l2_directive = result.l2_directive
        if result.scheme_overrides:
            cycle.opt_sp.l1_section_overrides = {
                **cycle.opt_sp.l1_section_overrides,
                **result.scheme_overrides,
            }
        if result.text_overrides:
            cycle.opt_sp.l1_section_overrides_text = {
                **cycle.opt_sp.l1_section_overrides_text,
                **result.text_overrides,
            }
        if result.template_override:
            cycle.opt_sp.l1_template_override = result.template_override
        cycle.opt_sp.l2_output_failures = list(result.l2_output_failures)
        cycle.escalation.l2.record_entry(cycle.best_accuracy, cycle.best_composite)

        is_probe = result.action == OptimizerAction.PROBE_ROUND
        _cycle_mod.record_decision(
            cycle.pending_decisions,
            DecisionKind.PROBE_ROUND_COMMITMENT,
            {
                "round_num": round_num,
                "l2_round": cycle.escalation.l2.round,
            },
            is_probe,
            data={
                "action": str(result.action),
                "l2_directive_preview": (result.l2_directive or "")[:200],
                "changes_description": result.opt_search_point.lineage.changes_description or "",
            },
        )
        if is_probe:
            cycle.probe_next_round = True
            logger.debug("L2 requested probe — next round uses warned queries")
        logger.debug(
            "L2 refine_strategy at round %d (l2_round=%d)", round_num, cycle.escalation.l2.round
        )

    def enter_payload(self, cycle: Cycle) -> dict[str, Any]:
        return {
            "l2_round": cycle.escalation.l2.round,
            "l1_stall_count": cycle.escalation.l1_stall_count,
            "current_params": cycle.opt_sp.optimizer_params,
            "current_accuracy": cycle.current_accuracy,
            "best_accuracy": cycle.best_accuracy,
        }

    def exit_payload(self, cycle: Cycle, result: TransitionResult) -> dict[str, Any]:
        warned_count, top_warning = warning_summary(cycle.opt_sp.warning_inventory)
        return {
            "l2_round": cycle.escalation.l2.round,
            "param_changes_count": len(result.opt_search_point.optimizer_params),
            "task_context_changed": result.task_context is not None,
            "scheme_overrides_count": len(result.scheme_overrides),
            "text_overrides_count": len(result.text_overrides),
            "template_override_changed": bool(result.template_override),
            "changes_description": result.opt_search_point.lineage.changes_description or "",
            "pipeline_params_changed": result.pipeline_params is not None,
            "pipeline_params": result.pipeline_params,
            "action": result.action,
            "warned_queries": warned_count,
            "top_warning": top_warning,
            "l2_prompt": result.debug_prompt,
            "l2_response": result.debug_response,
        }


# ---------------------------------------------------------------------------
# L3 strategy — propose a new strategic plan + optional pipeline_params deltas.
# ---------------------------------------------------------------------------


class L3ModifyPlan:
    """L3: propose a new strategic plan + optional pipeline_params deltas."""

    layer: ClassVar[Literal["L2", "L3"]] = "L3"
    template_name: ClassVar[str] = "l3_plan"
    default_temperature: ClassVar[float] = 0.5
    phase: ClassVar[CampaignPhase] = CampaignPhase.MODIFY_PLAN

    def build_compile_vars(
        self,
        cycle: Cycle,
        *,
        pipeline_params: dict | None,
        round_num: int,
        escalation_check_result: dict | None,
    ) -> dict:
        state = build_dispatch_state(
            Layer.L3,
            cycle,
            round_num=round_num,
            pipeline_schema=cycle.session.pipeline_schema,
            pipeline_params=pipeline_params,
            escalation_check_result=escalation_check_result,
        )
        return compile_prompt_vars(
            Layer.L3,
            state,
            cycle.opt_sp,
            extras=compile_l3_extras(cycle, pipeline_params),
        )

    def build_result(
        self,
        raw: dict,
        opt_sp: OptSearchPoint,
        prompt: str,
        *,
        pipeline_params: dict | None,
    ) -> TransitionResult:
        new_plan = raw.get("plan", opt_sp.plan)
        rationale = raw.get("rationale", "L3 modify_plan transition")

        pp_changes = raw.get("pipeline_params")
        new_pipeline_params: dict | None = None
        if isinstance(pp_changes, dict) and pp_changes:
            merged = dict(pipeline_params or {})
            for key, value in pp_changes.items():
                if isinstance(value, dict) and isinstance(merged.get(key), dict):
                    merged[key] = {**merged[key], **value}
                else:
                    merged[key] = value
            new_pipeline_params = merged

        logger.debug(
            "L3 modify_plan: %s, pipeline_params %s",
            rationale[:100],
            "updated" if new_pipeline_params else "unchanged",
        )

        new_opt_sp = opt_sp.mutate(
            plan=new_plan,
            changes_description=f"L3: {rationale[:80]}",
            source="l3_plan",
        )
        return TransitionResult(
            opt_search_point=new_opt_sp,
            pipeline_params=new_pipeline_params,
            debug_prompt=prompt,
            debug_response=raw,
        )

    def apply_side_effects(self, cycle: Cycle, result: TransitionResult, round_num: int) -> None:
        cycle.escalation.l3.record_entry(cycle.best_accuracy, cycle.best_composite)
        cycle.escalation.reset_for_l3(cycle.best_accuracy, cycle.best_composite)
        logger.debug(
            "L3 modify_plan at round %d (l3_round=%d)", round_num, cycle.escalation.l3.round
        )

    def enter_payload(self, cycle: Cycle) -> dict[str, Any]:
        return {
            "l3_round": cycle.escalation.l3.round,
            "l2_stall_count": cycle.escalation.l2.stall_count,
            "current_plan_preview": str(cycle.opt_sp.plan)[:120],
        }

    def exit_payload(self, cycle: Cycle, result: TransitionResult) -> dict[str, Any]:
        return {
            "l3_round": cycle.escalation.l3.round,
            "new_plan_preview": str(result.opt_search_point.plan)[:120],
            "changes_description": result.opt_search_point.lineage.changes_description or "",
            "pipeline_params_changed": result.pipeline_params is not None,
        }


# ---------------------------------------------------------------------------
# Action driver — escalate_l2 + _run_transition.
# ---------------------------------------------------------------------------


_TEMP_ATTR: dict[str, str] = {"L2": "l2_temperature", "L3": "l3_temperature"}


async def _run_transition(
    transition: L2RefineStrategy | L3ModifyPlan,
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
    """Unified L2/L3 orchestrator: enter → call → adopt → LayerApplied → side-effects → exit."""
    assert cycle.current_sp is not None
    client = _llm_client.get_llm_client(config.optimizer_llm.provider)
    current_pp = cycle.current_sp.pipeline_params

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
    cycle.current_sp = new_opt.to_job_search_point(
        base_pipeline_params=result.pipeline_params or current_pp,
        schema=pipeline_schema,
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
    """L1→L2 (and optional L2→L3) escalation; vanilla patience-exhausts → next layer / stop."""
    opt = config.optimization
    esc = cycle.escalation
    esc.l2.record_outcome(cycle.best_composite)

    l2_stalled = opt.l2_patience is not None and esc.l2.stall_count >= opt.l2_patience
    # entry_round = round whose rescored best_composite is the stall baseline (-1 = never fired).
    entry_round_l2 = esc.l2.round if esc.l2.round > 0 else -1
    _cycle_mod.record_decision(
        cycle.pending_decisions,
        DecisionKind.L2_ESCALATION_TRIGGER,
        {
            "round_num": round_num,
            "l2_patience": opt.l2_patience,
            "entry_round": entry_round_l2,
        },
        not l2_stalled,
        data={
            "l2_round": esc.l2.round,
            "stall_count": esc.l2.stall_count,
            "best_composite_at_entry": esc.l2.best_composite_at_entry,
            "best_composite_this_round": cycle.best_composite,
            "best_accuracy": cycle.best_accuracy,
        },
    )
    if not l2_stalled:
        await _run_transition(
            L2RefineStrategy(),
            cycle,
            config,
            pipeline_schema,
            round_num,
            on_phase,
            obs=obs,
            obs_campaign_id=obs_campaign_id,
            escalation_check_result=escalation_check_result,
        )

        # Loop 4 — post-L2 validator force: if L2 produced broken output,
        # L3 fires immediately to heal L2, bypassing l2_patience and
        # l3_patience. The trigger is deterministic from L2's output (which
        # itself rides on the trial JSON), so resume reproduces it without
        # needing a separate decision record.
        if cycle.opt_sp.l2_output_failures and opt.enable_l3:
            logger.info(
                "L3 force-triggered by %d L2-output validator failure(s) at round %d",
                len(cycle.opt_sp.l2_output_failures),
                round_num,
            )
            await _run_transition(
                L3ModifyPlan(),
                cycle,
                config,
                pipeline_schema,
                round_num,
                on_phase,
                obs=obs,
                obs_campaign_id=obs_campaign_id,
            )
        return None

    if not opt.enable_l3:
        logger.debug("L2 patience exhausted (%d stalls) at round %d", esc.l2.stall_count, round_num)
        return StopReason.L2_PATIENCE

    esc.l3.record_outcome(cycle.best_composite)
    l3_exhausted = opt.l3_patience is not None and esc.l3.stall_count >= opt.l3_patience
    entry_round_l3 = esc.l3.round if esc.l3.round > 0 else -1
    _cycle_mod.record_decision(
        cycle.pending_decisions,
        DecisionKind.L3_ESCALATION_TRIGGER,
        {
            "round_num": round_num,
            "l3_patience": opt.l3_patience,
            "entry_round": entry_round_l3,
        },
        not l3_exhausted,
        data={
            "l3_round": esc.l3.round,
            "stall_count": esc.l3.stall_count,
            "best_composite_at_entry": esc.l3.best_composite_at_entry,
            "best_composite_this_round": cycle.best_composite,
        },
    )
    if not l3_exhausted:
        await _run_transition(
            L3ModifyPlan(),
            cycle,
            config,
            pipeline_schema,
            round_num,
            on_phase,
            obs=obs,
            obs_campaign_id=obs_campaign_id,
        )
        return None

    logger.debug("L3 patience exhausted (%d stalls) at round %d", esc.l3.stall_count, round_num)
    return StopReason.L3_PATIENCE
