from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Literal, NotRequired, TypedDict

from pydantic import BaseModel


class ToolCall(TypedDict):
    """One call a model asked for: ``arguments`` parsed, never the wire's JSON string."""

    id: str
    name: str
    arguments: dict[str, Any]


class ChatMessage(TypedDict):
    """One turn in the provider-neutral shape each client translates to its own wire."""

    role: Literal["system", "user", "assistant", "tool"]
    content: str
    # An assistant turn's calls, replayed so the tool turns after it answer them.
    tool_calls: NotRequired[list[ToolCall]]
    # A tool turn: the call it answers.
    tool_call_id: NotRequired[str]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    # A JSON Schema object, and prompt text: the model reads every field of it.
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ChatRequest:
    """``None`` = not asked for; a client refuses an asked field it cannot send (``LLMClientBase.SENDS``)."""

    messages: list[ChatMessage]
    model: str
    temperature: float | None = None
    max_tokens: int | None = None
    response_model: type[BaseModel] | None = None
    response_schema: dict[str, Any] | None = None
    reasoning_effort: str | None = None
    top_p: float | None = None
    seed: int | None = None
    route_order: list[str] | None = None
    tools: tuple[Tool, ...] | None = None

    def asked(self) -> frozenset[str]:
        return frozenset(f.name for f in fields(self) if getattr(self, f.name) is not None)
