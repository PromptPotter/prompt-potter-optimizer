"""
LLM client abstraction layer.

Provides a unified interface via ``OpenAICompatibleClient`` (Groq default,
OpenAI, Anthropic). Chat completions, JSON mode, token tracking. Retry and
``Retry-After`` honoring are delegated to the provider SDKs (``max_retries``
kwarg on ``AsyncOpenAI`` / ``AsyncAnthropic``). ``get_llm_client(provider)``
returns a configured singleton.

Client-side tier throttling (RPM + TPM) is opt-in via ``*_RPM`` / ``*_TPM``
settings — see :mod:`promptpotter.infrastructure.llm.rate_limiter`.
"""

import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from openai import AsyncOpenAI

from pydantic import BaseModel, Field

from promptpotter.config.settings import settings
from promptpotter.infrastructure.llm.rate_limiter import RateLimiter, estimate_tokens
from promptpotter.shared.errors import RequestTooLargeError
from promptpotter.shared.llm_parsing import try_parse_json

# Provider defaults
OPENAI_MAX_RETRIES: int = 5
GROQ_MAX_RETRIES: int = 3
GROQ_TIMEOUT: float = 60.0
GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"

logger = logging.getLogger(__name__)

__all__ = [
    "AnthropicClient",
    "LLMClientBase",
    "LLMResponse",
    "OpenAICompatibleClient",
    "get_llm_client",
]


def _parse_tpm_overflow(err_str: str) -> tuple[int, int] | None:
    """Extract ``(limit, requested)`` from a Groq-style ``"Limit X, Requested Y"`` body."""
    m = re.search(r"Limit\s+(\d+),\s*Requested\s+(\d+)", err_str)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def _parse_int_header(headers: Any, key: str) -> int | None:
    """Read ``key`` from a headers mapping and coerce to int, else ``None``."""
    if headers is None:
        return None
    val = headers.get(key) if hasattr(headers, "get") else None
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _discover_tier_caps(headers: Any) -> tuple[int | None, int | None]:
    """Extract ``(rpm, tpm)`` from standard OpenAI/Groq rate-limit headers."""
    rpm = _parse_int_header(headers, "x-ratelimit-limit-requests")
    tpm = _parse_int_header(headers, "x-ratelimit-limit-tokens")
    return rpm, tpm


def _raise_if_request_too_large(exc: Exception, provider_name: str) -> None:
    """Translate a terminal 413/429 "Requested > Limit" into ``RequestTooLargeError``."""
    status = getattr(exc, "status_code", None)
    if status not in (413, 429):
        return
    parsed = _parse_tpm_overflow(str(exc))
    if parsed is None:
        return
    limit, requested = parsed
    if requested <= limit:
        return
    raise RequestTooLargeError(
        provider_name=provider_name,
        limit=limit,
        requested=requested,
    ) from exc


def _repair_json_validate_failure(err_str: str) -> tuple[str, Any] | None:
    """Salvage Groq's ``json_validate_failed`` 400 by extracting failed_generation.

    Returns ``(content, parsed)`` when the error body carries a recoverable
    ``failed_generation`` block that re-parses as JSON, otherwise ``None``
    (caller should fall through to retry).
    """
    fg_key = "'failed_generation': '"
    fg_start = err_str.find(fg_key)
    if fg_start < 0:
        return None
    fg_text = err_str[fg_start + len(fg_key) :]
    fg_end = fg_text.rfind("'}")
    if fg_end <= 0:
        return None
    fg_text = fg_text[:fg_end].replace("\\n", "\n").replace("\\'", "'")
    parsed = try_parse_json(fg_text, "json_repair")
    if parsed is None:
        return None
    return fg_text, parsed


class LLMResponse(BaseModel):
    """Standardized response from LLM providers."""

    content: str = Field(..., description="Response content")
    model: str = Field(..., description="Model used")
    usage: dict[str, int] = Field(
        default_factory=dict,
        description="Token usage: prompt_tokens, completion_tokens, total_tokens",
    )
    finish_reason: str | None = Field(None, description="Why generation stopped")
    parsed: Any | None = Field(None, description="Parsed JSON if output_format='json'")


class LLMClientBase(ABC):
    """Abstract base class for LLM clients."""

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        output_format: Literal["text", "json", "json_schema"] = "text",
        json_schema: dict | None = None,
        **kwargs,
    ) -> LLMResponse:
        """Send a chat completion request.

        Args:
            messages: List of message dicts with 'role' and 'content'.
            model: Model identifier (uses default if not specified).
            temperature: Sampling temperature (0.0 = deterministic).
            max_tokens: Maximum response tokens. ``None`` = no cap (provider
                default — typically the model's output ceiling).
            output_format: "text", "json" (plain JSON mode), or "json_schema"
                (structured output with the provided ``json_schema``). When
                "json_schema" is selected, ``json_schema`` MUST be supplied
                and the provider must support ``response_format=json_schema``
                — no graceful fallback; a rejection surfaces as-is.
            json_schema: OpenAI-compatible JSON Schema dict. Required when
                ``output_format == "json_schema"``.
            **kwargs: Additional provider-specific parameters.

        Returns:
            LLMResponse with content and usage info.
        """
        ...


class OpenAICompatibleClient(LLMClientBase):
    """Client for any OpenAI-compatible API (OpenAI, Groq, etc.)."""

    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        max_retries: int = OPENAI_MAX_RETRIES,
        timeout: float | None = None,
        default_model: str | None = None,
        provider_name: str = "openai",
        rate_limiter: RateLimiter | None = None,
    ):
        self._api_key = api_key
        self._base_url = base_url
        self._max_retries = max_retries
        self._timeout = timeout
        self._default_model = default_model or settings.LLM_MODEL
        self._provider_name = provider_name
        self._rate_limiter = rate_limiter
        self._client: AsyncOpenAI | None = None

    def _ensure_client(self) -> "AsyncOpenAI":
        """Lazy-initialize the async OpenAI client."""
        if self._client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError as err:
                raise ImportError("openai package not installed. Run: pip install openai") from err

            kwargs: dict[str, Any] = {
                "api_key": self._api_key,
                "max_retries": self._max_retries,
            }
            if self._base_url:
                kwargs["base_url"] = self._base_url
            if self._timeout:
                kwargs["timeout"] = self._timeout
            self._client = AsyncOpenAI(**kwargs)
        return self._client

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        output_format: Literal["text", "json", "json_schema"] = "text",
        json_schema: dict | None = None,
        **kwargs,
    ) -> LLMResponse:
        client = self._ensure_client()

        request_params: dict[str, Any] = {
            "model": model or self._default_model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            request_params["max_tokens"] = max_tokens
        if output_format == "json":
            request_params["response_format"] = {"type": "json_object"}
        elif output_format == "json_schema":
            if not json_schema:
                raise ValueError("output_format='json_schema' requires json_schema arg")
            request_params["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": json_schema.get("name", "response_schema"),
                    "schema": json_schema.get("schema", json_schema),
                    "strict": json_schema.get("strict", False),
                },
            }

        request_params.update(kwargs)

        # Proactive tier check + throttle. If the configured TPM cap can never
        # fit this request, fail fast before any network call. Otherwise block
        # until sending it stays within the rolling-window budget.
        estimated = estimate_tokens(messages, max_tokens)
        if self._rate_limiter is not None:
            if self._rate_limiter.tpm is not None and estimated > self._rate_limiter.tpm:
                raise RequestTooLargeError(
                    provider_name=self._provider_name,
                    limit=self._rate_limiter.tpm,
                    requested=estimated,
                )
            await self._rate_limiter.acquire(estimated)

        # SDK's own ``max_retries`` handles 408/409/429/5xx + Retry-After.
        # We only intercept for: terminal request-too-large (413/429 with
        # Requested > Limit), 404 model-not-found, and Groq's 400
        # json_validate_failed quirk. Use ``with_raw_response`` so we can
        # self-tune the limiter from ``x-ratelimit-limit-*`` headers.
        try:
            raw = await client.chat.completions.with_raw_response.create(**request_params)
            response = raw.parse()
        except Exception as exc:
            recovered = self._try_recover_from_chat_error(exc, request_params)
            if recovered is not None:
                return recovered
            raise

        if self._rate_limiter is not None:
            discovered_rpm, discovered_tpm = _discover_tier_caps(raw.headers)
            self._rate_limiter.apply_discovered(discovered_rpm, discovered_tpm)

        if not response.choices:
            raise ValueError(f"{self._provider_name} returned empty choices")
        content = response.choices[0].message.content or ""
        parsed = (
            try_parse_json(content, self._provider_name)
            if output_format in ("json", "json_schema") and content
            else None
        )

        usage = response.usage
        if self._rate_limiter is not None and usage is not None:
            self._rate_limiter.record_actual(estimated, usage.total_tokens)
        return LLMResponse(
            content=content,
            model=response.model,
            usage={
                "prompt_tokens": usage.prompt_tokens if usage else 0,
                "completion_tokens": usage.completion_tokens if usage else 0,
                "total_tokens": usage.total_tokens if usage else 0,
            },
            finish_reason=response.choices[0].finish_reason,
            parsed=parsed,
        )

    def _try_recover_from_chat_error(
        self, exc: Exception, request_params: dict[str, Any]
    ) -> LLMResponse | None:
        """Translate known provider quirks into a salvaged response or a clearer raise.

        Returns a salvaged ``LLMResponse`` only for Groq's 400 json_validate_failed
        when the body carries a recoverable ``failed_generation``. Returns ``None``
        when the caller should re-raise the original exception.
        """
        _raise_if_request_too_large(exc, self._provider_name)
        status = getattr(exc, "status_code", None)
        if status == 404:
            model_name = request_params.get("model", "unknown")
            raise ValueError(
                f"Model '{model_name}' not found on {self._provider_name}. "
                f"Update campaign_config['optimizer_llm']['model'] or "
                f"set EXPERIMENT_ID = None to use current config."
            ) from exc
        if status == 400 and "json_validate_failed" in str(exc):
            repaired = _repair_json_validate_failure(str(exc))
            if repaired is not None:
                fg_text, parsed = repaired
                logger.info(
                    "%s: salvaged failed_generation via JSON repair",
                    self._provider_name,
                )
                return LLMResponse(
                    content=fg_text,
                    model=request_params.get("model", ""),
                    usage={"prompt_tokens": 0, "completion_tokens": 0},
                    parsed=parsed,
                )
        return None


class AnthropicClient(LLMClientBase):
    """Anthropic API client."""

    def __init__(
        self,
        api_key: str | None = None,
        rate_limiter: RateLimiter | None = None,
    ):
        self._api_key = api_key or settings.ANTHROPIC_API_KEY
        self._rate_limiter = rate_limiter
        self._client = None

    def _ensure_client(self):
        """Lazy-initialize the async Anthropic client."""
        if self._client is None:
            try:
                from anthropic import AsyncAnthropic
            except ImportError as err:
                raise ImportError(
                    "anthropic package not installed. "
                    'Install the anthropic extras: pip install -e ".[anthropic]"'
                ) from err
            self._client = AsyncAnthropic(api_key=self._api_key)
        return self._client

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        output_format: Literal["text", "json", "json_schema"] = "text",
        json_schema: dict | None = None,
        **kwargs,
    ) -> LLMResponse:
        if output_format == "json_schema":
            raise NotImplementedError(
                "Anthropic client does not support response_format=json_schema. "
                "Use a JSON-schema-capable provider (Groq/OpenAI) for structured output."
            )
        client = self._ensure_client()

        # Separate system message (Anthropic API convention)
        system_message = None
        anthropic_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system_message = msg["content"]
            else:
                anthropic_messages.append({"role": msg["role"], "content": msg["content"]})

        model_name = model or settings.LLM_MODEL

        # Anthropic's API requires max_tokens. When the caller passes None
        # (project-wide default = uncapped), we still have to send a value.
        # 8192 is the per-request ceiling on most Claude models — enough
        # headroom for any realistic optimizer output; boundary-local only,
        # not a project default.
        anthropic_max_tokens = max_tokens if max_tokens is not None else 8192

        request_params: dict[str, Any] = {
            "model": model_name,
            "messages": anthropic_messages,
            "max_tokens": anthropic_max_tokens,
            "temperature": temperature,
        }
        if system_message:
            request_params["system"] = system_message

        estimated = estimate_tokens(messages, max_tokens)
        if self._rate_limiter is not None:
            if self._rate_limiter.tpm is not None and estimated > self._rate_limiter.tpm:
                raise RequestTooLargeError(
                    provider_name="Anthropic",
                    limit=self._rate_limiter.tpm,
                    requested=estimated,
                )
            await self._rate_limiter.acquire(estimated)

        raw = await client.messages.with_raw_response.create(**request_params)
        response = raw.parse()

        total = response.usage.input_tokens + response.usage.output_tokens
        if self._rate_limiter is not None:
            self._rate_limiter.record_actual(estimated, total)
            rpm = _parse_int_header(raw.headers, "anthropic-ratelimit-requests-limit")
            tpm = _parse_int_header(raw.headers, "anthropic-ratelimit-tokens-limit")
            self._rate_limiter.apply_discovered(rpm, tpm)

        content = "".join(block.text for block in response.content if hasattr(block, "text"))
        parsed = (
            try_parse_json(content, "Anthropic") if output_format == "json" and content else None
        )

        return LLMResponse(
            content=content,
            model=response.model,
            usage={
                "prompt_tokens": response.usage.input_tokens,
                "completion_tokens": response.usage.output_tokens,
                "total_tokens": total,
            },
            finish_reason=response.stop_reason,
            parsed=parsed,
        )


_llm_client: LLMClientBase | None = None

_PLACEHOLDER_KEYS = {
    "your_openai_api_key_here",
    "your_anthropic_api_key_here",
    "your_groq_api_key_here",
}


def _build_rate_limiter(rpm: int | None, tpm: int | None) -> RateLimiter:
    """Build a limiter. Configured caps pin; unconfigured slots self-tune from
    ``x-ratelimit-limit-*`` response headers on the first successful call."""
    return RateLimiter(
        rpm=rpm,
        tpm=tpm,
        rpm_pinned=rpm is not None,
        tpm_pinned=tpm is not None,
    )


def _make_groq_client() -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        api_key=settings.GROQ_API_KEY,
        base_url=GROQ_BASE_URL,
        max_retries=GROQ_MAX_RETRIES,
        timeout=GROQ_TIMEOUT,
        provider_name="Groq",
        rate_limiter=_build_rate_limiter(settings.GROQ_RPM, settings.GROQ_TPM),
    )


def _make_openai_client() -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        api_key=settings.OPENAI_API_KEY,
        max_retries=OPENAI_MAX_RETRIES,
        provider_name="OpenAI",
        rate_limiter=_build_rate_limiter(settings.OPENAI_RPM, settings.OPENAI_TPM),
    )


def _make_anthropic_client() -> "AnthropicClient":
    return AnthropicClient(
        rate_limiter=_build_rate_limiter(settings.ANTHROPIC_RPM, settings.ANTHROPIC_TPM),
    )


def _resolve_provider() -> str:
    """Auto-detect LLM provider from settings + environment.

    Priority: configured provider (if key exists) → first available key.
    """
    configured = getattr(settings, "LLM_PROVIDER", "").lower()
    _candidates = [
        ("groq", settings.GROQ_API_KEY),
        ("anthropic", settings.ANTHROPIC_API_KEY),
        ("openai", settings.OPENAI_API_KEY),
    ]

    def _has_key(key: str | None) -> bool:
        return bool(key) and key not in _PLACEHOLDER_KEYS

    # Configured provider takes priority
    for name, key in _candidates:
        if configured == name and _has_key(key):
            return name
    # Fallback: first available key
    for name, key in _candidates:
        if _has_key(key):
            return name
    return "mock"


def _make_mock_client() -> LLMClientBase:
    """Lazy-load MockLLMClient (lives in tests package)."""
    try:
        from tests.mock_llm_client import MockLLMClient  # type: ignore[import-not-found]
    except ImportError as err:
        raise ValueError(
            "No LLM API keys configured and test mock unavailable. "
            "Set GROQ_API_KEY, OPENAI_API_KEY, or ANTHROPIC_API_KEY."
        ) from err
    return MockLLMClient()


_PROVIDER_FACTORIES: dict[str, Callable[[], LLMClientBase]] = {
    "groq": _make_groq_client,
    "anthropic": _make_anthropic_client,
    "openai": _make_openai_client,
    "mock": _make_mock_client,
}


def get_llm_client(provider: str | None = None) -> LLMClientBase:
    """Get the configured LLM client (auto-detects provider if not specified)."""
    global _llm_client

    if provider:
        factory = _PROVIDER_FACTORIES.get(provider)
        if factory is None:
            raise ValueError(f"Unknown provider: {provider}")
        return factory()

    if _llm_client is None:
        _llm_client = _PROVIDER_FACTORIES[_resolve_provider()]()

    return _llm_client
