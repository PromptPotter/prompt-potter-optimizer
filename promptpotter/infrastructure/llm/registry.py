"""Provider registry — name → factory.

Provider must be supplied explicitly (the optimizer node's ``config.provider`` in
``datasets/_optimizer/pipeline.json``, read by ``llm_call``); no auto-detection
or env-var fallback."""

from __future__ import annotations

import functools
from collections.abc import Callable
from dataclasses import dataclass

from promptpotter.config.settings import settings
from promptpotter.infrastructure.llm.anthropic import AnthropicClient
from promptpotter.infrastructure.llm.base import LLMClientBase
from promptpotter.infrastructure.llm.openai_compat import OpenAICompatibleClient
from promptpotter.infrastructure.llm.rate_limit import build_rate_limiter


@dataclass(frozen=True)
class ProviderSpec:
    """Wiring for one OpenAI-compatible provider."""

    display_name: str  # e.g. "Groq" — used in error messages + logs
    api_key_attr: str  # settings field holding the API key
    base_url: str | None = None  # None ⇒ SDK default (OpenAI)
    max_retries: int = 5
    timeout: float | None = None


_OPENAI_COMPAT_SPECS: dict[str, ProviderSpec] = {
    "groq": ProviderSpec(
        "Groq",
        "GROQ_API_KEY",
        base_url="https://api.groq.com/openai/v1",
        max_retries=3,
        timeout=60.0,
    ),
    "openai": ProviderSpec(
        "OpenAI",
        "OPENAI_API_KEY",
    ),
    # `:nitro` asks OpenRouter for the fastest provider, and it is where we WANT every node to
    # end up — measured 13x on the optimizer nodes, which are over half this system's wall-clock.
    # The catch is that the fastest provider need not implement `response_format`, and OpenRouter
    # drops the parameter rather than refusing the route. Measured 2026-07-13:
    # `openai/gpt-oss-120b:nitro` lands on Cerebras and, at today's prompt length, intermittently
    # returns the SCHEMA ITSELF instead of an instance — valid JSON, finish_reason=stop, no
    # payload. So the optimizer nodes run plain FOR NOW (see `datasets/_optimizer/pipeline.json`):
    # shorten the prompts, re-probe, then turn :nitro on. Free-text / backend target nodes carry it
    # already. Rule: never flip a schema-bearing node to :nitro without re-running the schema probe
    # — the failure corrupts the search silently instead of erroring.
    "openrouter": ProviderSpec(
        "OpenRouter",
        "OPENROUTER_API_KEY",
        base_url="https://openrouter.ai/api/v1",
    ),
}


def _rate_caps(provider: str) -> tuple[int | None, int | None]:
    """The (rpm, tpm) caps for *provider* from ``settings.RATE_LIMITS`` (both None if unset)."""
    caps = settings.RATE_LIMITS.get(provider) or []
    return (caps[0] if len(caps) > 0 else None, caps[1] if len(caps) > 1 else None)


def _make_openai_compat(provider: str, spec: ProviderSpec) -> OpenAICompatibleClient:
    rpm, tpm = _rate_caps(provider)
    return OpenAICompatibleClient(
        api_key=getattr(settings, spec.api_key_attr),
        base_url=spec.base_url,
        max_retries=spec.max_retries,
        timeout=spec.timeout,
        provider_name=spec.display_name,
        rate_limiter=build_rate_limiter(rpm, tpm),
    )


def _make_anthropic_client() -> AnthropicClient:
    rpm, tpm = _rate_caps("anthropic")
    return AnthropicClient(rate_limiter=build_rate_limiter(rpm, tpm))


def _make_mock_client() -> LLMClientBase:
    """Lazy-load MockLLMClient (lives in tests package)."""
    try:
        from tests.mock_llm_client import MockLLMClient
    except ImportError as err:
        raise ValueError("Test mock unavailable outside the test environment.") from err
    client: LLMClientBase = MockLLMClient()
    return client


_PROVIDER_FACTORIES: dict[str, Callable[[], LLMClientBase]] = {
    **{
        name: functools.partial(_make_openai_compat, name, spec)
        for name, spec in _OPENAI_COMPAT_SPECS.items()
    },
    "anthropic": _make_anthropic_client,
    "mock": _make_mock_client,
}


@functools.cache
def get_llm_client(provider: str) -> LLMClientBase:
    """The LLM client for ``provider`` — one instance per provider per process.

    Resolved on every optimizer call now (``llm_call`` reads the per-node
    ``provider``), so it's cached: the rate-limiter state is per-provider-account
    and rightly shared across cycles, and the lazy SDK/httpx pool is built once."""
    factory = _PROVIDER_FACTORIES.get(provider)
    if factory is None:
        valid = ", ".join(sorted(_PROVIDER_FACTORIES))
        raise ValueError(f"Unknown LLM provider: {provider!r}. Valid: {valid}.")
    return factory()


__all__ = ["ProviderSpec", "get_llm_client"]
