"""Layer transition functions for the 3-loop feedback cycle.

L2 (refine_context): Analyzes L1 failure patterns and adjusts PromptState
    parameters and context to improve generation quality.

L3 (modify_plan): Analyzes why L2 adjustments didn't help and suggests
    a new strategic plan.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from api.config.optimizer_prompt_loader import load_optimizer_prompt
from api.models.prompt_state import PromptState

if TYPE_CHECKING:
    from api.models.pipeline_schema import PipelineSchema
    from api.services.llm_client import LLMClientBase

logger = logging.getLogger(__name__)

DISPLAY_TRUNCATE = 60


@dataclass
class TransitionResult:
    """Return value from L2/L3 transitions.

    Bundles the new PromptState with optional pipeline_params changes,
    keeping both dimensions of the search space in one result.
    """

    prompt_state: PromptState
    pipeline_params: dict | None = None
    task_context: dict | None = None
    debug_prompt: str = ""
    debug_response: dict | None = None


async def refine_context(
    current_ps: PromptState,
    stalled_rounds: list[dict],
    eval_data: list[dict],
    llm_client: LLMClientBase,
    model: str | None = None,
    temperature: float = 0.3,
    pipeline_params: dict | None = None,
    pipeline_schema: PipelineSchema | None = None,
    escalation_context: dict | None = None,
    escalation_journal: list[dict] | None = None,
    task_context: dict | None = None,
) -> TransitionResult:
    """LLM-driven L2 adjustment: tune parameters, context, and pipeline params.

    Analyzes L1 failure patterns from the last stalled rounds and recommends
    changes to ``parameters`` (creativity, n_variants, variant_strategy),
    ``context`` (domain grounding text), and optionally ``pipeline_params``
    when a pipeline schema is available.

    Returns:
        TransitionResult with derived PromptState and optional pipeline_params.
    """
    failure_lines = []
    for rd in stalled_rounds[-3:]:
        for r in rd.get("results", [])[:5]:
            if not r.get("hit") and not r.get("error"):
                failure_lines.append(
                    f"  Q: {r['query'][:DISPLAY_TRUNCATE]}  "
                    f"Pred: {r.get('predicted', '?')[:DISPLAY_TRUNCATE]}  "
                    f"GT: {r['ground_truth'][:DISPLAY_TRUNCATE]}"
                )

    round_summary = "\n".join(
        f"  Round {rd.get('round', '?')}: acc={rd.get('accuracy', 0):.1%}"
        for rd in stalled_rounds
    )

    pipeline_section = _build_pipeline_prompt_section(pipeline_params, pipeline_schema)
    escalation_section = _build_escalation_prompt_section(
        escalation_context, escalation_journal, pipeline_params,
    )

    task_context_section = ""
    if task_context:
        task_context_section = (
            "\n\nTASK CONTEXT (structured domain understanding — refine if inaccurate):\n"
            + json.dumps(task_context, indent=2)
        )

    response_schema_suffix = (
        "\nReturn a JSON object with:\n"
        '  "parameters": dict of parameter changes (or {} to keep current)\n'
        '  "context": new context string (or "" to keep current)\n'
        '  "pipeline_params": {"step_name": {"param": value}} '
        "(or {} for no changes)\n"
        '  "task_context": dict of refined domain fields (or {} to keep current)\n'
        '  "rationale": 1-2 sentence explanation'
    )

    prompt = load_optimizer_prompt("l2_refine_context").compile(
        round_summary=round_summary,
        rendered_prompt=current_ps.render(),
        failure_lines=chr(10).join(failure_lines[:15]),
        current_params=json.dumps(current_ps.parameters),
        current_context=current_ps.context[:200] if current_ps.context else "(empty)",
        task_context_section=task_context_section,
        pipeline_section=pipeline_section,
        escalation_section=escalation_section,
        response_schema_suffix=response_schema_suffix,
    )

    response = await llm_client.chat(
        messages=[{"role": "user", "content": prompt}],
        model=model,
        temperature=temperature,
        max_tokens=2048,
        output_format="json",
    )
    result = response.parsed or json.loads(response.content)

    changes: dict = {}
    if result.get("parameters"):
        new_params = {**current_ps.parameters, **result["parameters"]}
        changes["parameters"] = new_params
    if result.get("context"):
        changes["context"] = result["context"]

    rationale = result.get("rationale", "L2 refine_context transition")
    changes["changes_description"] = f"L2: {rationale[:80]}"

    new_pipeline_params = _parse_pipeline_params(result, pipeline_params)

    # Parse refined task_context (merge with current, only non-empty updates)
    new_task_context = None
    if result.get("task_context") and isinstance(result["task_context"], dict):
        merged = {**(task_context or {}), **result["task_context"]}
        # Only count as changed if there are actual differences
        if merged != (task_context or {}):
            new_task_context = merged

    logger.info(
        "L2 refine_context: %d param changes, context %s, pipeline_params %s, task_context %s",
        len(result.get("parameters", {})),
        "updated" if result.get("context") else "unchanged",
        "updated" if new_pipeline_params else "unchanged",
        "updated" if new_task_context else "unchanged",
    )

    new_ps = current_ps.derive(**changes) if changes else current_ps
    return TransitionResult(
        prompt_state=new_ps,
        pipeline_params=new_pipeline_params,
        task_context=new_task_context,
        debug_prompt=prompt,
        debug_response=result,
    )


async def modify_plan(
    current_ps: PromptState,
    l2_history: list[dict],
    eval_data: list[dict],
    llm_client: LLMClientBase,
    model: str | None = None,
    temperature: float = 0.5,
    pipeline_params: dict | None = None,
    pipeline_schema: PipelineSchema | None = None,
) -> TransitionResult:
    """LLM-driven L3 adjustment: suggest a new strategic plan.

    Analyzes why L2 context/parameter adjustments didn't help and proposes
    a fundamentally different optimization strategy via ``PromptState.plan``,
    and optionally new pipeline_params when a pipeline schema is available.

    Returns:
        TransitionResult with derived PromptState and optional pipeline_params.
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

    prompt = load_optimizer_prompt("l3_modify_plan").compile(
        current_plan=current_ps.plan or "(none — default strategy)",
        l2_summary=l2_summary,
        rendered_prompt=current_ps.render(),
        pipeline_section=pipeline_section,
        response_schema_suffix=response_schema_suffix,
    )

    response = await llm_client.chat(
        messages=[{"role": "user", "content": prompt}],
        model=model,
        temperature=temperature,
        max_tokens=2048,
        output_format="json",
    )
    result = response.parsed or json.loads(response.content)

    new_plan = result.get("plan", current_ps.plan)
    rationale = result.get("rationale", "L3 modify_plan transition")

    new_pipeline_params = _parse_pipeline_params(result, pipeline_params)

    logger.info("L3 modify_plan: %s, pipeline_params %s",
                rationale[:100],
                "updated" if new_pipeline_params else "unchanged")

    new_ps = current_ps.derive(
        plan=new_plan,
        changes_description=f"L3: {rationale[:80]}",
    )
    return TransitionResult(
        prompt_state=new_ps,
        pipeline_params=new_pipeline_params,
    )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


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
    param_keys = pipeline_schema.step_param_keys()
    if not param_keys:
        return ""
    lines = ["AVAILABLE PIPELINE PARAMETERS (you may suggest value changes):\n"]
    for step_name, keys in sorted(param_keys.items()):
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
            prefix = entry.get("query_prefix", "?")
            suffix = entry.get("query_suffix", "")
            prev_rate = entry.get("degraded_rate", 0)
            outcome = entry.get("outcome_degraded_rate")
            outcome_str = f" -> {outcome:.0%}" if outcome is not None else ""
            ws_cfg = entry.get("web_search_config", {})
            cfg_parts = [f"prefix={prefix!r}"]
            if suffix:
                cfg_parts.append(f"suffix={suffix!r}")
            if ws_cfg:
                for k, v in sorted(ws_cfg.items()):
                    cfg_parts.append(f"{k}={v}")
            lines.append(
                f"    Round {entry.get('round', '?')}: "
                f"{', '.join(cfg_parts)}"
                f" | {prev_rate:.0%} degraded{outcome_str}"
            )
        lines.append("")

    lines.append(
        "  The configurations above are all unstable. Suggest different "
        "query_prefix/query_suffix values or web_search settings to stabilize."
    )
    lines.append("")
    return "\n".join(lines) + "\n"


def _parse_pipeline_params(
    llm_result: dict,
    current_pipeline_params: dict | None,
) -> dict | None:
    """Extract and merge pipeline_params from LLM response.

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
            merged[key] = value
    return merged
