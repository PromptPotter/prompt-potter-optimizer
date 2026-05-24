"""Anthropic API client."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from promptpotter.config.settings import settings
from promptpotter.infrastructure.llm.base import LLMClientBase
from promptpotter.infrastructure.llm.json_parse import parse_response_content
from promptpotter.infrastructure.llm.models import LLMResponse
from promptpotter.infrastructure.llm.rate_limit import (
    ANTHROPIC_RPM_HEADER,
    ANTHROPIC_TPM_HEADER,
    RateLimiter,
    acquire_reservation,
    apply_discovered_caps,
)

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic


class AnthropicClient(LLMClientBase):
    """Anthropic API client."""

    def __init__(
        self,
        api_key: str | None = None,
        rate_limiter: RateLimiter | None = None,
    ):
        self._api_key = api_key or settings.ANTHROPIC_API_KEY
        self._rate_limiter = rate_limiter
        self._client: AsyncAnthropic | None = None

    def _ensure_client(self) -> AsyncAnthropic:
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
        response_model: type[BaseModel] | None = None,
        response_schema: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        # Anthropic has no wire ``response_format``: JSON is contractual via the prompt;
        # ``response_model``/``response_schema`` parse + validate client-side, never sent.
        client = self._ensure_client()

        # Anthropic convention: system message lifts out of the messages array.
        system_message = None
        anthropic_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system_message = msg["content"]
            else:
                anthropic_messages.append({"role": msg["role"], "content": msg["content"]})

        model_name = model or settings.LLM_MODEL

        # Anthropic requires max_tokens; 8192 is the per-request ceiling on most Claude models (boundary-local fallback, not a project default).
        anthropic_max_tokens = max_tokens if max_tokens is not None else 8192

        request_params: dict[str, Any] = {
            "model": model_name,
            "messages": anthropic_messages,
            "max_tokens": anthropic_max_tokens,
            "temperature": temperature,
        }
        if system_message:
            request_params["system"] = system_message

        reservation = await acquire_reservation(
            self._rate_limiter, messages, max_tokens, "Anthropic"
        )

        raw = await client.messages.with_raw_response.create(**request_params)
        response = raw.parse()

        total = response.usage.input_tokens + response.usage.output_tokens
        if reservation is not None:
            reservation.close(total)
        apply_discovered_caps(
            self._rate_limiter,
            raw.headers,
            rpm_header=ANTHROPIC_RPM_HEADER,
            tpm_header=ANTHROPIC_TPM_HEADER,
        )

        content = "".join(block.text for block in response.content if hasattr(block, "text"))
        parsed = parse_response_content(content, response_model, response_schema, "Anthropic")

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


__all__ = ["AnthropicClient"]
