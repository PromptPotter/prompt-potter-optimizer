"""Abstract base class for LLM clients."""

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
        """Send a chat completion request.

        - ``model``: mandatory + concrete. ``llm_call`` reads it from the
          optimizer node config (``promptpotter/assets/optimizer/pipeline.yaml``) before
          the call; no model fallback lives below this seam.
        - ``max_tokens``: ``None`` = no cap (provider default).
        - ``response_model``: typed JSON output via provider ``response_format`` +
          client-side ``model_validate``. Anthropic validates client-side only
          (no wire ``response_format`` support).
        - ``response_schema``: dict OVERRIDE for the wire schema, taking
          precedence over ``response_model.model_json_schema()``. Used by
          ``l1_generate`` whose schema is built dynamically per
          ``PipelineSchema``. Pass alone for untyped JSON mode (parsed → dict).
        """
        ...


__all__ = ["LLMClientBase"]
