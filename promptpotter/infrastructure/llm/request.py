from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any

from pydantic import BaseModel


@dataclass(frozen=True)
class ChatRequest:
    """One chat request, as every provider client takes it. ``model`` is concrete: no model
    fallback lives below this seam. ``response_schema`` overrides ``response_model``'s wire schema;
    passed alone it means untyped JSON mode. A field left ``None`` is one the caller did not ask
    for, and a client refuses a field it was asked for and cannot send (``LLMClientBase.SENDS``)."""

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
