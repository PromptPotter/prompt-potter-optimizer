"""Data types crossing the LLM client boundary.

- ``LLMResponse`` — standardized response every client returns.
- ``TokenUsage`` + emission sink — runner installs a sink so optimizer events reach
  the ledger via ``RunCallbacks.on_token_usage``."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from promptpotter.config.settings import settings

logger = logging.getLogger(__name__)


class LLMResponse(BaseModel):
    """Standardized response from LLM providers."""

    content: str = Field(..., description="Response content")
    model: str = Field(..., description="Model used")
    usage: dict[str, int] = Field(
        default_factory=dict,
        description="Token usage: prompt_tokens, completion_tokens, total_tokens",
    )
    finish_reason: str | None = Field(None, description="Why generation stopped")
    parsed: Any | None = Field(
        None,
        description=(
            "Parsed response. Typed Pydantic instance when ``chat`` was "
            "called with ``response_model``; plain dict when only "
            "``response_schema`` was supplied; ``None`` for text-mode."
        ),
    )
    schema_repair_attempts: int = Field(
        0,
        description=(
            "Times the schema-validation repair-retry path fired before "
            "the parsed response landed. 0 = clean first parse; 1 = one "
            "repair round-trip. Surfaces L1-prompt parse-failure rate as a "
            "quality signal — bad templates produce schema-noncompliant "
            "JSON and silently double-up the LLM spend."
        ),
    )


@dataclass(frozen=True)
class TokenUsage:
    """Per-call LLM token record.

    ``model`` = provider-reported model id (``response.model``) so cost resolution
    rate-tables it without re-deriving the call site's config. ``cost_usd``
    populated when the provider ships USD on the wire (today: OpenRouter);
    ``None`` ⇒ rate-table path runs.

    ``kind="optimizer"`` (meta-prompt) vs ``"backend"`` (in-pipeline) — sinks
    threshold separately."""

    node: str
    kind: Literal["optimizer", "backend"]
    input_tokens: int
    output_tokens: int
    duration_s: float
    model: str | None = None
    cost_usd: float | None = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


# Module-level sink installed by the runner; default no-op. ``llm_call.py``
# imports the function, not the slot, so mid-run re-binding is safe.
_token_usage_sink: Callable[[TokenUsage], None] | None = None


def set_token_usage_sink(sink: Callable[[TokenUsage], None] | None) -> None:
    """Install (or clear) the sink for ``emit_token_usage``; additive (overflow-warning always runs)."""
    global _token_usage_sink
    _token_usage_sink = sink


def emit_token_usage(usage: TokenUsage) -> None:
    """Warn on overlong optimizer prompts and forward to the active sink."""
    if usage.kind == "optimizer":
        threshold = settings.OPTIMIZER_PROMPT_WARN_TOKENS
        if usage.input_tokens > threshold:
            logger.warning(
                "optimizer node %r prompt at %d tokens (threshold=%d) — tune the "
                "template or drop context; large meta-prompts reduce signal-to-noise "
                "for the optimizer LLM and risk provider TPM caps",
                usage.node,
                usage.input_tokens,
                threshold,
            )
    sink = _token_usage_sink
    if sink is not None:
        try:
            sink(usage)
        except Exception:
            logger.exception("token_usage sink raised; suppressing")


__all__ = ["LLMResponse", "TokenUsage", "emit_token_usage", "set_token_usage_sink"]
