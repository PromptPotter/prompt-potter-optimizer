"""JSON parsing, repair, and post-response validation for LLM outputs — invalid escapes, trailing
commas, brace truncation, plus Groq's ``json_validate_failed`` salvage."""

from __future__ import annotations

import logging
from typing import Any

from json_repair import repair_json
from pydantic import BaseModel, ValidationError

from promptpotter.infrastructure.llm.response import LLMResponse

logger = logging.getLogger(__name__)

# Below this, content is "the provider returned nothing", not an attempt at the schema.
# ONE home for the threshold: the client picks its retry STRATEGY with it and the error
# classifies itself with it, so a call retried as a flake cannot then be scored as a
# prompt fault (or the reverse) because two modules drew the line differently.
MIN_CONTENT_CHARS = 20

# Retry strategies (``OptimizerPromptParseError.retry_kind``). See ``OpenAICompatibleClient.chat``.
RETRY_CLEAN_REASK = "clean_reask"
RETRY_SCHEMA_REPAIR = "schema_repair"


class OptimizerPromptParseError(RuntimeError):
    """Content that failed Pydantic validation after one repair-hint retry. **Two round-trips, two
    accounts**: the ``first_*`` fields describe the attempt that failed, the rest the repair."""

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
        first_finish_reason: str | None = None,
        first_content_chars: int | None = None,
        first_completion_tokens: int = 0,
        first_reasoning_tokens: int = 0,
        retry_kind: str = "",
    ):
        super().__init__(
            f"Optimizer prompt response failed Pydantic validation after {attempts} attempt(s): "
            f"{error.error_count()} errors"
        )
        self.raw = raw
        self.error = error
        self.attempts = attempts
        self.model = model
        self.finish_reason = finish_reason
        # TOTALS across both round-trips — the billing contract. `dispatch/llm_call/call.py`
        # is the sole `emit_token_usage` site and meters the burned spend off these two, and
        # a failed call is billed exactly like a good one. Per-attempt figures live in the
        # `first_*` fields below; do not narrow these to one attempt to make a log read
        # nicer, or the ledger under-reports every repaired call by a full round-trip.
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.reasoning_tokens = reasoning_tokens
        self.reasoning_chars = reasoning_chars
        self.first_finish_reason = first_finish_reason
        self.first_content_chars = first_content_chars
        self.first_completion_tokens = first_completion_tokens
        self.first_reasoning_tokens = first_reasoning_tokens
        self.retry_kind = retry_kind

    @property
    def raw_chars(self) -> int:
        """Stripped content length — the number every consumer reports."""
        return len((self.raw or "").strip())

    @property
    def failing_chars(self) -> int:
        """Content length of the attempt that FAILED — the first one where it was
        recorded, else the repair's. The number the classification below reasons over."""
        return self.raw_chars if self.first_content_chars is None else self.first_content_chars

    @property
    def reproduced(self) -> bool:
        """Did the second attempt fail the SAME way as the first? Only meaningful after a clean re-ask,
        where matching ``finish_reason`` means the failure is a property of the request, not the moment."""
        first_empty = (self.first_content_chars or 0) < MIN_CONTENT_CHARS
        return self.first_finish_reason == self.finish_reason and first_empty == (
            self.raw_chars < MIN_CONTENT_CHARS
        )

    @property
    def is_empty(self) -> bool:
        """Provider degraded, vs output the optimizer prompt owns. Downstream DELETES an L4 round on this, so a wrong
        answer loses evidence: ``finish_reason="length"`` is never degradation, and a clean re-ask decides the rest."""
        if self.first_finish_reason == "length":
            return False
        if self.retry_kind == RETRY_CLEAN_REASK and self.reproduced:
            return False
        return self.failing_chars < MIN_CONTENT_CHARS

    def warning_detail(self) -> dict[str, Any]:
        """The provider's own account of WHY, for a round warning's disk-bound ``detail``. ``length`` + large reasoning tokens
        means the PROMPT is too big; ``stop`` with ~2 completion tokens means the provider degraded. Opposite fixes."""
        return {
            "retry_kind": self.retry_kind,
            "reproduced": self.reproduced if self.retry_kind == RETRY_CLEAN_REASK else None,
            "first_finish_reason": self.first_finish_reason,
            "first_content_chars": self.first_content_chars,
            "first_completion_tokens": self.first_completion_tokens,
            "first_reasoning_tokens": self.first_reasoning_tokens,
            "finish_reason": self.finish_reason,
            "reasoning_tokens": self.reasoning_tokens,
            "reasoning_chars": self.reasoning_chars,
            "raw_chars": self.raw_chars,
            "billed_completion_tokens": self.completion_tokens,
        }

    def diagnosis(self) -> str:
        """One-line disk-bound account of the failure. Per ATTEMPT, because the two attempts are different events with
        different causes, and the fix differs by which one you are reading."""
        return (
            f"attempt1(finish={self.first_finish_reason} chars={self.first_content_chars} "
            f"completion_tokens={self.first_completion_tokens} "
            f"reasoning_tokens={self.first_reasoning_tokens}) "
            f"retry={self.retry_kind or 'none'} "
            f"attempt2(finish={self.finish_reason} chars={self.raw_chars} "
            f"reasoning_tokens={self.reasoning_tokens} reasoning_chars={self.reasoning_chars}) "
            f"reproduced={self.reproduced if self.retry_kind == RETRY_CLEAN_REASK else 'n/a'} "
            f"billed_completion_tokens={self.completion_tokens} model={self.model}"
        )


def try_parse_json(content: str, provider: str) -> Any | None:
    """Parse JSON from response content, ``None`` on failure. Strips ```` ```json ```` fences, which
    Groq and Kimi emit even under ``response_format=json``."""
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


def extract_parsed_json(response: LLMResponse) -> Any:
    """The parsed JSON object from an ``LLMResponse`` — the ``output_format='text'`` fallback, since
    JSON-mode calls populate ``response.parsed`` upstream."""
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
    """Decode + optionally validate provider content, mirroring ``chat()``'s contract. An EMPTY
    body raises rather than leaking past the schema guard as ``parsed=None``."""
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
    "MIN_CONTENT_CHARS",
    "RETRY_CLEAN_REASK",
    "RETRY_SCHEMA_REPAIR",
    "OptimizerPromptParseError",
    "extract_parsed_json",
    "parse_response_content",
    "try_groq_json_validate_repair",
]
