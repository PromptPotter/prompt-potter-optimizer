"""LLM response parsing utilities.

Leaf module shared by llm_client and service-layer callers.
Lives in ``promptpotter/shared/`` — no domain model or service dependencies.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def try_parse_json(content: str, provider: str) -> Any | None:
    """Parse JSON from response content, return None on failure."""
    text = content.strip()
    # Strip markdown code fences (e.g. ```json ... ```)
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl != -1:
            text = text[first_nl + 1:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Best-effort repair of malformed JSON (Groq/kimi artifacts)
    try:
        repaired = text.replace("\\\\n", "\\n").replace("\\\\t", "\\t")
        repaired = re.sub(r'(?<=[{,\s])(\w+)\s*:', r'"\1":', repaired)
        repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
        # Truncate after last balanced closing brace
        depth = 0
        last_valid = -1
        for i, ch in enumerate(repaired):
            if ch in "{[":
                depth += 1
            elif ch in "}]":
                depth -= 1
                if depth == 0:
                    last_valid = i
        if 0 < last_valid < len(repaired) - 1:
            repaired = repaired[: last_valid + 1]
        result = json.loads(repaired)
        logger.info("%s JSON repaired successfully", provider)
        return result
    except json.JSONDecodeError:
        logger.debug("%s response not valid JSON: %s", provider, content[:200])
        return None


def extract_parsed_json(response: Any) -> Any:
    """Extract parsed JSON from an LLM response.

    Uses the pre-parsed ``response.parsed`` if available, otherwise
    falls back to ``json.loads(response.content)``.

    Args:
        response: An ``LLMResponse`` (or any object with ``.parsed``
            and ``.content`` attributes).

    Returns:
        The parsed JSON value (dict, list, etc.).

    Raises:
        json.JSONDecodeError: If ``response.parsed`` is falsy and
            ``response.content`` is not valid JSON.
    """
    if response.parsed:
        return response.parsed
    return json.loads(response.content)
