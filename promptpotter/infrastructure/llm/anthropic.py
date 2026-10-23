from __future__ import annotations

import functools
import logging
from typing import TYPE_CHECKING, Any

from pydantic import AliasPath, BaseModel, Field

from promptpotter.config.settings import settings
from promptpotter.domain.spend import TokenAccount
from promptpotter.infrastructure.llm.base import (
    WIRE_TIMEOUT_S,
    LLMClientBase,
    post_json,
    wire_client,
)
from promptpotter.infrastructure.llm.json_parse import parse_response_content
from promptpotter.infrastructure.llm.request import ChatRequest
from promptpotter.infrastructure.llm.response import LLMResponse
from promptpotter.infrastructure.llm.send_pacing import (
    ANTHROPIC_RPM_HEADER,
    ANTHROPIC_TPM_HEADER,
    RateLimiter,
)
from promptpotter.infrastructure.llm.spend_book import Billed, CallLabel

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger(__name__)

_FINISH_REASONS: dict[str, str] = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "tool_use": "tool_calls",
    "refusal": "content_filter",
}
# What a request declaring no `max_tokens` asks for: the Messages API refuses one without it.
_UNSET_MAX_TOKENS = 8192


class _Block(BaseModel):
    text: str | None = None


class _Message(BaseModel):
    model: str
    content: list[_Block]
    stop_reason: str | None = None
    input_tokens: int = Field(validation_alias=AliasPath("usage", "input_tokens"))
    output_tokens: int = Field(validation_alias=AliasPath("usage", "output_tokens"))
    cache_read: int | None = Field(
        None, validation_alias=AliasPath("usage", "cache_read_input_tokens")
    )
    cache_write: int | None = Field(
        None, validation_alias=AliasPath("usage", "cache_creation_input_tokens")
    )

    @property
    def tokens(self) -> TokenAccount:
        """Anthropic reports cache counts BESIDE its input count; `TokenAccount` holds them INSIDE."""
        cache_read, cache_write = self.cache_read or 0, self.cache_write or 0
        return TokenAccount(
            input=self.input_tokens + cache_read + cache_write,
            output=self.output_tokens,
            cache_read=cache_read,
            cache_write=cache_write,
        )


class AnthropicClient(LLMClientBase):
    # The two schema fields are honoured by a client-side parse, never sent (`_chat`).
    SENDS = frozenset(
        {
            "messages",
            "model",
            "temperature",
            "max_tokens",
            "top_p",
            "response_model",
            "response_schema",
        }
    )
    CREDIT_REFUSAL_STATUSES = frozenset({400})
    RATE_CAP_HEADERS = (ANTHROPIC_RPM_HEADER, ANTHROPIC_TPM_HEADER)
    _schema_warned = False

    def __init__(
        self,
        api_key: str | None = None,
        rate_limiter: RateLimiter | None = None,
    ):
        super().__init__(provider="anthropic", display_name="Anthropic", rate_limiter=rate_limiter)
        self._api_key = api_key or settings.ANTHROPIC_API_KEY.get_secret_value()

    @functools.cached_property
    def _http(self) -> httpx.AsyncClient:
        return wire_client(
            "https://api.anthropic.com",
            {"x-api-key": self._api_key, "anthropic-version": "2023-06-01"},
            timeout_s=WIRE_TIMEOUT_S,
        )

    async def _chat(self, request: ChatRequest, label: CallLabel) -> LLMResponse:
        messages, model, max_tokens = request.messages, request.model, request.max_tokens
        response_model, response_schema = request.response_model, request.response_schema
        if (response_schema or response_model) and not AnthropicClient._schema_warned:
            AnthropicClient._schema_warned = True
            logger.warning(
                "AnthropicClient: response schema is parsed client-side and never sent — "
                "field order and `description` strings reach no model on this provider. "
                "Schema-axis optimization against Anthropic measures nothing."
            )

        system_message = None
        anthropic_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system_message = msg["content"]
            else:
                anthropic_messages.append({"role": msg["role"], "content": msg["content"]})

        anthropic_max_tokens = max_tokens if max_tokens is not None else _UNSET_MAX_TOKENS

        request_params: dict[str, Any] = {
            "model": model,
            "messages": anthropic_messages,
            "max_tokens": anthropic_max_tokens,
            "temperature": request.temperature,
        }
        if system_message:
            request_params["system"] = system_message
        if request.top_p is not None:
            request_params["top_p"] = request.top_p

        async def send() -> tuple[object, _Message]:
            reply = await post_json(self._http, "v1/messages", request_params)
            return reply.headers, _Message.model_validate_json(reply.content)

        # Held and throttled against the number we are ABOUT TO SEND, not the caller's raw one.
        response = await self._admitted_send(
            label,
            model=model,
            messages=messages,
            sent={"system": system_message, "messages": anthropic_messages},
            max_tokens=anthropic_max_tokens,
            send=send,
            billed=lambda reply: Billed(reply.tokens, None, None, reply.model),
        )

        content = "".join(block.text for block in response.content if block.text is not None)
        parsed = parse_response_content(content, response_model, response_schema, "Anthropic")

        return LLMResponse(
            content=content,
            model=response.model,
            usage=response.tokens,
            finish_reason=_FINISH_REASONS.get(response.stop_reason or "", response.stop_reason),
            parsed=parsed,
        )
