"""Data types crossing the LLM client boundary.

``LLMResponse`` — the standardized response object every client returns.
``TokenUsage`` — per-call token record + emission registry. The module-level
sink is installed by the runner so optimizer ``TokenUsage`` events reach
the cycle ledger via ``RunCallbacks.on_token_usage``.
"""

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


@dataclass(frozen=True)
class TokenUsage:
    """Per-call LLM token record.

    ``model`` is the provider's reported model id (``response.model``)
    so downstream cost resolution can rate-table it without re-deriving
    the call site's config. ``cost_usd`` is populated when the provider
    returned a USD figure on the wire (today only OpenRouter does this);
    callers leave it ``None`` when the rate-table path should run.

    Attributes:
        node: Logical node name (optimizer: ``"l1_generate"``, ``"l1_critique"``,
            …; backend: ``"entity_profiling"``, ``"llm_ranking"``, …).
        kind: ``"optimizer"`` for meta-prompt calls, ``"backend"`` for
            in-pipeline LLM calls. Sinks use this to threshold separately.
        input_tokens: Prompt (input) tokens reported by the provider.
        output_tokens: Completion (output) tokens reported by the provider.
        duration_s: Wall-clock call duration in seconds.
        model: Provider model id (``None`` if unknown).
        cost_usd: Provider-reported USD cost (``None`` if not provided).
    """

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


# Module-level sink — installed by the runner so optimizer ``TokenUsage``
# events reach the cycle ledger via ``RunCallbacks.on_token_usage``.
# Default is a no-op; ``llm_call.py`` imports the function below, not
# the slot, so re-binding mid-run is safe.
_token_usage_sink: Callable[[TokenUsage], None] | None = None


def set_token_usage_sink(sink: Callable[[TokenUsage], None] | None) -> None:
    """Install (or clear) the cross-cutting sink for ``emit_token_usage``.

    The runner calls this once with ``RunCallbacks.on_token_usage`` and
    clears to ``None`` on teardown. ``emit_token_usage`` always runs the
    overflow-warning logic — the sink is additive.
    """
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
