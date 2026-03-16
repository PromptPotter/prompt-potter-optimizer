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


async def refine_context(
    current_ps: PromptState,
    stalled_rounds: list[dict],
    eval_data: list[dict],
    llm_client: LLMClientBase,
    model: str | None = None,
    temperature: float = 0.3,
    pipeline_params: dict | None = None,
    pipeline_schema: PipelineSchema | None = None,
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

    prompt = (
        "You are a prompt optimization expert. The L1 inner optimization loop "
        "has stalled — candidates are no longer improving.\n\n"
        f"ROUND HISTORY (stalled):\n{round_summary}\n\n"
        f"CURRENT PROMPT:\n---\n{current_ps.render()}\n---\n\n"
        f"FAILURE EXAMPLES:\n{chr(10).join(failure_lines[:15])}\n\n"
        f"CURRENT PARAMETERS: {json.dumps(current_ps.parameters)}\n"
        f"CURRENT CONTEXT: {current_ps.context[:200] if current_ps.context else '(empty)'}\n\n"
        f"{pipeline_section}"
        "Analyze WHY L1 is stuck and recommend:\n"
        "1. Parameter adjustments (creativity, n_variants, variant_strategy)\n"
        "2. Context text changes (domain grounding, constraints)\n"
    )
    if pipeline_section:
        prompt += "3. Pipeline parameter adjustments (see available params above)\n"
    prompt += (
        "\nReturn a JSON object with:\n"
        '  "parameters": dict of parameter changes to apply\n'
        '  "context": new context string (or empty to keep current)\n'
    )
    if pipeline_section:
        prompt += (
            '  "pipeline_params": dict of pipeline parameter changes '
            "(param_name -> new value)\n"
        )
    prompt += '  "rationale": 1-2 sentence explanation'

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

    logger.info(
        "L2 refine_context: %d param changes, context %s, pipeline_params %s",
        len(result.get("parameters", {})),
        "updated" if result.get("context") else "unchanged",
        "updated" if new_pipeline_params else "unchanged",
    )

    new_ps = current_ps.derive(**changes) if changes else current_ps
    return TransitionResult(prompt_state=new_ps, pipeline_params=new_pipeline_params)


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

    prompt = (
        "You are an expert optimization strategist. Both the inner prompt "
        "generation loop (L1) and the parameter tuning loop (L2) have stalled.\n\n"
        f"CURRENT PLAN: {current_ps.plan or '(none — default strategy)'}\n\n"
        f"L2 ADJUSTMENT HISTORY:\n{l2_summary}\n\n"
        f"CURRENT PROMPT:\n---\n{current_ps.render()}\n---\n\n"
        f"{pipeline_section}"
        "The current approach is not working. Suggest a fundamentally different "
        "optimization strategy. Consider:\n"
        "- Different prompting paradigms (chain-of-thought, few-shot, etc.)\n"
        "- Different evaluation focus areas\n"
        "- Structural changes to how the prompt is organized\n"
    )
    if pipeline_section:
        prompt += "- Pipeline parameter changes (see available params above)\n"
    prompt += (
        "\nReturn a JSON object with:\n"
        '  "plan": new strategy text for guiding future optimization\n'
    )
    if pipeline_section:
        prompt += (
            '  "pipeline_params": dict of pipeline parameter changes '
            "(param_name -> new value)\n"
        )
    prompt += '  "rationale": 1-2 sentence explanation of the strategic shift'

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
    return TransitionResult(prompt_state=new_ps, pipeline_params=new_pipeline_params)


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
