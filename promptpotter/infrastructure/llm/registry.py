from __future__ import annotations

import functools
import hashlib
from dataclasses import dataclass

from promptpotter.config.settings import settings
from promptpotter.domain.pipeline_overlay import normalize_model_id
from promptpotter.domain.spend import KeySource, draws_coupon
from promptpotter.infrastructure.llm.base import ProviderKey, SendPacing
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


# Keyed by the normalized ``org/model`` id; a row is read off a round whose arms differ in `reasoning_effort`.
_MODEL_PROFILES: dict[str, ModelProfile] = {
    "deepseek/deepseek-v4-flash": ModelProfile(min_max_tokens=8000),
    # DigitalOcean (the `:nitro` pick) answers half of an L4 outer `l1_generate` with a schema skeleton.
    "minimax/minimax-m3.1-flash": ModelProfile(default_route=("Together",)),
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


def providers() -> frozenset[str]:
    """Every provider a node may name, and so every one an account may hold a key for."""
    return frozenset(_OPENAI_COMPAT_SPECS)


def host_key_attr(provider: str) -> str:
    """The `.env` name of *provider*'s HOST key: the box's wallet, which the coupon meters."""
    return _OPENAI_COMPAT_SPECS[provider].api_key_attr


def host_key(provider: str) -> ProviderKey | None:
    """``None`` where the box holds no key for *provider*."""
    secret = getattr(settings, host_key_attr(provider))
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


def _rate_caps(provider: str, paid_by: KeySource) -> tuple[int | None, int | None]:
    """`RATE_LIMITS` are the box's provider accounts' caps: an account's own key learns its own."""
    caps = (settings.RATE_LIMITS.get(provider) or []) if draws_coupon(paid_by) else []
    return (caps[0] if len(caps) > 0 else None, caps[1] if len(caps) > 1 else None)


# Bounded: under open signup each account's own key is one more account paced.
_ACCOUNTS_PACED = 256


@functools.lru_cache(maxsize=_ACCOUNTS_PACED)
def send_pacing(provider: str, _account: str, paid_by: KeySource) -> SendPacing:
    """``_account`` is the key's DIGEST, which only keys what is held: no secret outlives a send."""
    rpm, tpm = _rate_caps(provider, paid_by)
    return SendPacing(build_rate_limiter(rpm, tpm))


def get_llm_client(provider: str, key: ProviderKey) -> OpenAICompatibleClient:
    """A client for one resolution, sharing its ACCOUNT's pacing across clients and cycles.

    The key is the caller's resolution (`wallet.py::paying_key`), never
    read here: which wallet a send draws is an account's question, not a provider's."""
    spec = _OPENAI_COMPAT_SPECS.get(provider)
    if spec is None:
        valid = ", ".join(sorted(_OPENAI_COMPAT_SPECS))
        raise ValueError(f"Unknown LLM provider: {provider!r}. Valid: {valid}.")
    account = hashlib.sha256(key.secret.get_secret_value().encode()).hexdigest()
    pacing = send_pacing(provider, account, key.paid_by)
    return OpenAICompatibleClient(key, pacing, provider=provider, spec=spec)
