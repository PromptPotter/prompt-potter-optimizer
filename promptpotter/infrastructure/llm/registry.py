from __future__ import annotations

import functools
from collections.abc import Callable
from dataclasses import dataclass

from promptpotter.config.settings import settings
from promptpotter.infrastructure.llm.anthropic import AnthropicClient
from promptpotter.infrastructure.llm.base import LLMClientBase
from promptpotter.infrastructure.llm.openai_compat import OpenAICompatibleClient, ProviderSpec
from promptpotter.infrastructure.llm.send_pacing import build_rate_limiter


@dataclass(frozen=True)
class ModelProfile:
    """A row IS the claim that this model reasons: an unprofiled model is never assumed to."""

    # Below this a reasoning model spends its whole output budget thinking and emits nothing.
    min_max_tokens: int = 0
    refuses_efforts: frozenset[str] = frozenset()
    # Never subtracted, only reported. `None` is unprobed; `frozenset()` is probed, all distinct.
    indistinct_efforts: frozenset[str] | None = None


# Keyed by the normalized ``org/model`` id; fill one with `probe-reasoning <model>`.
_MODEL_PROFILES: dict[str, ModelProfile] = {
    "deepseek/deepseek-v4-flash": ModelProfile(min_max_tokens=8000),
    "openai/gpt-6-luna": ModelProfile(min_max_tokens=8000),
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
    """``:nitro`` picks a HOST, and every host of one model takes the same parameters."""
    return model.split(":", 1)[0].strip().lower()


def model_profile(model: str) -> ModelProfile | None:
    return _MODEL_PROFILES.get(normalize_model_id(model))


_OPENAI_COMPAT_SPECS: dict[str, ProviderSpec] = {
    "groq": ProviderSpec(
        "Groq",
        "GROQ_API_KEY",
        base_url="https://api.groq.com/openai/v1",
        timeout=60.0,
    ),
    "openai": ProviderSpec(
        "OpenAI",
        "OPENAI_API_KEY",
    ),
    # A `:nitro` host may silently drop `response_format`: re-run the schema probe on a new route.
    "openrouter": ProviderSpec(
        "OpenRouter",
        "OPENROUTER_API_KEY",
        base_url="https://openrouter.ai/api/v1",
        gateway=True,
    ),
}


def openai_compat_spec(provider: str) -> ProviderSpec | None:
    return _OPENAI_COMPAT_SPECS.get(provider)


def _rate_caps(provider: str) -> tuple[int | None, int | None]:
    caps = settings.RATE_LIMITS.get(provider) or []
    return (caps[0] if len(caps) > 0 else None, caps[1] if len(caps) > 1 else None)


def _make_openai_compat(provider: str, spec: ProviderSpec) -> OpenAICompatibleClient:
    rpm, tpm = _rate_caps(provider)
    return OpenAICompatibleClient(
        api_key=getattr(settings, spec.api_key_attr),
        provider=provider,
        spec=spec,
        rate_limiter=build_rate_limiter(rpm, tpm),
    )


def _make_anthropic_client() -> AnthropicClient:
    rpm, tpm = _rate_caps("anthropic")
    return AnthropicClient(rate_limiter=build_rate_limiter(rpm, tpm))


_PROVIDER_FACTORIES: dict[str, Callable[[], LLMClientBase]] = {
    **{
        name: functools.partial(_make_openai_compat, name, spec)
        for name, spec in _OPENAI_COMPAT_SPECS.items()
    },
    "anthropic": _make_anthropic_client,
}


@functools.cache
def get_llm_client(provider: str) -> LLMClientBase:
    """Cached: rate-limiter state is per provider account and shared across cycles."""
    factory = _PROVIDER_FACTORIES.get(provider)
    if factory is None:
        valid = ", ".join(sorted(_PROVIDER_FACTORIES))
        raise ValueError(f"Unknown LLM provider: {provider!r}. Valid: {valid}.")
    return factory()


__all__ = [
    "ModelProfile",
    "get_llm_client",
    "model_profile",
    "openai_compat_spec",
]
