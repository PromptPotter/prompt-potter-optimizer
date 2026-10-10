from __future__ import annotations

import logging
from typing import Any

from json_repair import repair_json
from pydantic import BaseModel, ValidationError

from promptpotter.domain.spend import TokenAccount
from promptpotter.infrastructure.llm.response import LLMResponse

logger = logging.getLogger(__name__)

MIN_CONTENT_CHARS = 20

RETRY_CLEAN_REASK = "clean_reask"
RETRY_SCHEMA_REPAIR = "schema_repair"


class OptimizerPromptParseError(RuntimeError):
    """Two round-trips, two accounts: ``first_*`` is the attempt that failed, the rest the repair."""

    def __init__(
        self,
        raw: str,
        error: ValidationError,
        *,
        attempts: int = 2,
        model: str | None = None,
        finish_reason: str | None = None,
        usage: TokenAccount | None = None,
        reasoning_chars: int = 0,
        first_finish_reason: str | None = None,
        first_content_chars: int | None = None,
        first: TokenAccount | None = None,
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
        # BILLED across both round-trips: `bench/llm_call.py` meters the burned spend off it.
        self.usage = usage or TokenAccount()
        self.reasoning_chars = reasoning_chars
        self.first_finish_reason = first_finish_reason
        self.first_content_chars = first_content_chars
        self.first = first or TokenAccount()
        self.retry_kind = retry_kind

    @property
    def raw_chars(self) -> int:
        return len(self.raw.strip())

    @property
    def failing_chars(self) -> int:
        return self.raw_chars if self.first_content_chars is None else self.first_content_chars

    @property
    def reproduced(self) -> bool:
        """Only meaningful after a clean re-ask."""
        first_empty = (self.first_content_chars or 0) < MIN_CONTENT_CHARS
        return self.first_finish_reason == self.finish_reason and first_empty == (
            self.raw_chars < MIN_CONTENT_CHARS
        )

    @property
    def is_empty(self) -> bool:
        """Downstream DELETES an L4 round on this: ``finish_reason="length"`` is never degradation."""
        if self.first_finish_reason == "length":
            return False
        if self.retry_kind == RETRY_CLEAN_REASK and self.reproduced:
            return False
        return self.failing_chars < MIN_CONTENT_CHARS

    def warning_detail(self) -> dict[str, Any]:
        return {
            "retry_kind": self.retry_kind,
            "reproduced": self.reproduced if self.retry_kind == RETRY_CLEAN_REASK else None,
            "first_finish_reason": self.first_finish_reason,
            "first_content_chars": self.first_content_chars,
            "first_completion_tokens": self.first.output,
            "first_reasoning_tokens": self.first.reasoning,
            "finish_reason": self.finish_reason,
            "reasoning_chars": self.reasoning_chars,
            "raw_chars": self.raw_chars,
            "billed_reasoning_tokens": self.usage.reasoning,
            "billed_completion_tokens": self.usage.output,
        }

    def diagnosis(self) -> str:
        return (
            f"attempt1(finish={self.first_finish_reason} chars={self.first_content_chars} "
            f"completion_tokens={self.first.output} "
            f"reasoning_tokens={self.first.reasoning}) "
            f"retry={self.retry_kind or 'none'} "
            f"attempt2(finish={self.finish_reason} chars={self.raw_chars} "
            f"reasoning_chars={self.reasoning_chars}) "
            f"reproduced={self.reproduced if self.retry_kind == RETRY_CLEAN_REASK else 'n/a'} "
            f"billed_completion_tokens={self.usage.output} "
            f"billed_reasoning_tokens={self.usage.reasoning} model={self.model}"
        )


def try_parse_json(content: str, provider: str) -> Any | None:
    text = content.strip()
    # Groq and Kimi emit a fence even under ``response_format=json``.
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
    # Groq/openai-oss occasionally wraps the object in a one-element list; every model is a root object.
    if isinstance(parsed, list) and len(parsed) == 1 and isinstance(parsed[0], dict):
        return parsed[0]
    return parsed


def extract_parsed_json(response: LLMResponse) -> Any:
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
    if not content or not content.strip():
        # An EMPTY body raises here rather than leaking past the schema guard as ``parsed=None``.
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
    # No `usage`: the 400 body carries none, so the default empty account is the honest answer.
    return LLMResponse(
        content=fg_text,
        model=request_params.get("model", ""),
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
