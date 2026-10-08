from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from promptpotter.config.settings import settings
from promptpotter.domain.spend import TokenAccount
from promptpotter.infrastructure.llm.base import LLMClientBase
from promptpotter.infrastructure.llm.json_parse import parse_response_content
from promptpotter.infrastructure.llm.rate_limit import (
    ANTHROPIC_RPM_HEADER,
    ANTHROPIC_TPM_HEADER,
    RateLimiter,
)
from promptpotter.infrastructure.llm.request import ChatRequest
from promptpotter.infrastructure.llm.response import LLMResponse
from promptpotter.infrastructure.llm.spend_book import Billed, CallLabel

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic
    from anthropic.types import Message

logger = logging.getLogger(__name__)

# Anthropic's `stop_reason` in the OpenAI spelling `LLMResponse.finish_reason` declares; a reason
# with no counterpart rides through under its own name.
_FINISH_REASONS: dict[str, str] = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "tool_use": "tool_calls",
    "refusal": "content_filter",
}


def _usage(response: Message) -> TokenAccount:
    """Anthropic reports its cache counts BESIDE its input count; the OpenAI-compat wire reports
    its own INSIDE it. Normalized to the latter — the convention `TokenAccount` declares — so no
    reader downstream has to know which provider answered."""
    cache_read = int(getattr(response.usage, "cache_read_input_tokens", 0) or 0)
    cache_write = int(getattr(response.usage, "cache_creation_input_tokens", 0) or 0)
    return TokenAccount(
        input=response.usage.input_tokens + cache_read + cache_write,
        output=response.usage.output_tokens,
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
        self._api_key = api_key or settings.ANTHROPIC_API_KEY
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
            # No SDK retries — see `OpenAICompatibleClient._ensure_client`.
            self._client = AsyncAnthropic(api_key=self._api_key, max_retries=0)
        return self._client

    async def _chat(self, request: ChatRequest, label: CallLabel) -> LLMResponse:
        messages, model, max_tokens = request.messages, request.model, request.max_tokens
        response_model, response_schema = request.response_model, request.response_schema
        # Anthropic has no wire ``response_format``: JSON is contractual via the prompt;
        # ``response_model``/``response_schema`` parse + validate client-side, never sent.
        # So the schema's two free levers — field ORDER and per-field ``description``
        # (`docs/concepts/structured-output.md`) — reach no model here. A campaign that
        # optimizes them on this provider measures noise. Warned once per process rather
        # than raised: parsing still works, only the second prompt is missing.
        if (response_schema or response_model) and not AnthropicClient._schema_warned:
            AnthropicClient._schema_warned = True
            logger.warning(
                "AnthropicClient: response schema is parsed client-side and never sent — "
                "field order and `description` strings reach no model on this provider. "
                "Schema-axis optimization against Anthropic measures nothing."
            )
        client = self._ensure_client()

        # Anthropic convention: system message lifts out of the messages array.
        system_message = None
        anthropic_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system_message = msg["content"]
            else:
                anthropic_messages.append({"role": msg["role"], "content": msg["content"]})

        # Anthropic requires max_tokens; 8192 is the per-request ceiling on most Claude models (boundary-local fallback, not a project default).
        anthropic_max_tokens = max_tokens if max_tokens is not None else 8192

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

        async def send() -> tuple[object, tuple[Message, TokenAccount]]:
            raw = await client.messages.with_raw_response.create(**request_params)
            reply = raw.parse()
            return raw.headers, (reply, _usage(reply))

        # Held and throttled against the number we are ABOUT TO SEND, not the caller's raw one:
        # with `max_tokens=None` the request still asks for 8192.
        response, usage = await self._admitted_send(
            label,
            model=model,
            messages=messages,
            sent={"system": system_message, "messages": anthropic_messages},
            max_tokens=anthropic_max_tokens,
            send=send,
            billed=lambda reply: Billed(reply[1], None, None, reply[0].model),
        )

        content = "".join(block.text for block in response.content if hasattr(block, "text"))
        parsed = parse_response_content(content, response_model, response_schema, "Anthropic")

        return LLMResponse(
            content=content,
            model=response.model,
            usage=usage,
            finish_reason=_FINISH_REASONS.get(response.stop_reason or "", response.stop_reason),
            parsed=parsed,
        )


__all__ = ["AnthropicClient"]
