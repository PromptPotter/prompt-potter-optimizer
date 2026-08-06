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


@dataclass(frozen=True)
class ModelProfile:
    """Static facts about one model's serving behaviour. A REASONING model spends output budget on hidden tokens before emitting content, so
    ``max_tokens`` below ``min_max_tokens`` returns nothing — the floor turns that paid-for silence into a preflight block."""

    is_reasoning: bool
    min_max_tokens: int = 0
    notes: str = ""


# Per-model profiles, keyed by the normalized ``org/model`` id (routing suffix like
# ``:nitro`` stripped). ONLY the models we run today — extend as we add models. The floors
# are first estimates from observed ``reasoning_budget_exhausted`` failures, meant to be
# refined as we measure more; the per-model + per-provider quirks they encode are the prose
# in ``docs/operations/dataset-reasoning-matrix.md`` given a code home. A floor of 8000
# catches the egregious case (l1_critique @ 4000 → 0 content) without flagging the working
# nodes (l2_context/l3_plan @ 8000, checkin @ 10000, l1_generate @ 12000); the complementary
# lever for a reasoning model is keeping ``reasoning_effort`` low so reasoning stays bounded.
_MODEL_PROFILES: dict[str, ModelProfile] = {
    "deepseek/deepseek-v4-flash": ModelProfile(
        is_reasoning=True,
        min_max_tokens=8000,
        notes="Emits ~4k reasoning tokens before content; a 4k cap returned 0 content on "
        "l1_critique. Pair a >=8k budget with reasoning_effort=low.",
    ),
    "openai/gpt-oss-20b": ModelProfile(
        is_reasoning=True,
        min_max_tokens=8000,
        notes="Groq route enforces a ~2048-tok output ceiling that COUNTS reasoning tokens — "
        "keep reasoning_effort<=low; a numeric max_tokens is a per-cycle override, not a default.",
    ),
    "openai/gpt-oss-120b": ModelProfile(
        is_reasoning=True,
        min_max_tokens=8000,
        notes="Over-reasons on long optimizer prompts and returns empty content; keep effort low.",
    ),
}


def model_profile(model: str) -> ModelProfile | None:
    """Profile for a model string, normalizing the id and stripping a routing suffix. ``None`` when unprofiled — an unknown
    model is never ASSUMED to be a reasoning model, so it cannot block a run."""
    base = model.split(":", 1)[0].strip().lower()
    return _MODEL_PROFILES.get(base)


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
