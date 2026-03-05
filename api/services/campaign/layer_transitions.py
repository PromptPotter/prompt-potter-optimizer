"""Layer transition functions for the 3-loop feedback cycle.

L2 (refine_context): Analyzes L1 failure patterns and adjusts PromptState
    parameters and context to improve generation quality.

L3 (modify_plan): Analyzes why L2 adjustments didn't help and suggests
    a new strategic plan.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from api.models.prompt_state import PromptState

if TYPE_CHECKING:
    from api.services.llm_client import LLMClientBase

logger = logging.getLogger(__name__)

DISPLAY_TRUNCATE = 60


async def refine_context(
    current_ps: PromptState,
    stalled_rounds: list[dict],
    eval_data: list[dict],
    llm_client: LLMClientBase,
    model: str | None = None,
) -> PromptState:
    """LLM-driven L2 adjustment: tune parameters and context.

    Analyzes L1 failure patterns from the last stalled rounds and recommends
    changes to ``parameters`` (creativity, n_variants, variant_strategy) and
    ``context`` (domain grounding text).

    Returns:
        Derived PromptState with L2 changes applied.
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

    prompt = (
        "You are a prompt optimization expert. The L1 inner optimization loop "
        "has stalled — candidates are no longer improving.\n\n"
        f"ROUND HISTORY (stalled):\n{round_summary}\n\n"
        f"CURRENT PROMPT:\n---\n{current_ps.render()}\n---\n\n"
        f"FAILURE EXAMPLES:\n{chr(10).join(failure_lines[:15])}\n\n"
        f"CURRENT PARAMETERS: {json.dumps(current_ps.parameters)}\n"
        f"CURRENT CONTEXT: {current_ps.context[:200] if current_ps.context else '(empty)'}\n\n"
        "Analyze WHY L1 is stuck and recommend:\n"
        "1. Parameter adjustments (creativity, n_variants, variant_strategy)\n"
        "2. Context text changes (domain grounding, constraints)\n\n"
        "Return a JSON object with:\n"
        '  "parameters": dict of parameter changes to apply\n'
        '  "context": new context string (or empty to keep current)\n'
        '  "rationale": 1-2 sentence explanation'
    )

    response = await llm_client.chat(
        messages=[{"role": "user", "content": prompt}],
        model=model,
        temperature=0.3,
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

    logger.info(
        "L2 refine_context: %d param changes, context %s",
        len(result.get("parameters", {})),
        "updated" if result.get("context") else "unchanged",
    )

    return current_ps.derive(**changes) if changes else current_ps


async def modify_plan(
    current_ps: PromptState,
    l2_history: list[dict],
    eval_data: list[dict],
    llm_client: LLMClientBase,
    model: str | None = None,
) -> PromptState:
    """LLM-driven L3 adjustment: suggest a new strategic plan.

    Analyzes why L2 context/parameter adjustments didn't help and proposes
    a fundamentally different optimization strategy via ``PromptState.plan``.

    Returns:
        Derived PromptState with new plan text.
    """
    l2_summary = "\n".join(
        f"  L2 round {rd.get('l2_round', '?')}: "
        f"params={rd.get('parameters', {})}, "
        f"acc_change={rd.get('accuracy_change', 0):+.1%}"
        for rd in l2_history[-3:]
    )

    prompt = (
        "You are an expert optimization strategist. Both the inner prompt "
        "generation loop (L1) and the parameter tuning loop (L2) have stalled.\n\n"
        f"CURRENT PLAN: {current_ps.plan or '(none — default strategy)'}\n\n"
        f"L2 ADJUSTMENT HISTORY:\n{l2_summary}\n\n"
        f"CURRENT PROMPT:\n---\n{current_ps.render()}\n---\n\n"
        "The current approach is not working. Suggest a fundamentally different "
        "optimization strategy. Consider:\n"
        "- Different prompting paradigms (chain-of-thought, few-shot, etc.)\n"
        "- Different evaluation focus areas\n"
        "- Structural changes to how the prompt is organized\n\n"
        "Return a JSON object with:\n"
        '  "plan": new strategy text for guiding future optimization\n'
        '  "rationale": 1-2 sentence explanation of the strategic shift'
    )

    response = await llm_client.chat(
        messages=[{"role": "user", "content": prompt}],
        model=model,
        temperature=0.5,
        max_tokens=2048,
        output_format="json",
    )
    result = response.parsed or json.loads(response.content)

    new_plan = result.get("plan", current_ps.plan)
    rationale = result.get("rationale", "L3 modify_plan transition")

    logger.info("L3 modify_plan: %s", rationale[:100])

    return current_ps.derive(
        plan=new_plan,
        changes_description=f"L3: {rationale[:80]}",
    )
