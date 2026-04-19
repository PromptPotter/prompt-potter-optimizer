"""
LLM client abstraction layer.

Provides a unified interface via ``OpenAICompatibleClient`` (Groq default,
OpenAI, Anthropic). Chat completions, JSON mode, token tracking, and
exponential backoff for transient 503/429 errors. ``get_llm_client(provider)``
returns a configured singleton.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Literal, TypeVar

if TYPE_CHECKING:
    from openai import AsyncOpenAI

from pydantic import BaseModel, Field

from promptpotter.config.settings import settings
from promptpotter.shared.llm_parsing import try_parse_json

# Provider defaults
OPENAI_MAX_RETRIES: int = 5
GROQ_MAX_RETRIES: int = 3
GROQ_TIMEOUT: float = 60.0
GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"

# App-level retry (beyond SDK's own retry)
LLM_RETRY_STATUSES: frozenset[int] = frozenset({429, 502, 503})
LLM_MAX_APP_RETRIES: int = 3
LLM_BASE_DELAY: float = 1.0  # seconds

logger = logging.getLogger(__name__)

T = TypeVar("T")

__all__ = [
    "AnthropicClient",
    "LLMClientBase",
    "LLMResponse",
    "OpenAICompatibleClient",
    "get_llm_client",
]


def _backoff_delay(attempt: int) -> float:
    """Exponential backoff delay (seconds) for 0-indexed attempt N."""
    return LLM_BASE_DELAY * (2**attempt)


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
        max_tokens: int = 1000,
        output_format: Literal["text", "json"] = "text",
        **kwargs,
    ) -> LLMResponse:
        """Send a chat completion request.

        Args:
            messages: List of message dicts with 'role' and 'content'.
            model: Model identifier (uses default if not specified).
            temperature: Sampling temperature (0.0 = deterministic).
            max_tokens: Maximum response tokens.
            output_format: "text" or "json" (enables JSON mode).
            **kwargs: Additional provider-specific parameters.

        Returns:
            LLMResponse with content and usage info.
        """
        ...

    async def _retry_send(
        self,
        send: Callable[[], Awaitable[T]],
        provider_name: str,
    ) -> T:
        """Run ``send()`` with exponential backoff on transient (5xx / 429 / connection) failures.

        Re-raises non-retryable exceptions (4xx other than 429, unknown errors)
        so the caller can apply provider-specific handling. Subclasses with
        special 4xx logic (e.g. JSON-validate repair) implement their own loop
        on top of ``_backoff_delay`` / ``_repair_json_validate_failure``.
        """
        last_exc: Exception | None = None
        for attempt in range(LLM_MAX_APP_RETRIES + 1):
            try:
                return await send()
            except (KeyboardInterrupt, asyncio.CancelledError):
                raise
            except Exception as exc:
                status = getattr(exc, "status_code", None)
                is_connection = "Connection" in type(exc).__name__
                if status not in LLM_RETRY_STATUSES and not is_connection:
                    raise
                if attempt >= LLM_MAX_APP_RETRIES:
                    raise
                last_exc = exc
                delay = _backoff_delay(attempt)
                logger.warning(
                    "%s request failed (attempt %d/%d, status=%s), retrying in %.1fs: %s",
                    provider_name,
                    attempt + 1,
                    LLM_MAX_APP_RETRIES + 1,
                    status,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)
        assert last_exc is not None
        raise last_exc


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
    ):
        self._api_key = api_key
        self._base_url = base_url
        self._max_retries = max_retries
        self._timeout = timeout
        self._default_model = default_model or settings.LLM_MODEL
        self._provider_name = provider_name
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
        max_tokens: int = 1000,
        output_format: Literal["text", "json"] = "text",
        **kwargs,
    ) -> LLMResponse:
        client = self._ensure_client()

        request_params: dict[str, Any] = {
            "model": model or self._default_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if output_format == "json":
            request_params["response_format"] = {"type": "json_object"}

        request_params.update(kwargs)

        # App-level retry: 5xx / 429 / connection retry via _retry_send;
        # 4xx (404 model-not-found, 400 json_validate_failed) handled here
        # because they require provider-specific transformations.
        last_exc: Exception | None = None
        for attempt in range(LLM_MAX_APP_RETRIES + 1):
            try:
                response = await client.chat.completions.create(**request_params)
                break
            except (KeyboardInterrupt, asyncio.CancelledError):
                raise
            except Exception as exc:
                status = getattr(exc, "status_code", None)
                if status == 404:
                    model_name = request_params.get("model", "unknown")
                    raise ValueError(
                        f"Model '{model_name}' not found on {self._provider_name}. "
                        f"Update campaign_config['optimizer_llm']['model'] or "
                        f"set EXPERIMENT_ID = None to use current config."
                    ) from exc

                # Groq JSON mode quirk: 400 json_validate_failed → try repair, else retry
                is_json_validate_failed = status == 400 and "json_validate_failed" in str(exc)
                if is_json_validate_failed:
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
                    # Repair miss — treat as retryable
                    last_exc = exc
                    if attempt >= LLM_MAX_APP_RETRIES:
                        raise
                    delay = _backoff_delay(attempt)
                    logger.info(
                        "%s JSON validation failed (attempt %d/%d), retrying in %.1fs",
                        self._provider_name,
                        attempt + 1,
                        LLM_MAX_APP_RETRIES + 1,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue

                is_connection = "Connection" in type(exc).__name__
                if status not in LLM_RETRY_STATUSES and not is_connection:
                    raise
                last_exc = exc
                if attempt >= LLM_MAX_APP_RETRIES:
                    raise
                delay = _backoff_delay(attempt)
                logger.warning(
                    "%s request failed (attempt %d/%d, status=%s), retrying in %.1fs: %s",
                    self._provider_name,
                    attempt + 1,
                    LLM_MAX_APP_RETRIES + 1,
                    status,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)
        else:
            raise last_exc  # type: ignore[misc]

        if not response.choices:
            raise ValueError(f"{self._provider_name} returned empty choices")
        content = response.choices[0].message.content or ""
        parsed = (
            try_parse_json(content, self._provider_name)
            if output_format == "json" and content
            else None
        )

        usage = response.usage
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


class AnthropicClient(LLMClientBase):
    """Anthropic API client."""

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key or settings.ANTHROPIC_API_KEY
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
        max_tokens: int = 1000,
        output_format: Literal["text", "json"] = "text",
        **kwargs,
    ) -> LLMResponse:
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

        request_params: dict[str, Any] = {
            "model": model_name,
            "messages": anthropic_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system_message:
            request_params["system"] = system_message

        async def _send():
            return await client.messages.create(**request_params)

        response = await self._retry_send(_send, "Anthropic")

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
                "total_tokens": (response.usage.input_tokens + response.usage.output_tokens),
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


def _make_groq_client() -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        api_key=settings.GROQ_API_KEY,
        base_url=GROQ_BASE_URL,
        max_retries=GROQ_MAX_RETRIES,
        timeout=GROQ_TIMEOUT,
        provider_name="Groq",
    )


def _make_openai_client() -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        api_key=settings.OPENAI_API_KEY,
        max_retries=OPENAI_MAX_RETRIES,
        provider_name="OpenAI",
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
    "anthropic": AnthropicClient,
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
