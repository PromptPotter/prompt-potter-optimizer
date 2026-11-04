from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic import AliasPath, BaseModel, Field

from promptpotter.domain.spend import TokenAccount
from promptpotter.infrastructure.llm.base import (
    WIRE_TIMEOUT_S,
    LLMClientBase,
    ProviderKey,
    SendPacing,
    post_json,
    wire_client,
)
from promptpotter.infrastructure.llm.json_parse import parse_response_content
from promptpotter.infrastructure.llm.request import ChatMessage, ChatRequest, ToolCall
from promptpotter.infrastructure.llm.response import LLMResponse
from promptpotter.infrastructure.llm.send_pacing import (
    ANTHROPIC_RPM_HEADER,
    ANTHROPIC_TPM_HEADER,
    raise_if_context_overflow,
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
    type: str
    text: str | None = None
    id: str | None = None
    name: str | None = None
    input: dict[str, Any] | None = None

    def tool_call(self) -> ToolCall | None:
        if self.type != "tool_use" or self.id is None or self.name is None:
            return None
        return ToolCall(id=self.id, name=self.name, arguments=self.input or {})


def _wire_turns(messages: list[ChatMessage]) -> tuple[str | None, list[dict[str, Any]]]:
    """The system prompt rides beside the turns; a tool's answer is a USER turn's block, and
    consecutive answers share one turn, since the Messages API alternates the two roles."""
    system: str | None = None
    turns: list[dict[str, Any]] = []
    for message in messages:
        role, content = message["role"], message["content"]
        if role == "system":
            system = content
        elif role == "tool":
            answer = {
                "type": "tool_result",
                "tool_use_id": message.get("tool_call_id"),
                "content": content,
            }
            last = turns[-1] if turns else None
            if last is not None and last["role"] == "user" and isinstance(last["content"], list):
                last["content"].append(answer)
            else:
                turns.append({"role": "user", "content": [answer]})
        elif calls := message.get("tool_calls"):
            blocks: list[dict[str, Any]] = [{"type": "text", "text": content}] if content else []
            blocks += [
                {"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["arguments"]}
                for c in calls
            ]
            turns.append({"role": role, "content": blocks})
        else:
            turns.append({"role": role, "content": content})
    return system, turns


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
            "tools",
        }
    )
    CREDIT_REFUSAL_STATUSES = frozenset({400})
    RATE_CAP_HEADERS = (ANTHROPIC_RPM_HEADER, ANTHROPIC_TPM_HEADER)
    _schema_warned = False

    def __init__(self, key: ProviderKey, pacing: SendPacing):
        super().__init__(provider="anthropic", display_name="Anthropic", key=key, pacing=pacing)

    async def _post(self, body: dict[str, Any]) -> httpx.Response:
        return await post_json(
            wire_client("https://api.anthropic.com", timeout_s=WIRE_TIMEOUT_S),
            "v1/messages",
            body,
            headers={
                "x-api-key": self._key.secret.get_secret_value(),
                "anthropic-version": "2023-06-01",
            },
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

        system_message, anthropic_messages = _wire_turns(messages)
        anthropic_max_tokens = max_tokens if max_tokens is not None else _UNSET_MAX_TOKENS

        request_params: dict[str, Any] = {
            "model": model,
            "messages": anthropic_messages,
            "max_tokens": anthropic_max_tokens,
        }
        if request.temperature is not None:
            request_params["temperature"] = request.temperature
        if system_message:
            request_params["system"] = system_message
        if request.top_p is not None:
            request_params["top_p"] = request.top_p
        if request.tools:
            request_params["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in request.tools
            ]

        async def send() -> tuple[object, _Message]:
            import httpx

            try:
                reply = await self._post(request_params)
            except httpx.HTTPStatusError as refused:
                raise_if_context_overflow(refused, self._provider_name)
                raise
            return reply.headers, _Message.model_validate_json(reply.content)

        # Held and throttled against the number we are ABOUT TO SEND, not the caller's raw one.
        response = await self._admitted_send(
            label,
            model=model,
            messages=messages,
            sent={
                "system": system_message,
                "messages": anthropic_messages,
                **({"tools": request_params["tools"]} if request.tools else {}),
            },
            max_tokens=anthropic_max_tokens,
            send=send,
            billed=lambda reply: Billed(reply.tokens, None, None, reply.model),
        )

        content = "".join(block.text for block in response.content if block.text is not None)
        tool_calls = [c for block in response.content if (c := block.tool_call()) is not None]
        # The calls are the answer: no schema governs a turn that asks for tools.
        parsed = (
            None
            if tool_calls
            else parse_response_content(content, response_model, response_schema, "Anthropic")
        )

        return LLMResponse(
            content=content,
            model=response.model,
            usage=response.tokens,
            finish_reason=_FINISH_REASONS.get(response.stop_reason or "", response.stop_reason),
            parsed=parsed,
            tool_calls=tool_calls,
        )
