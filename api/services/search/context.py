"""LLM-assisted context restructuring into Layer 1 fields."""

import json
from typing import Any

from api.services.llm_client import LLMClientBase


async def restructure_context(
    context_input: Any,
    llm_client: LLMClientBase,
    model: str | None = None,
    improvement_areas: str = "",
) -> dict:
    """LLM-assisted restructuring of user context into Layer 1 fields.

    Args:
        context_input: Either a string (raw context) or a dict of partial
            Layer 1 fields.
        llm_client: LLM client implementing LLMClientBase.
        model: Model identifier (uses client default if None).
        improvement_areas: Optional domain-expert observations about where
            improvement is most likely.

    Returns:
        Dict of structured Layer 1 field values, plus a ``consultation``
        string when improvement_areas is provided.
    """
    if isinstance(context_input, dict):
        user_content = (
            "The user has provided partial Layer 1 fields for a prompt. "
            "Validate them, fill any gaps, and suggest improvements.\n\n"
            f"Provided fields:\n{json.dumps(context_input, indent=2)}"
        )
    else:
        user_content = (
            "The user has provided a raw context description. Parse it into "
            "structured Layer 1 prompt fields.\n\n"
            f"Context:\n{context_input}"
        )

    if improvement_areas:
        user_content += (
            "\n\nThe user has identified the following areas where improvement "
            "is most likely:\n"
            f"{improvement_areas}\n\n"
            "Take these observations into account when structuring the fields "
            "and provide strategic advice in the consultation field."
        )

    layer1_keys_description = (
        "Layer 1 fields:\n"
        "- persona: Who the LLM should act as (e.g., 'You are a domain expert...')\n"
        "- task_intent: What the prompt needs to accomplish\n"
        "- problem_description: Description of the problem domain\n"
        "- instruction: Core instruction text (may contain template variables)\n"
        "- thinking_style: How to reason (e.g., 'Think step by step')\n"
        "- answer_format: Expected output format\n"
    )

    if improvement_areas:
        system_prompt = (
            "You are a prompt engineering assistant. Your job is to structure "
            "user-provided context into Layer 1 prompt fields for an optimization "
            "campaign.\n\n"
            f"{layer1_keys_description}\n"
            "Return a JSON object with these keys plus a \"consultation\" key. "
            "The consultation value should be a natural-language paragraph of "
            "strategic advice on how to approach optimization given the user's "
            "identified improvement areas. Use empty string for Layer 1 fields "
            "that don't apply. Be concise and actionable."
        )
    else:
        system_prompt = (
            "You are a prompt engineering assistant. Your job is to structure "
            "user-provided context into Layer 1 prompt fields for an optimization "
            "campaign.\n\n"
            f"{layer1_keys_description}\n"
            "Return a JSON object with exactly these keys. Use empty string for "
            "fields that don't apply. Be concise and actionable."
        )

    response = await llm_client.chat(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        model=model,
        temperature=0.3,
        max_tokens=2000,
        output_format="json",
    )
    result = response.parsed or json.loads(response.content)

    for key in ("persona", "task_intent", "problem_description",
                "instruction", "thinking_style", "answer_format"):
        result.setdefault(key, "")

    if improvement_areas:
        result.setdefault("consultation", "")

    return result
