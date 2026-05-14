"""Provider registry — name → factory.

``get_llm_client(provider)`` returns the configured client for one of the
known provider names. Provider must be supplied explicitly — typically
from ``CampaignConfig.optimizer_llm.provider``. There is no
auto-detection or env-var fallback.
"""

from __future__ import annotations

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
    rpm_attr: str  # settings field for rolling RPM cap
    tpm_attr: str  # settings field for rolling TPM cap
    base_url: str | None = None  # None ⇒ SDK default (OpenAI)
    max_retries: int = 5
    timeout: float | None = None


_OPENAI_COMPAT_SPECS: dict[str, ProviderSpec] = {
    "groq": ProviderSpec(
        "Groq",
        "GROQ_API_KEY",
        "GROQ_RPM",
        "GROQ_TPM",
        base_url="https://api.groq.com/openai/v1",
        max_retries=3,
        timeout=60.0,
    ),
    "openai": ProviderSpec(
        "OpenAI",
        "OPENAI_API_KEY",
        "OPENAI_RPM",
        "OPENAI_TPM",
    ),
    "openrouter": ProviderSpec(
        "OpenRouter",
        "OPENROUTER_API_KEY",
        "OPENROUTER_RPM",
        "OPENROUTER_TPM",
        base_url="https://openrouter.ai/api/v1",
    ),
}


def _make_openai_compat(spec: ProviderSpec) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        api_key=getattr(settings, spec.api_key_attr),
        base_url=spec.base_url,
        max_retries=spec.max_retries,
        timeout=spec.timeout,
        provider_name=spec.display_name,
        rate_limiter=build_rate_limiter(
            getattr(settings, spec.rpm_attr),
            getattr(settings, spec.tpm_attr),
        ),
    )


def _make_anthropic_client() -> AnthropicClient:
    return AnthropicClient(
        rate_limiter=build_rate_limiter(settings.ANTHROPIC_RPM, settings.ANTHROPIC_TPM),
    )


def _make_mock_client() -> LLMClientBase:
    """Lazy-load MockLLMClient (lives in tests package)."""
    try:
        from tests.mock_llm_client import MockLLMClient  # type: ignore[import-not-found]
    except ImportError as err:
        raise ValueError("Test mock unavailable outside the test environment.") from err
    return MockLLMClient()


def _bind_openai_compat(spec: ProviderSpec) -> Callable[[], LLMClientBase]:
    return lambda: _make_openai_compat(spec)


_PROVIDER_FACTORIES: dict[str, Callable[[], LLMClientBase]] = {
    **{name: _bind_openai_compat(spec) for name, spec in _OPENAI_COMPAT_SPECS.items()},
    "anthropic": _make_anthropic_client,
    "mock": _make_mock_client,
}


def get_llm_client(provider: str) -> LLMClientBase:
    """Construct the LLM client for ``provider``.

    Provider must be supplied explicitly — typically from
    ``CampaignConfig.optimizer_llm.provider``. There is no auto-detection
    or env-var fallback.
    """
    factory = _PROVIDER_FACTORIES.get(provider)
    if factory is None:
        valid = ", ".join(sorted(_PROVIDER_FACTORIES))
        raise ValueError(f"Unknown LLM provider: {provider!r}. Valid: {valid}.")
    return factory()


__all__ = ["ProviderSpec", "get_llm_client"]
