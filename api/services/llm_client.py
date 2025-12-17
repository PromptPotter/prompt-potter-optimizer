"""
LLM client abstraction layer.

Provides a unified interface for OpenAI and Anthropic APIs,
with support for chat completions, JSON mode, and token tracking.
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, Literal
from pydantic import BaseModel, Field
import json

from api.config.settings import settings


class LLMResponse(BaseModel):
    """Standardized response from LLM providers."""

    content: str = Field(..., description="Response content")
    model: str = Field(..., description="Model used")
    usage: Dict[str, int] = Field(
        default_factory=dict,
        description="Token usage: prompt_tokens, completion_tokens, total_tokens"
    )
    finish_reason: Optional[str] = Field(None, description="Why generation stopped")
    parsed: Optional[Any] = Field(None, description="Parsed JSON if output_format='json'")


class LLMClientBase(ABC):
    """Abstract base class for LLM clients."""

    @abstractmethod
    async def chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 1000,
        output_format: Literal["text", "json"] = "text",
        **kwargs
    ) -> LLMResponse:
        """
        Send a chat completion request.

        Args:
            messages: List of message dicts with 'role' and 'content'
            model: Model identifier (uses default if not specified)
            temperature: Sampling temperature (0.0 = deterministic)
            max_tokens: Maximum response tokens
            output_format: "text" or "json" (enables JSON mode)
            **kwargs: Additional provider-specific parameters

        Returns:
            LLMResponse with content and usage info
        """
        pass


class OpenAIClient(LLMClientBase):
    """OpenAI API client."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.OPENAI_API_KEY
        self._client = None

    def _get_client(self):
        """Lazy-load the OpenAI client."""
        if self._client is None:
            try:
                from openai import AsyncOpenAI
                self._client = AsyncOpenAI(api_key=self.api_key)
            except ImportError:
                raise ImportError("openai package not installed. Run: pip install openai")
        return self._client

    async def chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 1000,
        output_format: Literal["text", "json"] = "text",
        **kwargs
    ) -> LLMResponse:
        client = self._get_client()

        # Prepare request
        request_params = {
            "model": model or settings.DEFAULT_MODEL,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        # Enable JSON mode if requested
        if output_format == "json":
            request_params["response_format"] = {"type": "json_object"}

        # Add any extra params
        request_params.update(kwargs)

        # Make request
        response = await client.chat.completions.create(**request_params)

        # Extract content
        content = response.choices[0].message.content or ""

        # Parse JSON if requested
        parsed = None
        if output_format == "json" and content:
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                pass

        return LLMResponse(
            content=content,
            model=response.model,
            usage={
                "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                "total_tokens": response.usage.total_tokens if response.usage else 0,
            },
            finish_reason=response.choices[0].finish_reason,
            parsed=parsed
        )


class GroqClient(LLMClientBase):
    """Groq API client (OpenAI-compatible)."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.GROQ_API_KEY
        self._client = None

    def _get_client(self):
        """Lazy-load the Groq client (uses OpenAI SDK with Groq base URL)."""
        if self._client is None:
            try:
                from openai import AsyncOpenAI
                self._client = AsyncOpenAI(
                    api_key=self.api_key,
                    base_url="https://api.groq.com/openai/v1"
                )
            except ImportError:
                raise ImportError("openai package not installed. Run: pip install openai")
        return self._client

    async def chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 1000,
        output_format: Literal["text", "json"] = "text",
        **kwargs
    ) -> LLMResponse:
        client = self._get_client()

        # Use configured model or default Groq model
        model_name = model or settings.LLM_MODEL or "llama-3.3-70b-versatile"

        # Prepare request
        request_params = {
            "model": model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        # Enable JSON mode if requested
        if output_format == "json":
            request_params["response_format"] = {"type": "json_object"}

        # Add any extra params
        request_params.update(kwargs)

        # Make request
        response = await client.chat.completions.create(**request_params)

        # Extract content
        content = response.choices[0].message.content or ""

        # Parse JSON if requested
        parsed = None
        if output_format == "json" and content:
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                pass

        return LLMResponse(
            content=content,
            model=response.model,
            usage={
                "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                "total_tokens": response.usage.total_tokens if response.usage else 0,
            },
            finish_reason=response.choices[0].finish_reason,
            parsed=parsed
        )


class AnthropicClient(LLMClientBase):
    """Anthropic API client."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.ANTHROPIC_API_KEY
        self._client = None

    def _get_client(self):
        """Lazy-load the Anthropic client."""
        if self._client is None:
            try:
                from anthropic import AsyncAnthropic
                self._client = AsyncAnthropic(api_key=self.api_key)
            except ImportError:
                raise ImportError("anthropic package not installed. Run: pip install anthropic")
        return self._client

    async def chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 1000,
        output_format: Literal["text", "json"] = "text",
        **kwargs
    ) -> LLMResponse:
        client = self._get_client()

        # Convert messages format (extract system message)
        system_message = None
        anthropic_messages = []

        for msg in messages:
            if msg["role"] == "system":
                system_message = msg["content"]
            else:
                anthropic_messages.append({
                    "role": msg["role"],
                    "content": msg["content"]
                })

        # Use default model or map to Anthropic model
        model_name = model or "claude-3-sonnet-20240229"
        if model_name.startswith("gpt"):
            # Map OpenAI model names to Anthropic
            model_name = "claude-3-sonnet-20240229"

        # Prepare request
        request_params = {
            "model": model_name,
            "messages": anthropic_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        if system_message:
            request_params["system"] = system_message

        # Make request
        response = await client.messages.create(**request_params)

        # Extract content
        content = ""
        for block in response.content:
            if hasattr(block, "text"):
                content += block.text

        # Parse JSON if requested
        parsed = None
        if output_format == "json" and content:
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                pass

        return LLMResponse(
            content=content,
            model=response.model,
            usage={
                "prompt_tokens": response.usage.input_tokens,
                "completion_tokens": response.usage.output_tokens,
                "total_tokens": response.usage.input_tokens + response.usage.output_tokens,
            },
            finish_reason=response.stop_reason,
            parsed=parsed
        )


class MockLLMClient(LLMClientBase):
    """
    Mock LLM client for testing.

    Returns configurable responses without making API calls.
    """

    def __init__(self, responses: Optional[List[str]] = None):
        self.responses = responses or ["Mock LLM response"]
        self._call_count = 0

    async def chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 1000,
        output_format: Literal["text", "json"] = "text",
        **kwargs
    ) -> LLMResponse:
        # Cycle through responses
        content = self.responses[self._call_count % len(self.responses)]
        self._call_count += 1

        # Parse JSON if requested
        parsed = None
        if output_format == "json":
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                pass

        return LLMResponse(
            content=content,
            model=model or "mock-model",
            usage={
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
            },
            finish_reason="stop",
            parsed=parsed
        )


# Global client instance
_llm_client: Optional[LLMClientBase] = None


def get_llm_client(provider: Optional[str] = None) -> LLMClientBase:
    """
    Get the configured LLM client.

    Args:
        provider: "openai", "anthropic", "groq", or "mock". Auto-detects if not specified.

    Returns:
        LLM client instance
    """
    global _llm_client

    if provider:
        if provider == "openai":
            return OpenAIClient()
        elif provider == "anthropic":
            return AnthropicClient()
        elif provider == "groq":
            return GroqClient()
        elif provider == "mock":
            return MockLLMClient()
        else:
            raise ValueError(f"Unknown provider: {provider}")

    if _llm_client is None:
        # Check LLM_PROVIDER setting first
        configured_provider = getattr(settings, "LLM_PROVIDER", "").lower()
        if configured_provider == "groq" and settings.GROQ_API_KEY:
            _llm_client = GroqClient()
        elif configured_provider == "anthropic" and settings.ANTHROPIC_API_KEY:
            _llm_client = AnthropicClient()
        elif configured_provider == "openai" and settings.OPENAI_API_KEY:
            _llm_client = OpenAIClient()
        # Auto-detect based on available API keys
        elif settings.GROQ_API_KEY:
            _llm_client = GroqClient()
        elif settings.OPENAI_API_KEY and settings.OPENAI_API_KEY != "your_openai_api_key_here":
            _llm_client = OpenAIClient()
        elif settings.ANTHROPIC_API_KEY and settings.ANTHROPIC_API_KEY != "your_anthropic_api_key_here":
            _llm_client = AnthropicClient()
        else:
            # Fall back to mock for testing
            _llm_client = MockLLMClient()

    return _llm_client


def set_llm_client(client: LLMClientBase) -> None:
    """
    Set a custom LLM client (useful for testing).

    Args:
        client: LLM client instance to use
    """
    global _llm_client
    _llm_client = client
