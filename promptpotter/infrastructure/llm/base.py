from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel

from promptpotter.infrastructure.llm.response import LLMResponse


class LLMClientBase(ABC):
    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        response_model: type[BaseModel] | None = None,
        response_schema: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """``model`` is mandatory and concrete — no model fallback lives below this seam. ``response_schema`` overrides
        ``response_model``'s wire schema; passed alone it means untyped JSON mode."""
        ...


__all__ = ["LLMClientBase"]
