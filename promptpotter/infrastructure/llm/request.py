from __future__ import annotations

import functools
from dataclasses import dataclass, fields
from typing import Any, Literal, NotRequired, TypedDict, cast

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


def strip_titles(node: object) -> object:
    """A schema is serialized into the INPUT, so Pydantic's auto `title`s are prompt tokens."""
    if isinstance(node, dict):
        return {k: strip_titles(v) for k, v in node.items() if k != "title"}
    if isinstance(node, list):
        return [strip_titles(v) for v in node]
    return node


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    # A JSON Schema object, and prompt text: the model reads every field of it.
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ChatRequest:
    """``None`` = not asked for; a client refuses an asked field it cannot send (``OpenAICompatibleClient.SENDS``)."""

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

    @functools.cached_property
    def wire_schema(self) -> dict[str, Any] | None:
        """The schema SENT, titles stripped: the client sends it and `hash_call` keys on it."""
        schema = self.response_schema or (
            self.response_model.model_json_schema() if self.response_model else None
        )
        return None if schema is None else cast("dict[str, Any]", strip_titles(schema))

    def asked(self) -> frozenset[str]:
        return frozenset(f.name for f in fields(self) if getattr(self, f.name) is not None)
