"""JSON parsing, repair, and post-response validation for LLM outputs.

``json_repair.repair_json`` handles invalid ``\\X`` escapes, trailing commas,
unquoted keys, brace truncation. ``try_groq_json_validate_repair`` salvages
Groq's 400 ``json_validate_failed`` where the original JSON sits in
``failed_generation``.
"""

from __future__ import annotations

import logging
from typing import Any

from json_repair import repair_json
from pydantic import BaseModel, ValidationError

from promptpotter.infrastructure.llm.models import LLMResponse

logger = logging.getLogger(__name__)


class MetaPromptParseError(RuntimeError):
    """LLM returned content that failed Pydantic validation after one repair-hint retry.

    Carries raw + last ValidationError → L1 records Wound 1, L2 heals next round.

    Also carries the provider's own account of WHY, because the dominant failure is an
    empty ``content`` and the raw string alone cannot distinguish its causes: a reasoning
    model that spent its whole ``max_tokens`` budget on reasoning (``finish_reason:
    length``, large ``reasoning_tokens``) looks identical to a provider that simply
    returned nothing (``finish_reason: stop``, ``completion_tokens: 2``). The caller
    (``dispatch/llm_call/call.py``) meters the burned tokens off this record — the call
    is billed whether or not it parsed, and it is the sole ``emit_token_usage`` site.
    """

    def __init__(
        self,
        raw: str,
        error: ValidationError,
        *,
        attempts: int = 2,
        model: str | None = None,
        finish_reason: str | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        reasoning_tokens: int = 0,
        reasoning_chars: int = 0,
    ):
        super().__init__(
            f"Meta-prompt response failed Pydantic validation after {attempts} attempt(s): "
            f"{error.error_count()} errors"
        )
        self.raw = raw
        self.error = error
        self.attempts = attempts
        self.model = model
        self.finish_reason = finish_reason
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.reasoning_tokens = reasoning_tokens
        self.reasoning_chars = reasoning_chars

    @property
    def raw_chars(self) -> int:
        """Stripped content length — the number every consumer reports."""
        return len((self.raw or "").strip())

    @property
    def is_empty(self) -> bool:
        """Provider-degraded empty/truncated content, vs schema-noncompliant output.
        The one home of the <20-chars split — it steers L2's heal direction, so the
        L1 and L2/L3 handlers must classify identically."""
        return self.raw_chars < 20

    def warning_detail(self) -> dict[str, Any]:
        """The provider's own account of WHY, for a round warning's disk-bound
        ``detail``. ``finish_reason: length`` + a large ``reasoning_tokens`` means
        the meta-prompt is too big for the token budget (fix the prompt); ``stop``
        with ~2 completion tokens means the provider degraded (retry/route
        elsewhere). Same symptom, opposite fix — so both land on disk rather than
        only in the log."""
        return {
            "finish_reason": self.finish_reason,
            "completion_tokens": self.completion_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "reasoning_chars": self.reasoning_chars,
            "raw_chars": self.raw_chars,
        }

    def diagnosis(self) -> str:
        """One-line, disk-bound account of the failure — the wound's ``value`` and the log."""
        return (
            f"finish_reason={self.finish_reason} completion_tokens={self.completion_tokens} "
            f"reasoning_tokens={self.reasoning_tokens} reasoning_chars={self.reasoning_chars} "
            f"content_chars={self.raw_chars} model={self.model}"
        )


def try_parse_json(content: str, provider: str) -> Any | None:
    """Parse JSON from response content; return None on failure.

    Strips ```json ... ``` fences (Groq/Kimi emit them even with
    ``response_format=json``), then delegates to ``json_repair.repair_json``.
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


def _unwrap_single_element_list(parsed: Any) -> Any:
    # Groq/openai-oss occasionally wraps the structured-output object in a
    # single-element list; every optimizer response_model is a root-object.
    if isinstance(parsed, list) and len(parsed) == 1 and isinstance(parsed[0], dict):
        return parsed[0]
    return parsed


def extract_parsed_json(response: Any) -> Any:
    """Return the parsed JSON object from an ``LLMResponse``.

    JSON-mode calls populate ``response.parsed`` upstream; this is the
    ``output_format='text'`` fallback that re-attempts the repair pipeline,
    raising ``ValueError`` with a snippet if even repair fails.
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
    response_schema: dict[str, Any] | None,
    provider_name: str,
) -> Any | None:
    """Decode + (optionally) validate provider-returned content. Mirrors ``chat()``'s contract:

    - both ``None``: text mode → ``None``.
    - only ``response_schema``: JSON-mode dict (``None`` on repair fail).
    - ``response_model`` set: parse + ``model_validate``; ``ValueError`` on
      unparseable, ``ValidationError`` on empty (reasoning models can burn
      their full budget on reasoning and emit empty ``content`` — must not
      leak past the schema guard as ``parsed=None``).
    """
    if not content or not content.strip():
        if response_model is not None:
            response_model.model_validate(None)
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
    parsed = _unwrap_single_element_list(parsed)
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
    """Groq 400 ``json_validate_failed`` → salvaged ``LLMResponse``; ``None`` otherwise (caller re-raises)."""
    if getattr(exc, "status_code", None) != 400 or "json_validate_failed" not in str(exc):
        return None
    repaired = _repair_json_validate_failure(str(exc))
    if repaired is None:
        return None
    fg_text, parsed = repaired
    if response_model is not None:
        parsed = _unwrap_single_element_list(parsed)
        parsed = response_model.model_validate(parsed)
    logger.info("%s: salvaged failed_generation via JSON repair", provider_name)
    return LLMResponse(
        content=fg_text,
        model=request_params.get("model", ""),
        usage={"prompt_tokens": 0, "completion_tokens": 0},
        parsed=parsed,
    )


__all__ = [
    "MetaPromptParseError",
    "extract_parsed_json",
    "parse_response_content",
    "try_groq_json_validate_repair",
    "try_parse_json",
]
