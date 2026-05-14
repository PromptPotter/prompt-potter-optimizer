"""JSON parsing, repair, and post-response validation for LLM outputs.

``json_repair.repair_json`` is the single repair surface for everything
the hand-rolled regex repair this replaced couldn't handle (invalid
``\\X`` escapes, trailing commas, unquoted keys, brace truncation).
``_try_groq_json_validate_repair`` salvages Groq's 400
``json_validate_failed`` quirk where the original (parseable) JSON sits
inside the error body's ``failed_generation`` field.
"""

from __future__ import annotations

import logging
from typing import Any

from json_repair import repair_json
from pydantic import BaseModel

from promptpotter.infrastructure.llm.models import LLMResponse

logger = logging.getLogger(__name__)


def try_parse_json(content: str, provider: str) -> Any | None:
    """Parse JSON from response content; return None on failure.

    Strips markdown code fences (Groq/Kimi emit ```json ... ``` even with
    ``response_format=json``), then delegates to ``json_repair.repair_json``
    for everything else (invalid ``\\X`` escapes, trailing commas, unquoted
    keys, brace truncation). One library, one repair surface — the
    hand-rolled regex repair this replaced couldn't handle invalid escape
    sequences (the failure mode that produced the audit).
    """
    text = content.strip()
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl != -1:
            text = text[first_nl + 1 :]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    try:
        return repair_json(text, return_objects=True)
    except Exception:
        logger.debug("%s response not valid JSON: %s", provider, content[:200])
        return None


def extract_parsed_json(response: Any) -> Any:
    """Return the parsed JSON object from an ``LLMResponse``.

    ``OpenAICompatibleClient.chat()`` and ``AnthropicClient.chat()`` already
    populate ``response.parsed`` via :func:`try_parse_json` for every
    ``json``/``json_schema`` call. This function exists for the rare
    ``output_format='text'`` caller that happens to want JSON back; in that
    path ``response.parsed`` is ``None`` and we attempt the same repair
    pipeline. If even repair fails, raise a ``ValueError`` carrying a
    diagnostic snippet — the prior raw ``json.loads`` fallback was dead
    code (re-ran ``json.loads`` on content ``try_parse_json`` had already
    given up on) and is gone.
    """
    if response.parsed is not None:
        return response.parsed
    parsed = try_parse_json(response.content, "extract_parsed_json")
    if parsed is not None:
        return parsed
    raise ValueError(
        f"LLM response could not be parsed as JSON; content: {response.content[:500]!r}"
    )


def parse_response_content(
    content: str,
    response_model: type[BaseModel] | None,
    response_schema: dict | None,
    provider_name: str,
) -> Any | None:
    """Decode + (optionally) validate provider-returned content.

    Three modes (mirrors ``chat()``'s contract):
      - ``response_model is None and response_schema is None``: text mode,
        return ``None``.
      - ``response_model is None and response_schema is not None``:
        JSON-mode dict — parse via ``try_parse_json``, return dict (or
        ``None`` if even repair fails).
      - ``response_model is not None``: parse via ``try_parse_json`` then
        ``response_model.model_validate(...)`` for type-level guarantee.
        Raises ``ValueError`` on parse failure.
    """
    if not content:
        return None
    if response_model is None and response_schema is None:
        return None
    parsed = try_parse_json(content, provider_name)
    if response_model is None:
        return parsed
    if parsed is None:
        raise ValueError(
            f"{provider_name} returned unparseable JSON for {response_model.__name__}: "
            f"{content[:500]!r}"
        )
    return response_model.model_validate(parsed)


def _repair_json_validate_failure(err_str: str) -> tuple[str, Any] | None:
    """Salvage Groq's ``json_validate_failed`` 400 by re-parsing ``failed_generation``; ``None`` to fall through."""
    fg_key = "'failed_generation': '"
    fg_start = err_str.find(fg_key)
    if fg_start < 0:
        return None
    fg_text = err_str[fg_start + len(fg_key) :]
    fg_end = fg_text.rfind("'}")
    if fg_end <= 0:
        return None
    fg_text = fg_text[:fg_end].replace("\\n", "\n").replace("\\'", "'")
    parsed = try_parse_json(fg_text, "json_repair")
    if parsed is None:
        return None
    return fg_text, parsed


def try_groq_json_validate_repair(
    exc: Exception,
    request_params: dict[str, Any],
    provider_name: str,
    response_model: type[BaseModel] | None,
) -> LLMResponse | None:
    """Groq-specific: turn a 400 ``json_validate_failed`` into a salvaged response.

    Returns ``None`` for any error that isn't this exact quirk so the caller
    re-raises. No-op for non-Groq providers — they don't surface this status.
    When ``response_model`` is supplied, the salvaged dict is validated and
    the typed instance lands on ``LLMResponse.parsed``.
    """
    if getattr(exc, "status_code", None) != 400 or "json_validate_failed" not in str(exc):
        return None
    repaired = _repair_json_validate_failure(str(exc))
    if repaired is None:
        return None
    fg_text, parsed = repaired
    if response_model is not None:
        parsed = response_model.model_validate(parsed)
    logger.info("%s: salvaged failed_generation via JSON repair", provider_name)
    return LLMResponse(
        content=fg_text,
        model=request_params.get("model", ""),
        usage={"prompt_tokens": 0, "completion_tokens": 0},
        parsed=parsed,
    )
