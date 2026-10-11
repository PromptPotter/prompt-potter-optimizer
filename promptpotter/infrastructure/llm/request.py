from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any

from pydantic import BaseModel


@dataclass(frozen=True)
class ChatRequest:
    """``None`` = not asked for; a client refuses an asked field it cannot send (``LLMClientBase.SENDS``)."""

    messages: list[dict[str, str]]
    model: str
    temperature: float = 0.0
    max_tokens: int | None = None
    response_model: type[BaseModel] | None = None
    response_schema: dict[str, Any] | None = None
    reasoning_effort: str | None = None
    top_p: float | None = None
    seed: int | None = None
    route_order: list[str] | None = None

    def asked(self) -> frozenset[str]:
        return frozenset(f.name for f in fields(self) if getattr(self, f.name) is not None)


__all__ = ["ChatRequest"]
