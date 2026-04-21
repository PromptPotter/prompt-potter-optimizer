"""L2 (refine_strategy) / L3 (modify_plan) transitions for the 3-loop feedback cycle.

``LayerTransition`` is the shared template method (load prompt → compile →
LLM call → parse → derive ``OptSearchPoint`` → ``TransitionResult``). L2 and
L3 are peer subclasses: each owns its prompt template, temperature,
intelligence assembly, and result construction. Adding an L4 layer later is
a new subclass plus a single call-site wiring in ``escalation.py``.
"""

from __future__ import annotations

import enum
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from promptpotter.application.optimization.nodes.formatting import (
    assess_candidate_diversity,
    build_candidate_comparison,
    build_trajectory_report,
    format_escalation_report,
    format_l2_intelligence,
    format_pipeline_section,
    format_runtime_failures_for_l3,
    format_search_memory_block,
)
from promptpotter.application.optimization.phases import CampaignPhase
from promptpotter.application.optimization.pipeline import llm_call, load_optimizer_prompt
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.shared.llm_parsing import extract_parsed_json

if TYPE_CHECKING:
    from promptpotter.application.intelligence.search_memory import SearchMemory
    from promptpotter.application.optimization.loop_state import LoopState
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.infrastructure.llm.client import LLMClientBase

logger = logging.getLogger(__name__)

__all__ = [
    "L2RefineStrategy",
    "L3ModifyPlan",
    "LayerTransition",
    "TransitionAction",
    "TransitionResult",
    "modify_plan",
    "refine_strategy",
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


async def _run_llm_transition(
    *,
    template_name: str,
    compile_vars: dict,
    llm_client: LLMClientBase,
    model: str | None,
    temperature: float,
) -> tuple[dict, str]:
    """Shared L2/L3 plumbing: template → compile → llm_call → JSON; returns (parsed, prompt)."""
    template = load_optimizer_prompt(template_name)
    prompt = template.compile_prompt(**compile_vars)
    response = await llm_call(
        llm_client,
        messages=[{"role": "user", "content": prompt}],
        node=template_name,
        model=model,
        temperature=temperature,
        trace_meta={
            "template_name": template_name,
            "template_fields": template.prompt_field_dict(),
            "variables": compile_vars,
        },
    )
    return extract_parsed_json(response), prompt


class LayerTransition(ABC):
    """Template method for an LLM-driven optimizer transition (L2 or L3).

    Subclasses declare layer metadata as class-level constants and override
    ``assemble_intelligence`` (prompt compile-vars) and ``build_result``
    (``TransitionResult`` from the raw LLM JSON). ``run`` is the shared
    template method: assemble → LLM → build result.
    """

    layer: ClassVar[Literal["L2", "L3"]]
    template_name: ClassVar[str]
    default_temperature: ClassVar[float]
    phase: ClassVar[CampaignPhase]

    async def run(
        self,
        opt_sp: OptSearchPoint,
        llm_client: LLMClientBase,
        *,
        model: str | None = None,
        temperature: float | None = None,
        pipeline_params: dict | None = None,
        pipeline_schema: PipelineSchema | None = None,
        search_memory: SearchMemory | None = None,
        **ctx: Any,
    ) -> TransitionResult:
        compile_vars = self.assemble_intelligence(
            opt_sp,
            pipeline_params=pipeline_params,
            pipeline_schema=pipeline_schema,
            search_memory=search_memory,
            **ctx,
        )
        raw, prompt = await _run_llm_transition(
            template_name=self.template_name,
            compile_vars=compile_vars,
            llm_client=llm_client,
            model=model,
            temperature=self.default_temperature if temperature is None else temperature,
        )
        return self.build_result(raw, opt_sp, prompt, pipeline_params=pipeline_params)

    @abstractmethod
    def assemble_intelligence(self, opt_sp: OptSearchPoint, **ctx: Any) -> dict:
        """Build the compile-vars dict for the layer's prompt template."""

    @abstractmethod
    def build_result(
        self,
        raw: dict,
        opt_sp: OptSearchPoint,
        prompt: str,
        *,
        pipeline_params: dict | None,
    ) -> TransitionResult:
        """Convert raw LLM JSON response into a ``TransitionResult``."""

    @abstractmethod
    def apply_side_effects(
        self, state: LoopState, result: TransitionResult, round_num: int
    ) -> None:
        """Mutate ``LoopState`` post-transition (record escalation entry, reset counters, ...).

        Called by the escalation orchestrator after ``state.adopt_transition``
        has applied the result's OptSearchPoint + pipeline_params. Runs the
        layer-specific tail (L2: l2_directive, probe-round decision; L3:
        record_entry + reset_for_l3).
        """


class L2RefineStrategy(LayerTransition):
    """L2: tune ``optimizer_params`` + ``task_context`` + directive (one-round window)."""

    layer: ClassVar[Literal["L2", "L3"]] = "L2"
    template_name: ClassVar[str] = "l2_refine_strategy"
    default_temperature: ClassVar[float] = 0.3
    phase: ClassVar[CampaignPhase] = CampaignPhase.REFINE_STRATEGY

    def assemble_intelligence(self, opt_sp: OptSearchPoint, **ctx: Any) -> dict:
        pipeline_params = ctx.get("pipeline_params")
        pipeline_schema = ctx.get("pipeline_schema")
        search_memory = ctx.get("search_memory")
        escalation_check_result = ctx.get("escalation_check_result")
        rounds = ctx.get("rounds")
        candidate_scores = ctx.get("candidate_scores")
        current_round_num = int(ctx.get("round_num", 0))

        escalation_section = format_escalation_report(
            escalation_check_result,
            opt_sp.memory.escalation_journal or None,
            pipeline_params,
            pipeline_schema=pipeline_schema,
        )

        task_context_section = ""
        if opt_sp.task_context:
            tc_display = {
                k: v for k, v in opt_sp.task_context.items() if k != "raw_description" and v
            }
            task_context_section = (
                "\n\nTASK CONTEXT (structured domain understanding — refine if inaccurate):\n"
                + json.dumps(tc_display, indent=2)
            )

        # Validation failures (parse-time rail) still arrive per-round via candidate_scores
        # — they're synthetic-0'd and not mirrored to outer memory.
        validation_failures: list[dict] = []
        for cs in candidate_scores or []:
            validation_failures.extend(cs.get("validation_failures") or [])

        # Runtime failures (mid-eval rail) are mirrored onto outer memory in
        # execute_round BEFORE L2 fires; one list, partitioned at format time
        # by first_seen_round.
        runtime_failures = [rf.to_dict() for rf in opt_sp.memory.runtime_failures] or None

        intelligence_sections = format_l2_intelligence(
            escalation_section=escalation_section,
            warning_inventory=opt_sp.memory.warning_inventory or None,
            critique_text=opt_sp.memory.critique_text,
            l2_directive=opt_sp.memory.l2_directive,
            search_memory_digest=(
                search_memory.to_strategic_digest(include_correlations=True)
                if search_memory is not None
                else None
            ),
            trajectory=build_trajectory_report(rounds) if rounds else None,
            candidate_comparison=(
                build_candidate_comparison(candidate_scores) if candidate_scores else None
            ),
            diversity_alert=assess_candidate_diversity(rounds) if rounds else None,
            validation_failures=validation_failures or None,
            runtime_failures=runtime_failures,
            current_round_num=current_round_num,
        )

        return {
            "current_params": json.dumps(opt_sp.optimizer_params),
            "task_context_section": task_context_section,
            "intelligence_sections": (
                ("\n\n" + intelligence_sections) if intelligence_sections else ""
            ),
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

        new_opt_sp = opt_sp.derive_candidate(**changes) if changes else opt_sp
        return TransitionResult(
            opt_search_point=new_opt_sp,
            task_context=new_task_context,
            l2_directive=l2_directive,
            action=action,
            debug_prompt=prompt,
            debug_response=raw,
        )

    def apply_side_effects(
        self, state: LoopState, result: TransitionResult, round_num: int
    ) -> None:
        from promptpotter.application.campaign.decisions import record_decision

        if result.task_context:
            state.opt_sp.task_context = result.task_context
        state.opt_sp.memory.l2_directive = result.l2_directive
        state.escalation.l2.record_entry(state.best_accuracy, state.best_composite)

        is_probe = result.action == TransitionAction.PROBE
        record_decision(
            state.pending_decisions,
            "probe_round_commitment",
            {
                "round_num": round_num,
                "l2_round": state.escalation.l2.round,
            },
            is_probe,
            data={
                "action": str(result.action),
                "l2_directive_preview": (result.l2_directive or "")[:200],
                "changes_description": result.opt_search_point.changes_description or "",
            },
        )
        if is_probe:
            state.probe_next_round = True
            logger.debug("L2 requested probe — next round uses warned queries")
        logger.debug(
            "L2 refine_strategy at round %d (l2_round=%d)", round_num, state.escalation.l2.round
        )


class L3ModifyPlan(LayerTransition):
    """L3: propose a new strategic plan + optional pipeline_params deltas."""

    layer: ClassVar[Literal["L2", "L3"]] = "L3"
    template_name: ClassVar[str] = "l3_modify_plan"
    default_temperature: ClassVar[float] = 0.5
    phase: ClassVar[CampaignPhase] = CampaignPhase.MODIFY_PLAN

    def assemble_intelligence(self, opt_sp: OptSearchPoint, **ctx: Any) -> dict:
        pipeline_params = ctx.get("pipeline_params")
        pipeline_schema = ctx.get("pipeline_schema")
        search_memory = ctx.get("search_memory")
        l2_history = ctx.get("l2_history") or []

        l2_summary = "\n".join(
            f"  L2 round {rd.get('l2_round', '?')}: "
            f"params={rd.get('parameters', {})}, "
            f"acc_change={rd.get('accuracy_change', 0):+.1%}"
            for rd in l2_history[-3:]
        )

        # Runtime failure trail — patterns L2 couldn't reduce (empty string collapses template).
        runtime_failures_section = format_runtime_failures_for_l3(
            [rf.to_dict() for rf in opt_sp.memory.runtime_failures]
        )

        return {
            "current_plan": opt_sp.plan or "(none — default strategy)",
            "l2_summary": l2_summary,
            "rendered_prompt": opt_sp.render(),
            "pipeline_section": format_pipeline_section(pipeline_params, pipeline_schema),
            "runtime_failures_section": (
                "\n\n" + runtime_failures_section if runtime_failures_section else ""
            ),
            "intelligence_section": format_search_memory_block(
                search_memory.to_strategic_digest(include_clusters=True)
                if search_memory is not None
                else None,
                {
                    "axis_rankings": "Axis impact rankings",
                    "bottleneck_distribution": "Bottleneck distribution",
                    "failure_clusters": "Failure clusters",
                    "persistent_failures": "Persistent failures",
                },
            ),
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

        new_opt_sp = opt_sp.derive_candidate(
            plan=new_plan,
            changes_description=f"L3: {rationale[:80]}",
        )
        return TransitionResult(
            opt_search_point=new_opt_sp,
            pipeline_params=new_pipeline_params,
            debug_prompt=prompt,
            debug_response=raw,
        )

    def apply_side_effects(
        self, state: LoopState, result: TransitionResult, round_num: int
    ) -> None:
        state.escalation.l3.record_entry(state.best_accuracy, state.best_composite)
        state.escalation.reset_for_l3(state.best_accuracy, state.best_composite)
        logger.debug(
            "L3 modify_plan at round %d (l3_round=%d)", round_num, state.escalation.l3.round
        )


async def refine_strategy(
    opt_sp: OptSearchPoint,
    llm_client: LLMClientBase,
    model: str | None = None,
    temperature: float = 0.3,
    pipeline_params: dict | None = None,
    pipeline_schema: PipelineSchema | None = None,
    escalation_check_result: dict | None = None,
    search_memory: SearchMemory | None = None,
    rounds: list | None = None,
    candidate_scores: list[dict] | None = None,
) -> TransitionResult:
    """L2 refine_strategy shim — delegates to ``L2RefineStrategy().run(...)``."""
    return await L2RefineStrategy().run(
        opt_sp,
        llm_client,
        model=model,
        temperature=temperature,
        pipeline_params=pipeline_params,
        pipeline_schema=pipeline_schema,
        search_memory=search_memory,
        escalation_check_result=escalation_check_result,
        rounds=rounds,
        candidate_scores=candidate_scores,
    )


async def modify_plan(
    opt_sp: OptSearchPoint,
    l2_history: list[dict],
    llm_client: LLMClientBase,
    model: str | None = None,
    temperature: float = 0.5,
    pipeline_params: dict | None = None,
    pipeline_schema: PipelineSchema | None = None,
    search_memory: SearchMemory | None = None,
) -> TransitionResult:
    """L3 modify_plan shim — delegates to ``L3ModifyPlan().run(...)``."""
    return await L3ModifyPlan().run(
        opt_sp,
        llm_client,
        model=model,
        temperature=temperature,
        pipeline_params=pipeline_params,
        pipeline_schema=pipeline_schema,
        search_memory=search_memory,
        l2_history=l2_history,
    )
