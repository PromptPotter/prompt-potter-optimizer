"""LLM client abstraction layer.

Providers: Groq, OpenAI, OpenRouter (via :class:`OpenAICompatibleClient` over
the ``openai`` SDK with ``base_url`` swap) and Anthropic (via
:class:`AnthropicClient` over the ``anthropic`` SDK). Chat completions, JSON
mode, token tracking. Retry and ``Retry-After`` honoring are delegated to
the provider SDKs (``max_retries`` kwarg on ``AsyncOpenAI`` / ``AsyncAnthropic``).

Provider selection is always explicit — caller passes
:func:`get_llm_client` ``(provider)``, typically sourced from
``CampaignConfig.optimizer_llm.provider``. There is no auto-detection or
env-var fallback.

Client-side tier throttling (RPM + TPM) is opt-in via ``*_RPM`` / ``*_TPM``
settings — see :mod:`.rate_limit`.
"""

from __future__ import annotations

from promptpotter.infrastructure.llm.anthropic import AnthropicClient
from promptpotter.infrastructure.llm.base import LLMClientBase
from promptpotter.infrastructure.llm.json_parse import (
    extract_parsed_json,
    try_parse_json,
)
from promptpotter.infrastructure.llm.models import (
    LLMResponse,
    TokenUsage,
    emit_token_usage,
    set_token_usage_sink,
)
from promptpotter.infrastructure.llm.openai_compat import OpenAICompatibleClient
from promptpotter.infrastructure.llm.rate_limit import (
    DEPR_RETRY_COOLDOWN_SEC,
    MAX_429_ATTEMPTS,
    RateLimiter,
    RateLimitReservation,
    diagnose_rate_limit_scope,
    estimate_tokens,
    parse_retry_after,
    wait_with_countdown,
)
from promptpotter.infrastructure.llm.registry import ProviderSpec, get_llm_client

__all__ = [
    "DEPR_RETRY_COOLDOWN_SEC",
    "MAX_429_ATTEMPTS",
    "AnthropicClient",
    "LLMClientBase",
    "LLMResponse",
    "OpenAICompatibleClient",
    "ProviderSpec",
    "RateLimitReservation",
    "RateLimiter",
    "TokenUsage",
    "diagnose_rate_limit_scope",
    "emit_token_usage",
    "estimate_tokens",
    "extract_parsed_json",
    "get_llm_client",
    "parse_retry_after",
    "set_token_usage_sink",
    "try_parse_json",
    "wait_with_countdown",
]
