"""L2 (refine_strategy) / L3 (modify_plan) transitions for the 3-loop feedback cycle.

``LayerTransition`` is the shared template (``run()`` = load prompt → compile →
LLM call → parse → derive ``OptSearchPoint`` → ``TransitionResult``). Each
subclass declares its prompt template + temperature + phase as ClassVars, then
provides ``build_compile_vars``, ``build_result``, ``apply_side_effects``,
``enter_payload`` and ``exit_payload`` — the per-layer hooks the unified
orchestrator in ``layer_escalation._run_transition`` calls around the LLM step.
"""

from __future__ import annotations

import enum
import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from promptpotter.application.optimization.nodes.dispatch_msg_registry import (
    Layer,
    assemble_dispatch_msg,
)
from promptpotter.application.optimization.nodes.formatting import (
    format_pipeline_section,
    format_runtime_failures_for_l3,
    warning_summary,
)
from promptpotter.application.optimization.pipeline import run_optimizer_node
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.phases import CampaignPhase
from promptpotter.domain.search_point import TaskDecomposition

if TYPE_CHECKING:
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.infrastructure.llm.client import LLMClientBase

logger = logging.getLogger(__name__)

__all__ = [
    "L2RefineStrategy",
    "L3ModifyPlan",
    "LayerTransition",
    "TransitionAction",
    "TransitionResult",
]


class TransitionAction(enum.StrEnum):
    """What the feedback cycle should do after an L2/L3 transition."""

    CONTINUE = "continue"
    PROBE = "probe"


@dataclass
class TransitionResult:
    """L2/L3 transition result — new OptSearchPoint plus optional pipeline_params changes."""

    opt_search_point: OptSearchPoint
    pipeline_params: dict | None = None
    task_context: TaskDecomposition | None = None
    l2_directive: str = ""
    action: TransitionAction = TransitionAction.CONTINUE
    debug_prompt: str = ""
    debug_response: dict | None = None


class LayerTransition:
    """Base for an LLM-driven optimizer transition (L2 or L3).

    Subclasses set the four ClassVars (``layer``, ``template_name``,
    ``default_temperature``, ``phase``) and override the five hooks
    consumed by the orchestrator: ``build_compile_vars``,
    ``build_result``, ``apply_side_effects``, ``enter_payload``,
    ``exit_payload``. ``run()`` is the shared LLM-call template.
    """

    layer: ClassVar[Literal["L2", "L3"]]
    template_name: ClassVar[str]
    default_temperature: ClassVar[float]
    phase: ClassVar[CampaignPhase]

    async def run(
        self,
        cycle: Cycle,
        llm_client: LLMClientBase,
        *,
        model: str | None = None,
        temperature: float | None = None,
        pipeline_params: dict | None = None,
        round_num: int = 0,
        escalation_check_result: dict | None = None,
    ) -> TransitionResult:
        compile_vars = self.build_compile_vars(
            cycle,
            pipeline_params=pipeline_params,
            round_num=round_num,
            escalation_check_result=escalation_check_result,
        )
        raw, prompt = await run_optimizer_node(
            template_name=self.template_name,
            compile_vars=compile_vars,
            llm_client=llm_client,
            model=model,
            temperature=self.default_temperature if temperature is None else temperature,
            recorder=cycle.session.state.round_recorder,
        )
        return self.build_result(raw, cycle.opt_sp, prompt, pipeline_params=pipeline_params)

    def build_compile_vars(
        self,
        cycle: Cycle,
        *,
        pipeline_params: dict | None,
        round_num: int,
        escalation_check_result: dict | None,
    ) -> dict:
        raise NotImplementedError

    def build_result(
        self,
        raw: dict,
        opt_sp: OptSearchPoint,
        prompt: str,
        *,
        pipeline_params: dict | None,
    ) -> TransitionResult:
        raise NotImplementedError

    def apply_side_effects(self, cycle: Cycle, result: TransitionResult, round_num: int) -> None:
        raise NotImplementedError

    def enter_payload(self, cycle: Cycle) -> dict[str, Any]:
        raise NotImplementedError

    def exit_payload(self, cycle: Cycle, result: TransitionResult) -> dict[str, Any]:
        raise NotImplementedError


class L2RefineStrategy(LayerTransition):
    """L2: tune ``optimizer_params`` + ``task_context`` + directive (one-round window)."""

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
        opt_sp = cycle.opt_sp
        task_context_section = ""
        if opt_sp.task_context:
            tc_display = {
                k: v for k, v in opt_sp.task_context.items() if k != "raw_description" and v
            }
            task_context_section = (
                "\n\nTASK CONTEXT (structured domain understanding — refine if inaccurate):\n"
                + json.dumps(tc_display, indent=2)
            )

        candidate_scores = cycle.rounds[-1].candidate_scores if cycle.rounds else []
        dispatch_msg = assemble_dispatch_msg(
            Layer.L2,
            cycle,
            round_num=round_num,
            candidate_scores=candidate_scores,
            escalation_check_result=escalation_check_result,
            pipeline_params=pipeline_params,
        )

        return {
            "current_params": json.dumps(opt_sp.optimizer_params),
            "task_context_section": task_context_section,
            "dispatch_msg": ("\n\n" + dispatch_msg) if dispatch_msg else "",
        }

    def build_result(
        self,
        raw: dict,
        opt_sp: OptSearchPoint,
        prompt: str,
        *,
        pipeline_params: dict | None,
    ) -> TransitionResult:
        changes: dict = {}
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
            action = TransitionAction(raw.get("action", "continue"))
        except ValueError:
            action = TransitionAction.CONTINUE

        l2_directive = raw.get("directive", "")
        if not isinstance(l2_directive, str):
            l2_directive = ""

        logger.debug(
            "L2 refine_strategy: %d param changes, task_context %s, action=%s, directive=%d chars",
            len(raw.get("optimizer_params", {})),
            "updated" if new_task_context else "unchanged",
            action,
            len(l2_directive),
        )

        new_opt_sp = opt_sp.mutate(**changes) if changes else opt_sp
        return TransitionResult(
            opt_search_point=new_opt_sp,
            task_context=new_task_context,
            l2_directive=l2_directive,
            action=action,
            debug_prompt=prompt,
            debug_response=raw,
        )

    def apply_side_effects(self, cycle: Cycle, result: TransitionResult, round_num: int) -> None:
        from promptpotter.application.optimization.decisions import record_decision

        if result.task_context:
            cycle.opt_sp.task_context = result.task_context
        cycle.opt_sp.l2_directive = result.l2_directive
        cycle.escalation.l2.record_entry(cycle.best_accuracy, cycle.best_composite)

        is_probe = result.action == TransitionAction.PROBE
        record_decision(
            cycle.pending_decisions,
            "probe_round_commitment",
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
            "changes_description": result.opt_search_point.lineage.changes_description or "",
            "pipeline_params_changed": result.pipeline_params is not None,
            "pipeline_params": result.pipeline_params,
            "action": result.action,
            "warned_queries": warned_count,
            "top_warning": top_warning,
            "l2_prompt": result.debug_prompt,
            "l2_response": result.debug_response,
        }


class L3ModifyPlan(LayerTransition):
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
        opt_sp = cycle.opt_sp
        pipeline_schema = cycle.session.pipeline_schema

        # L3's "l2_history" — synthetic single-entry summary of the most recent
        # L2 round, sourced directly from cycle state.
        l2_history = [
            {
                "l2_round": cycle.escalation.l2.round,
                "optimizer_params": opt_sp.optimizer_params,
                "accuracy_change": cycle.best_composite
                - cycle.escalation.l3.best_composite_at_entry,
            }
        ]
        l2_summary = "\n".join(
            f"  L2 round {rd.get('l2_round', '?')}: "
            f"params={rd.get('parameters', {})}, "
            f"acc_change={rd.get('accuracy_change', 0):+.1%}"
            for rd in l2_history[-3:]
        )

        # Runtime failure trail — patterns L2 couldn't reduce (empty string collapses template).
        runtime_failures_section = format_runtime_failures_for_l3(
            [rf.to_dict() for rf in opt_sp.runtime_failures]
        )

        return {
            "current_plan": opt_sp.plan or "(none — default strategy)",
            "l2_summary": l2_summary,
            "rendered_prompt": opt_sp.render(),
            "pipeline_section": format_pipeline_section(pipeline_params, pipeline_schema),
            "runtime_failures_section": (
                "\n\n" + runtime_failures_section if runtime_failures_section else ""
            ),
            "dispatch_msg": assemble_dispatch_msg(Layer.L3, cycle),
        }

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
