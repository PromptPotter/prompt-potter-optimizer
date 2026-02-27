"""
LLM client abstraction layer.

Provides a unified interface for Groq (default), OpenAI, and Anthropic APIs,
with support for chat completions, JSON mode, and token tracking.
"""
import asyncio
import json
import logging
from abc import ABC, abstractmethod
from typing import Any, Literal

from pydantic import BaseModel, Field

from api.config.settings import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Provider-specific constants
# ---------------------------------------------------------------------------

OPENAI_MAX_RETRIES = 5
GROQ_MAX_RETRIES = 3
GROQ_TIMEOUT = 60.0
GROQ_BASE_URL = "https://api.groq.com/openai/v1"

# App-level retry for transient errors (beyond SDK's own retry)
_RETRY_STATUSES = {429, 502, 503}
_MAX_APP_RETRIES = 3
_BASE_DELAY = 1.0  # seconds


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Base client
# ---------------------------------------------------------------------------


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


def _try_parse_json(content: str, provider: str) -> Any | None:
    """Parse JSON from response content, return None on failure."""
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        logger.debug("%s response not valid JSON: %s", provider, content[:200])
        return None


# ---------------------------------------------------------------------------
# OpenAI-compatible client (shared by OpenAI and Groq)
# ---------------------------------------------------------------------------


class _OpenAICompatibleClient(LLMClientBase):
    """Base for clients that use the OpenAI SDK (OpenAI, Groq, etc.)."""

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
        self._client = None

    def _ensure_client(self):
        """Lazy-initialize the async OpenAI client."""
        if self._client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError:
                raise ImportError("openai package not installed. Run: pip install openai")

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

        # App-level retry for transient server errors
        last_exc: Exception | None = None
        for attempt in range(_MAX_APP_RETRIES + 1):
            try:
                response = await client.chat.completions.create(**request_params)
                break
            except Exception as exc:
                status = getattr(exc, "status_code", None)
                is_connection = "Connection" in type(exc).__name__
                if status in _RETRY_STATUSES or is_connection:
                    last_exc = exc
                    if attempt < _MAX_APP_RETRIES:
                        delay = _BASE_DELAY * (2 ** attempt)
                        logger.warning(
                            "%s request failed (attempt %d/%d, status=%s), "
                            "retrying in %.1fs: %s",
                            self._provider_name, attempt + 1,
                            _MAX_APP_RETRIES + 1, status, delay, exc,
                        )
                        await asyncio.sleep(delay)
                        continue
                raise
        else:
            raise last_exc  # type: ignore[misc]

        content = response.choices[0].message.content or ""
        parsed = (
            _try_parse_json(content, self._provider_name)
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


class OpenAIClient(_OpenAICompatibleClient):
    """OpenAI API client."""

    def __init__(self, api_key: str | None = None):
        super().__init__(
            api_key=api_key or settings.OPENAI_API_KEY,
            max_retries=OPENAI_MAX_RETRIES,
            provider_name="OpenAI",
        )


class GroqClient(_OpenAICompatibleClient):
    """Groq API client (OpenAI-compatible)."""

    def __init__(self, api_key: str | None = None):
        super().__init__(
            api_key=api_key or settings.GROQ_API_KEY,
            base_url=GROQ_BASE_URL,
            max_retries=GROQ_MAX_RETRIES,
            timeout=GROQ_TIMEOUT,
            provider_name="Groq",
        )


# ---------------------------------------------------------------------------
# Anthropic client
# ---------------------------------------------------------------------------


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
            except ImportError:
                raise ImportError(
                    "anthropic package not installed. Run: pip install anthropic"
                )
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
                anthropic_messages.append(
                    {"role": msg["role"], "content": msg["content"]}
                )

        model_name = model or settings.LLM_MODEL

        request_params: dict[str, Any] = {
            "model": model_name,
            "messages": anthropic_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system_message:
            request_params["system"] = system_message

        response = await client.messages.create(**request_params)

        content = "".join(
            block.text for block in response.content if hasattr(block, "text")
        )
        parsed = (
            _try_parse_json(content, "Anthropic")
            if output_format == "json" and content
            else None
        )

        return LLMResponse(
            content=content,
            model=response.model,
            usage={
                "prompt_tokens": response.usage.input_tokens,
                "completion_tokens": response.usage.output_tokens,
                "total_tokens": (
                    response.usage.input_tokens + response.usage.output_tokens
                ),
            },
            finish_reason=response.stop_reason,
            parsed=parsed,
        )


# ---------------------------------------------------------------------------
# Mock client (testing)
# ---------------------------------------------------------------------------


class MockLLMClient(LLMClientBase):
    """Mock LLM client for testing — returns configurable responses."""

    def __init__(self, responses: list[str] | None = None):
        self.responses = responses or ["Mock LLM response"]
        self._call_count = 0

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1000,
        output_format: Literal["text", "json"] = "text",
        **kwargs,
    ) -> LLMResponse:
        content = self.responses[self._call_count % len(self.responses)]
        self._call_count += 1

        parsed = (
            _try_parse_json(content, "Mock")
            if output_format == "json"
            else None
        )

        return LLMResponse(
            content=content,
            model=model or "mock-model",
            usage={
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
            },
            finish_reason="stop",
            parsed=parsed,
        )


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_llm_client: LLMClientBase | None = None

_PLACEHOLDER_KEYS = {
    "your_openai_api_key_here",
    "your_anthropic_api_key_here",
    "your_groq_api_key_here",
}


def get_llm_client(provider: str | None = None) -> LLMClientBase:
    """Get the configured LLM client.

    Args:
        provider: "openai", "anthropic", "groq", or "mock".
            Auto-detects from settings if not specified.

    Returns:
        LLM client instance.
    """
    global _llm_client

    if provider:
        _providers = {
            "openai": OpenAIClient,
            "anthropic": AnthropicClient,
            "groq": GroqClient,
            "mock": MockLLMClient,
        }
        factory = _providers.get(provider)
        if factory is None:
            raise ValueError(f"Unknown provider: {provider}")
        return factory()

    if _llm_client is None:
        configured = getattr(settings, "LLM_PROVIDER", "").lower()

        if (
            configured == "groq"
            and settings.GROQ_API_KEY
            and settings.GROQ_API_KEY not in _PLACEHOLDER_KEYS
        ):
            _llm_client = GroqClient()
        elif configured == "anthropic" and settings.ANTHROPIC_API_KEY:
            _llm_client = AnthropicClient()
        elif configured == "openai" and settings.OPENAI_API_KEY:
            _llm_client = OpenAIClient()
        elif (
            settings.GROQ_API_KEY
            and settings.GROQ_API_KEY not in _PLACEHOLDER_KEYS
        ):
            _llm_client = GroqClient()
        elif (
            settings.OPENAI_API_KEY
            and settings.OPENAI_API_KEY not in _PLACEHOLDER_KEYS
        ):
            _llm_client = OpenAIClient()
        elif (
            settings.ANTHROPIC_API_KEY
            and settings.ANTHROPIC_API_KEY not in _PLACEHOLDER_KEYS
        ):
            _llm_client = AnthropicClient()
        else:
            _llm_client = MockLLMClient()

    return _llm_client


def set_llm_client(client: LLMClientBase) -> None:
    """Set a custom LLM client (useful for testing)."""
    global _llm_client
    _llm_client = client
