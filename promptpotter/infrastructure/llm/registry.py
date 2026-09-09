"""Provider registry — name → factory. The provider must be supplied EXPLICITLY from the optimizer node's config; there
is no auto-detection and no env-var fallback."""

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
    display_name: str  # e.g. "Groq" — used in error messages + logs
    api_key_attr: str  # settings field holding the API key
    base_url: str | None = None  # None ⇒ SDK default (OpenAI)
    max_retries: int = 5
    timeout: float | None = None
    # Whether this provider answers OpenRouter's `usage: {include: true}` body field, which
    # itemizes what the call cost and how much of the prompt its cache served. False by
    # default: an unknown body key is a 400 on the providers that lack the extension.
    usage_accounting: bool = False


@dataclass(frozen=True)
class ModelProfile:
    """What we MEASURED about one model's serving behaviour. A row IS the claim that this model
    reasons — ``model_profile`` answers ``None`` for anything unprofiled, so an unknown model is
    never assumed to reason and can block no run."""

    # Below this floor a reasoning model spends its whole output budget thinking and emits nothing;
    # `preflight.check_model_reasoning_floors` turns that paid-for silence into a block.
    min_max_tokens: int = 0
    # Rungs the endpoint REFUSES — the only thing that narrows the offered ladder, since no
    # catalogue publishes a value set. Applied in `capabilities.py`.
    refuses_efforts: frozenset[str] = frozenset()
    # Rungs measured to produce the SAME call — never subtracted, only reported. THREE-STATE, and
    # the middle one is the trap: `None` is unprobed, `frozenset()` is probed and all distinct.
    indistinct_efforts: frozenset[str] | None = None


# Per-model profiles, keyed by the normalized ``org/model`` id — ONLY the models we run today, and
# a model absent here simply gets no measured layer. Fill one with `probe-reasoning <model>`.
# What each measurement means per dataset: ``docs/operations/dataset-reasoning-matrix.md``.
_MODEL_PROFILES: dict[str, ModelProfile] = {
    "deepseek/deepseek-v4-flash": ModelProfile(min_max_tokens=8000),
    "openai/gpt-oss-20b": ModelProfile(
        min_max_tokens=8000,
        refuses_efforts=frozenset({"none"}),
        indistinct_efforts=frozenset(),
    ),
    "openai/gpt-oss-120b": ModelProfile(
        min_max_tokens=8000,
        refuses_efforts=frozenset({"none"}),
        indistinct_efforts=frozenset(),
    ),
    "qwen/qwen3.7-flash": ModelProfile(
        indistinct_efforts=frozenset({"minimal", "low", "medium", "high"})
    ),
    "inclusionai/ling-3.0-flash": ModelProfile(
        indistinct_efforts=frozenset({"minimal", "low", "medium", "high"})
    ),
}


def normalize_model_id(model: str) -> str:
    """A model's IDENTITY, without the routing suffix — ``:nitro`` picks a HOST, and every host of
    one model takes the same parameters."""
    return model.split(":", 1)[0].strip().lower()


def model_profile(model: str) -> ModelProfile | None:
    return _MODEL_PROFILES.get(normalize_model_id(model))


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
    # `:nitro` asks OpenRouter for the fastest provider. The catch is that the fastest provider
    # need not implement `response_format`, and OpenRouter drops the parameter rather than refusing
    # the route: `openai/gpt-oss-120b:nitro` lands on Cerebras, which intermittently returns the
    # SCHEMA ITSELF instead of an instance — valid JSON, finish_reason=stop, no payload. Rule:
    # never point a schema-bearing node at a new :nitro route without re-running the schema probe
    # — the failure corrupts the search silently instead of erroring.
    "openrouter": ProviderSpec(
        "OpenRouter",
        "OPENROUTER_API_KEY",
        base_url="https://openrouter.ai/api/v1",
        usage_accounting=True,
    ),
}


def _rate_caps(provider: str) -> tuple[int | None, int | None]:
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
        usage_accounting=spec.usage_accounting,
    )


def _make_anthropic_client() -> AnthropicClient:
    rpm, tpm = _rate_caps("anthropic")
    return AnthropicClient(rate_limiter=build_rate_limiter(rpm, tpm))


def _make_mock_client() -> LLMClientBase:
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
    """The LLM client for ``provider``, one instance per provider per process. Cached because rate-limiter state is
    per-provider-account and rightly shared across cycles, and the lazy SDK pool should be built once."""
    factory = _PROVIDER_FACTORIES.get(provider)
    if factory is None:
        valid = ", ".join(sorted(_PROVIDER_FACTORIES))
        raise ValueError(f"Unknown LLM provider: {provider!r}. Valid: {valid}.")
    return factory()


__all__ = ["ModelProfile", "ProviderSpec", "get_llm_client", "model_profile"]
