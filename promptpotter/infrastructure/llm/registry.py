from __future__ import annotations

import functools
from collections.abc import Callable
from dataclasses import dataclass

from promptpotter.config.settings import settings
from promptpotter.domain.pipeline_overlay import normalize_model_id
from promptpotter.infrastructure.llm.anthropic import AnthropicClient
from promptpotter.infrastructure.llm.base import LLMClientBase, ProviderKey
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
    # The hosts a gateway tries for a node naming none: hosts of one model disagree systematically.
    default_route: tuple[str, ...] = ()


# Keyed by the normalized ``org/model`` id; fill one with `probe-reasoning <model>`.
_MODEL_PROFILES: dict[str, ModelProfile] = {
    "deepseek/deepseek-v4-flash": ModelProfile(min_max_tokens=8000),
    "openai/gpt-6-luna": ModelProfile(min_max_tokens=8000, default_route=("OpenAI",)),
    "openai/gpt-oss-20b": ModelProfile(
        min_max_tokens=8000,
        refuses_efforts=frozenset({"none"}),
        indistinct_efforts=frozenset(),
        default_route=("Groq",),
    ),
    "openai/gpt-oss-120b": ModelProfile(
        min_max_tokens=8000,
        refuses_efforts=frozenset({"none"}),
        indistinct_efforts=frozenset(),
        default_route=("Cerebras",),
    ),
    "qwen/qwen3.7-flash": ModelProfile(
        indistinct_efforts=frozenset({"minimal", "low", "medium", "high"})
    ),
    "inclusionai/ling-3.0-flash": ModelProfile(
        indistinct_efforts=frozenset({"minimal", "low", "medium", "high"})
    ),
}


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
        base_url="https://api.openai.com/v1",
    ),
    # A `:nitro` host may silently drop `response_format`: re-run the schema probe on a new route.
    "openrouter": ProviderSpec(
        "OpenRouter",
        "OPENROUTER_API_KEY",
        base_url="https://openrouter.ai/api/v1",
        gateway=True,
        # Its `/models` answers without a key.
        key_check="/key",
    ),
}


def openai_compat_spec(provider: str) -> ProviderSpec | None:
    return _OPENAI_COMPAT_SPECS.get(provider)


# The `.env` name of each provider's HOST key: the box's wallet, which the coupon meters.
_HOST_KEY_ATTRS: dict[str, str] = {
    **{name: spec.api_key_attr for name, spec in _OPENAI_COMPAT_SPECS.items()},
    "anthropic": "ANTHROPIC_API_KEY",
}


def providers() -> frozenset[str]:
    """Every provider a node may name, and so every one an account may hold a key for."""
    return frozenset(_HOST_KEY_ATTRS)


def host_key_attr(provider: str) -> str:
    return _HOST_KEY_ATTRS[provider]


def host_key(provider: str) -> ProviderKey | None:
    """``None`` where the box holds no key for *provider*."""
    secret = getattr(settings, _HOST_KEY_ATTRS[provider])
    return ProviderKey(secret, "host") if secret.get_secret_value().strip() else None


def keyed_specs() -> dict[str, tuple[ProviderSpec, str]]:
    """Each OpenAI-compatible provider this box holds a key for, with that key."""
    return {
        name: (spec, key.secret.get_secret_value().strip())
        for name, spec in _OPENAI_COMPAT_SPECS.items()
        if (key := host_key(name)) is not None
    }


def default_routes() -> dict[str, dict[str, list[str]]]:
    """Per gateway: a route names a gateway's hosts, so a direct provider takes none."""
    pins = {m: list(p.default_route) for m, p in _MODEL_PROFILES.items() if p.default_route}
    return {name: pins for name, spec in _OPENAI_COMPAT_SPECS.items() if spec.gateway and pins}


def _rate_caps(provider: str, key: ProviderKey) -> tuple[int | None, int | None]:
    """`RATE_LIMITS` are the box's provider accounts' caps: an account's own key learns its own."""
    caps = (settings.RATE_LIMITS.get(provider) or []) if key.paid_by == "host" else []
    return (caps[0] if len(caps) > 0 else None, caps[1] if len(caps) > 1 else None)


def _make_openai_compat(
    provider: str, spec: ProviderSpec, key: ProviderKey
) -> OpenAICompatibleClient:
    rpm, tpm = _rate_caps(provider, key)
    return OpenAICompatibleClient(
        key, provider=provider, spec=spec, rate_limiter=build_rate_limiter(rpm, tpm)
    )


def _make_anthropic_client(key: ProviderKey) -> AnthropicClient:
    rpm, tpm = _rate_caps("anthropic", key)
    return AnthropicClient(key, rate_limiter=build_rate_limiter(rpm, tpm))


_PROVIDER_FACTORIES: dict[str, Callable[[ProviderKey], LLMClientBase]] = {
    **{
        name: functools.partial(_make_openai_compat, name, spec)
        for name, spec in _OPENAI_COMPAT_SPECS.items()
    },
    "anthropic": _make_anthropic_client,
}

# Bounded: under open signup each account's own key is one more client held.
_CLIENTS_HELD = 256


@functools.lru_cache(maxsize=_CLIENTS_HELD)
def get_llm_client(provider: str, key: ProviderKey) -> LLMClientBase:
    """Cached per KEY: rate-limiter state is per provider account and shared across cycles.

    The key is the caller's resolution (`application/jobs/coupon.py::resolve_api_key`), never
    read here: which wallet a send draws is an account's question, not a provider's."""
    factory = _PROVIDER_FACTORIES.get(provider)
    if factory is None:
        valid = ", ".join(sorted(_PROVIDER_FACTORIES))
        raise ValueError(f"Unknown LLM provider: {provider!r}. Valid: {valid}.")
    return factory(key)
