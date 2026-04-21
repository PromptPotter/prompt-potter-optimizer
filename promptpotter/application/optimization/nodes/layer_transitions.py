"""L2 (refine_strategy) / L3 (modify_plan) transitions for the 3-loop feedback cycle."""

from __future__ import annotations

import enum
import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

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
from promptpotter.application.optimization.pipeline import llm_call, load_optimizer_prompt
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.shared.llm_parsing import extract_parsed_json

if TYPE_CHECKING:
    from promptpotter.application.intelligence.search_memory import SearchMemory
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.infrastructure.llm.client import LLMClientBase

logger = logging.getLogger(__name__)

__all__ = ["TransitionAction", "TransitionResult", "modify_plan", "refine_strategy"]


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


def _parse_l2_response(
    result: dict,
    opt_sp: OptSearchPoint,
    prompt: str,
) -> TransitionResult:
    """Parse L2 LLM response into a TransitionResult with derived OptSearchPoint."""
    changes: dict = {}
    if result.get("optimizer_params"):
        new_params = {**opt_sp.optimizer_params, **result["optimizer_params"]}
        changes["optimizer_params"] = new_params
    rationale = result.get("rationale", "L2 refine_strategy transition")
    changes["changes_description"] = f"L2: {rationale[:80]}"

    new_task_context = None
    if result.get("task_context") and isinstance(result["task_context"], dict):
        merged = opt_sp.task_context.merge(result["task_context"])
        if merged.to_dict() != opt_sp.task_context.to_dict():
            new_task_context = merged

    try:
        action = TransitionAction(result.get("action", "continue"))
    except ValueError:
        action = TransitionAction.CONTINUE

    l2_directive = result.get("directive", "")
    if not isinstance(l2_directive, str):
        l2_directive = ""

    logger.debug(
        "L2 refine_strategy: %d param changes, task_context %s, action=%s, directive=%d chars",
        len(result.get("optimizer_params", {})),
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
        debug_response=result,
    )


def _parse_l3_response(
    result: dict,
    opt_sp: OptSearchPoint,
    prompt: str,
    current_pipeline_params: dict | None,
) -> TransitionResult:
    """Parse L3 LLM response; merges nested pipeline_params deltas."""
    new_plan = result.get("plan", opt_sp.plan)
    rationale = result.get("rationale", "L3 modify_plan transition")

    pp_changes = result.get("pipeline_params")
    new_pipeline_params: dict | None = None
    if isinstance(pp_changes, dict) and pp_changes:
        merged = dict(current_pipeline_params or {})
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
        debug_response=result,
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
    """L2 refine_strategy: tune optimizer_params + task_context + pipeline_params."""
    escalation_section = format_escalation_report(
        escalation_check_result,
        opt_sp.memory.escalation_journal or None,
        pipeline_params,
        pipeline_schema=pipeline_schema,
    )

    task_context_section = ""
    if opt_sp.task_context:
        tc_display = {k: v for k, v in opt_sp.task_context.items() if k != "raw_description" and v}
        task_context_section = (
            "\n\nTASK CONTEXT (structured domain understanding — refine if inaccurate):\n"
            + json.dumps(tc_display, indent=2)
        )

    # Self-healing aggregate — validation (parse-time rail) + runtime (mid-eval rail).
    # Runtime labels live in a parallel list so the RuntimeFailure dicts stay pure.
    validation_failures: list[dict] = []
    runtime_failures: list[dict] = []
    runtime_failure_labels: list[str] = []
    for cs in candidate_scores or []:
        validation_failures.extend(cs.get("validation_failures") or [])
        label = cs.get("changes_description", "")
        for rf in cs.get("runtime_failures") or []:
            runtime_failures.append(rf)
            runtime_failure_labels.append(label)

    # Accumulated runtime_failures — patterns surviving L2's prior adjustments (justifies L3).
    runtime_failures_accumulated = [rf.to_dict() for rf in opt_sp.memory.runtime_failures] or None

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
        runtime_failures=runtime_failures or None,
        runtime_failure_labels=runtime_failure_labels or None,
        runtime_failures_accumulated=runtime_failures_accumulated,
    )

    compile_vars = {
        "current_params": json.dumps(opt_sp.optimizer_params),
        "task_context_section": task_context_section,
        "intelligence_sections": ("\n\n" + intelligence_sections) if intelligence_sections else "",
    }

    result, prompt = await _run_llm_transition(
        template_name="l2_refine_strategy",
        compile_vars=compile_vars,
        llm_client=llm_client,
        model=model,
        temperature=temperature,
    )
    return _parse_l2_response(result, opt_sp, prompt)


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
    """L3 modify_plan: propose new strategic plan + optional pipeline_params changes."""
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

    compile_vars = {
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

    result, prompt = await _run_llm_transition(
        template_name="l3_modify_plan",
        compile_vars=compile_vars,
        llm_client=llm_client,
        model=model,
        temperature=temperature,
    )
    return _parse_l3_response(result, opt_sp, prompt, pipeline_params)
