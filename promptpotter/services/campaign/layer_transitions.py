"""Layer transition functions for the 3-loop feedback cycle.

L2 (refine_context): Analyzes L1 failure patterns and adjusts OptSearchPoint
    parameters and context to improve generation quality.

L3 (modify_plan): Analyzes why L2 adjustments didn't help and suggests
    a new strategic plan.
"""

from __future__ import annotations

import enum
import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from promptpotter.config.optimizer_pipeline import llm_call
from promptpotter.config.optimizer_prompt_loader import load_optimizer_prompt
from promptpotter.models.opt_search_point import OptSearchPoint
from promptpotter.services.campaign.formatting import (
    L2IntelligenceData,
    build_l2_search_memory_context,
    format_l2_intelligence,
)
from promptpotter.shared.llm_parsing import extract_parsed_json

if TYPE_CHECKING:
    from promptpotter.models.opt_search_point import PromptTemplate
    from promptpotter.models.pipeline_schema import PipelineSchema
    from promptpotter.services.llm_client import LLMClientBase

logger = logging.getLogger(__name__)

__all__ = ["TransitionAction", "TransitionResult", "modify_plan", "refine_context"]


class TransitionAction(enum.StrEnum):
    """What the feedback cycle should do after an L2/L3 transition."""

    CONTINUE = "continue"
    PROBE = "probe"


@dataclass
class TransitionResult:
    """Return value from L2/L3 transitions.

    Bundles the new OptSearchPoint with optional pipeline_params changes,
    keeping both dimensions of the search space in one result.
    """

    opt_search_point: OptSearchPoint
    pipeline_params: dict | None = None
    task_context: dict | None = None
    l2_directive: str = ""
    action: TransitionAction = TransitionAction.CONTINUE
    debug_prompt: str = ""
    debug_response: dict | None = None


def _build_l2_prompt(
    opt_sp: OptSearchPoint,
    pipeline_params: dict | None,
    pipeline_schema: PipelineSchema | None,
    escalation_context: dict | None,
    search_memory: object | None = None,
) -> tuple[str, PromptTemplate, dict]:
    """Assemble the L2 refine_context prompt from all context sources.

    Returns (compiled_prompt, template, compile_variables).
    """
    escalation_section = _build_escalation_prompt_section(
        escalation_context,
        opt_sp.escalation_journal or None,
        pipeline_params,
        pipeline_schema=pipeline_schema,
    )

    task_context_section = ""
    if opt_sp.task_context:
        # Filter raw_description — already digested into structured fields
        tc_display = {k: v for k, v in opt_sp.task_context.items() if k != "raw_description"}
        task_context_section = (
            "\n\nTASK CONTEXT (structured domain understanding — refine if inaccurate):\n"
            + json.dumps(tc_display, indent=2)
        )

    intelligence_sections = format_l2_intelligence(
        L2IntelligenceData(
            escalation_section=escalation_section,
            warning_inventory=opt_sp.warning_inventory or None,
            critique_text=opt_sp.critique_text,
            l2_directive=opt_sp.l2_directive,
            search_memory_context=build_l2_search_memory_context(search_memory),
        )
    )

    response_schema_suffix = (
        "\nReturn a JSON object with:\n"
        '  "optimizer_params": dict of meta-setting changes '
        "(creativity, n_variants, sample_size — or {} to keep current)\n"
        '  "task_context": dict of refined domain fields (or {} to keep current)\n'
        '  "action": "continue" (normal L1 cycle) or "probe" '
        "(test warned queries with new settings first)\n"
        '  "directive": 2-3 sentence strategic guidance for the next candidate '
        "generator — what problem to solve and why, not specific parameter "
        "values (L1 decides those)\n"
        '  "rationale": 1-2 sentence explanation\n'
        "\nNote: L1 Generate makes the final decision on pipeline_params. "
        "Your job is to refine the "
        "situation context and meta-settings so L1 makes better choices.\n"
        'Use your judgment on when to set action to "probe" vs "continue" '
        "based on the data above."
    )

    _compile_vars = {
        "current_params": json.dumps(opt_sp.optimizer_params),
        "task_context_section": task_context_section,
        "intelligence_sections": ("\n\n" + intelligence_sections) if intelligence_sections else "",
        "response_schema_suffix": response_schema_suffix,
    }
    _template = load_optimizer_prompt("l2_refine_context")
    return _template.compile_prompt(**_compile_vars), _template, _compile_vars


def _parse_l2_response(
    result: dict,
    opt_sp: OptSearchPoint,
    prompt: str,
) -> TransitionResult:
    """Parse LLM response into a TransitionResult with derived OptSearchPoint."""
    changes: dict = {}
    if result.get("optimizer_params"):
        new_params = {**opt_sp.optimizer_params, **result["optimizer_params"]}
        changes["optimizer_params"] = new_params
    rationale = result.get("rationale", "L2 refine_context transition")
    changes["changes_description"] = f"L2: {rationale[:80]}"

    new_task_context = None
    if result.get("task_context") and isinstance(result["task_context"], dict):
        merged = {**(opt_sp.task_context or {}), **result["task_context"]}
        if merged != (opt_sp.task_context or {}):
            new_task_context = merged

    try:
        action = TransitionAction(result.get("action", "continue"))
    except ValueError:
        action = TransitionAction.CONTINUE

    l2_directive = result.get("directive", "")
    if not isinstance(l2_directive, str):
        l2_directive = ""

    logger.debug(
        "L2 refine_context: %d param changes, task_context %s, action=%s, directive=%d chars",
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


async def refine_context(
    opt_sp: OptSearchPoint,
    llm_client: LLMClientBase,
    model: str | None = None,
    temperature: float = 0.3,
    pipeline_params: dict | None = None,
    pipeline_schema: PipelineSchema | None = None,
    escalation_context: dict | None = None,
    search_memory: object | None = None,
) -> TransitionResult:
    """LLM-driven L2 adjustment: tune parameters, context, and pipeline params.

    Analyzes critique findings and recommends changes to ``parameters``
    (creativity, n_variants, variant_strategy), ``context`` (domain grounding
    text), and optionally ``pipeline_params`` when a pipeline schema is
    available.
    """
    prompt, _template, _compile_vars = _build_l2_prompt(
        opt_sp,
        pipeline_params,
        pipeline_schema,
        escalation_context,
        search_memory=search_memory,
    )

    response = await llm_call(
        llm_client,
        messages=[{"role": "user", "content": prompt}],
        node="l2_refine_context",
        model=model,
        temperature=temperature,
        trace_meta={
            "template_name": "l2_refine_context",
            "template_fields": _template.prompt_field_dict(),
            "variables": _compile_vars,
        },
    )

    return _parse_l2_response(extract_parsed_json(response), opt_sp, prompt)


async def modify_plan(
    opt_sp: OptSearchPoint,
    l2_history: list[dict],
    llm_client: LLMClientBase,
    model: str | None = None,
    temperature: float = 0.5,
    pipeline_params: dict | None = None,
    pipeline_schema: PipelineSchema | None = None,
) -> TransitionResult:
    """LLM-driven L3 adjustment: suggest a new strategic plan.

    Analyzes why L2 context/parameter adjustments didn't help and proposes
    a fundamentally different optimization strategy via ``OptSearchPoint.plan``,
    and optionally new pipeline_params when a pipeline schema is available.

    Returns:
        TransitionResult with derived OptSearchPoint and optional pipeline_params.
    """
    l2_summary = "\n".join(
        f"  L2 round {rd.get('l2_round', '?')}: "
        f"params={rd.get('parameters', {})}, "
        f"acc_change={rd.get('accuracy_change', 0):+.1%}"
        for rd in l2_history[-3:]
    )

    pipeline_section = _build_pipeline_prompt_section(pipeline_params, pipeline_schema)

    response_schema_suffix = (
        "\nReturn a JSON object with:\n"
        '  "plan": new strategy text for guiding future optimization\n'
        '  "pipeline_params": {"step_name": {"param": value}} '
        "(or {} for no changes)\n"
        '  "rationale": 1-2 sentence explanation of the strategic shift'
    )

    _compile_vars = {
        "current_plan": opt_sp.plan or "(none — default strategy)",
        "l2_summary": l2_summary,
        "rendered_prompt": opt_sp.render(),
        "pipeline_section": pipeline_section,
        "response_schema_suffix": response_schema_suffix,
    }
    _template = load_optimizer_prompt("l3_modify_plan")
    prompt = _template.compile_prompt(**_compile_vars)

    response = await llm_call(
        llm_client,
        messages=[{"role": "user", "content": prompt}],
        node="l3_modify_plan",
        model=model,
        temperature=temperature,
        trace_meta={
            "template_name": "l3_modify_plan",
            "template_fields": _template.prompt_field_dict(),
            "variables": _compile_vars,
        },
    )
    result = extract_parsed_json(response)

    new_plan = result.get("plan", opt_sp.plan)
    rationale = result.get("rationale", "L3 modify_plan transition")

    new_pipeline_params = _parse_pipeline_params(result, pipeline_params)

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
    )


def _build_pipeline_prompt_section(
    pipeline_params: dict | None,
    pipeline_schema: PipelineSchema | None,
) -> str:
    """Build the pipeline parameters section for L2/L3 LLM prompts.

    Returns an empty string when no schema is available, which causes the
    pipeline_params instructions to be omitted from the prompt.
    """
    if not pipeline_schema:
        return ""
    param_keys = pipeline_schema.node_param_keys()
    if not param_keys:
        return ""
    lines = ["AVAILABLE PIPELINE PARAMETERS (in pipeline execution order):\n"]
    for step_name, keys in param_keys.items():
        current_vals = {}
        if pipeline_params:
            step_cfg = pipeline_params.get(step_name, {})
            if isinstance(step_cfg, dict):
                current_vals = {k: step_cfg.get(k, "?") for k in keys}
        lines.append(f"  {step_name}: {', '.join(sorted(keys))}")
        if current_vals:
            lines.append(f"    current: {json.dumps(current_vals)}")
        lines.append("")
    return "\n".join(lines) + "\n"


def _build_escalation_prompt_section(
    escalation_context: dict | None,
    escalation_journal: list[dict] | None,
    pipeline_params: dict | None = None,
    pipeline_schema: PipelineSchema | None = None,
) -> str:
    """Build the escalation diagnostics section for L2 prompts.

    Returns an empty string when no escalation context is available,
    keeping the normal L2 prompt unchanged.  When present, the section
    shows a data-driven stability map of tried configs so the LLM can
    figure out what to change.
    """
    if not escalation_context:
        return ""

    dominant = escalation_context.get("dominant_warning", "unknown")
    step_name = dominant.split(":")[0] if ":" in dominant else "unknown"
    rate = escalation_context.get("degraded_rate", 0)

    wt = escalation_context.get("warning_types", {})
    wt_str = ", ".join(f"{k} ({v})" for k, v in sorted(wt.items(), key=lambda x: -x[1]))

    lines = [
        f"PIPELINE STABILITY REPORT ({step_name}):\n",
        f"  Current degradation: {rate:.0%} of queries ({wt_str})",
    ]

    # Show current web_search config
    step_cfg = (pipeline_params or {}).get(step_name, {})
    if isinstance(step_cfg, dict) and step_cfg:
        lines.append(f"  Current {step_name} config: {json.dumps(step_cfg)}")

    lines.append("")

    if escalation_journal:
        lines.append("  Tried configs and stability:")
        for entry in escalation_journal:
            step = entry.get("problem_step", "unknown")
            step_cfg = entry.get("step_config", {})
            prev_rate = entry.get("degraded_rate", 0)
            outcome = entry.get("outcome_degraded_rate")
            outcome_str = f" -> {outcome:.0%}" if outcome is not None else ""
            cfg_parts = [f"{k}={v!r}" for k, v in sorted(step_cfg.items())]
            lines.append(
                f"    Round {entry.get('round', '?')}: "
                f"{step} [{', '.join(cfg_parts) or 'defaults'}]"
                f" | {prev_rate:.0%} degraded{outcome_str}"
            )
        lines.append("")

    # Surface the problem step's configurable axes
    if pipeline_schema:
        all_keys = pipeline_schema.node_param_keys()
        step_keys = all_keys.get(step_name, set())
        if step_keys:
            lines.append(f"  Available {step_name} parameters: {', '.join(sorted(step_keys))}")

    lines.append(
        "  The configurations above are all unstable. Suggest different "
        "parameter values to stabilize the pipeline."
    )
    lines.append("")
    return "\n".join(lines) + "\n"


def _parse_pipeline_params(
    llm_result: dict,
    current_pipeline_params: dict | None,
) -> dict | None:
    """Extract and merge pipeline_params from LLM response.

    Expects nested format: ``{"node_name": {"param": value}}``.
    Returns merged pipeline_params dict if the LLM suggested changes,
    or None if no changes were suggested.
    """
    pp_changes = llm_result.get("pipeline_params")
    if not pp_changes or not isinstance(pp_changes, dict):
        return None
    merged = dict(current_pipeline_params or {})
    for key, value in pp_changes.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **value}
        else:
            # Defensive: non-dict values shouldn't appear in nested format
            merged[key] = value
    return merged
