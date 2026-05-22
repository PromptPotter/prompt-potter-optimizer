"""Abstract base class for LLM clients."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel

from promptpotter.infrastructure.llm.models import LLMResponse


class LLMClientBase(ABC):
    """Abstract base class for LLM clients."""

    @abstractmethod
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
        """Send a chat completion request.

        Args:
            messages: List of message dicts with 'role' and 'content'.
            model: Model identifier (uses default if not specified).
            temperature: Sampling temperature (0.0 = deterministic).
            max_tokens: Maximum response tokens. ``None`` = no cap (provider
                default — typically the model's output ceiling).
            response_model: Pydantic model class for typed output. When set,
                the provider receives the model's JSON Schema as
                ``response_format``, and ``LLMResponse.parsed`` is populated
                with a typed instance (validated via ``model_validate``
                against the parsed content). Anthropic does not support
                ``response_format=json_schema`` server-side — the model is
                still used for client-side validation of the parsed JSON.
            response_schema: JSON Schema dict OVERRIDE that takes precedence
                over ``response_model.model_json_schema()`` for the wire
                payload. Used by ``l1_generate`` whose schema is built
                dynamically per backend ``PipelineSchema`` (see
                ``l1_validators.build_l1_output_schema``). Without an
                override, the schema is derived from ``response_model``.
                Independent of ``response_model``: pass only the schema for
                untyped JSON-mode output (parsed as dict).
            **kwargs: Additional provider-specific parameters.

        Returns:
            LLMResponse with content + parsed (typed instance, dict, or None).
        """
        ...


__all__ = ["LLMClientBase"]
