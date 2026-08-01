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

# Below this, content is "the provider returned nothing", not an attempt at the schema.
# ONE home for the threshold: the client picks its retry STRATEGY with it and the error
# classifies itself with it, so a call retried as a flake cannot then be scored as a
# prompt fault (or the reverse) because two modules drew the line differently.
MIN_CONTENT_CHARS = 20

# Retry strategies (``OptimizerPromptParseError.retry_kind``). See ``OpenAICompatibleClient.chat``.
RETRY_CLEAN_REASK = "clean_reask"
RETRY_SCHEMA_REPAIR = "schema_repair"


class OptimizerPromptParseError(RuntimeError):
    """LLM returned content that failed Pydantic validation after one repair-hint retry.

    Carries raw + last ValidationError → L1 records Wound 1, L2 heals next round.

    Also carries the provider's own account of WHY, because the dominant failure is an
    empty ``content`` and the raw string alone cannot distinguish its causes: a reasoning
    model that spent its whole ``max_tokens`` budget on reasoning (``finish_reason:
    length``, large ``reasoning_tokens``) looks identical to a provider that simply
    returned nothing (``finish_reason: stop``, ``completion_tokens: 2``). The caller
    (``dispatch/llm_call/call.py``) meters the burned tokens off this record — the call
    is billed whether or not it parsed, and it is the sole ``emit_token_usage`` site.

    **Two round-trips, two accounts.** ``finish_reason`` / ``reasoning_*`` describe the
    REPAIR attempt; ``first_finish_reason`` / ``first_content_chars`` describe the attempt
    that actually failed. Both are carried because the repair's account cannot answer the
    question the classification asks. The repair re-sends the original prompt PLUS the
    whole failed output and demands the same large answer again under the same
    ``max_tokens``, so for a size-driven failure it is more likely to come back empty than
    the first call was — and an empty repair says nothing about why the first attempt was
    rejected.
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
        """Did the second attempt fail the SAME way as the first?

        Only meaningful after a clean re-ask — the identical request sent twice, i.e. a
        second independent sample (no optimizer node pins a seed and all run at
        temperature 0.3–0.5). Same ``finish_reason`` and the same side of the
        empty/non-empty line ⇒ the failure is a property of the request, not of the
        moment. A *seeded* request would reproduce by construction, which only makes this
        more trustworthy, never less: determinism cannot disguise a flake as a fault.
        """
        first_empty = (self.first_content_chars or 0) < MIN_CONTENT_CHARS
        return self.first_finish_reason == self.finish_reason and first_empty == (
            self.raw_chars < MIN_CONTENT_CHARS
        )

    @property
    def is_empty(self) -> bool:
        """Provider degraded (returned nothing) — vs output the OPTIMIZER PROMPT owns.

        Steers L2's heal direction, and downstream decides whether an L4 outer round
        counts at all (``domain/l4/proxies.py`` excludes a ``L1_PARSE_FAILURE_TOOLING``
        round as missing data), so a wrong answer here does not just mislabel — it deletes
        evidence. Three tests, most decisive first:

        1. ``finish_reason == "length"`` on the failing attempt is NOT provider
           degradation, however empty the content. Truncation means the optimizer prompt asked
           for more than the token budget could carry — its own fault.
        2. **The failure reproduced under a clean re-ask.** This is measured, not inferred:
           the same request was sent twice and failed the same way both times, so it is a
           property of the prompt. The whole point of choosing that retry strategy
           (``chat()``) is to buy this answer instead of guessing at it.
        3. No experiment available (the schema-repair path, or a legacy record with no
           first-attempt data) — fall back to the shape of the failing attempt.
        """
        if self.first_finish_reason == "length":
            return False
        if self.retry_kind == RETRY_CLEAN_REASK and self.reproduced:
            return False
        return self.failing_chars < MIN_CONTENT_CHARS

    def warning_detail(self) -> dict[str, Any]:
        """The provider's own account of WHY, for a round warning's disk-bound
        ``detail``. ``finish_reason: length`` + a large ``reasoning_tokens`` means
        the optimizer prompt is too big for the token budget (fix the prompt); ``stop``
        with ~2 completion tokens means the provider degraded (retry/route
        elsewhere). Same symptom, opposite fix — so both land on disk rather than
        only in the log."""
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
        """One-line, disk-bound account of the failure — the wound's ``value`` and the log.

        Per ATTEMPT, because the two are different events with different causes and the
        fix differs by which one you are reading.
        """
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


def extract_parsed_json(response: LLMResponse) -> Any:
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
    "MIN_CONTENT_CHARS",
    "RETRY_CLEAN_REASK",
    "RETRY_SCHEMA_REPAIR",
    "OptimizerPromptParseError",
    "extract_parsed_json",
    "parse_response_content",
    "try_groq_json_validate_repair",
    "try_parse_json",
]
