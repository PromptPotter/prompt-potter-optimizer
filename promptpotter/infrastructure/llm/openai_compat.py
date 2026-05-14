"""OpenAI-compatible client (OpenAI, Groq, OpenRouter)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from promptpotter.config.settings import settings
from promptpotter.infrastructure.llm.base import LLMClientBase
from promptpotter.infrastructure.llm.json_parse import (
    parse_response_content,
    try_groq_json_validate_repair,
)
from promptpotter.infrastructure.llm.models import LLMResponse
from promptpotter.infrastructure.llm.rate_limit import (
    OPENAI_RPM_HEADER,
    OPENAI_TPM_HEADER,
    RateLimiter,
    acquire_reservation,
    apply_discovered_caps,
    raise_if_request_too_large,
)

if TYPE_CHECKING:
    from openai import AsyncOpenAI


class OpenAICompatibleClient(LLMClientBase):
    """Client for any OpenAI-compatible API (OpenAI, Groq, etc.)."""

    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        max_retries: int = 5,
        timeout: float | None = None,
        default_model: str | None = None,
        provider_name: str = "openai",
        rate_limiter: RateLimiter | None = None,
    ):
        self._api_key = api_key
        self._base_url = base_url
        self._max_retries = max_retries
        self._timeout = timeout
        self._default_model = default_model or settings.LLM_MODEL
        self._provider_name = provider_name
        self._rate_limiter = rate_limiter
        self._client: AsyncOpenAI | None = None

    def _ensure_client(self) -> AsyncOpenAI:
        """Lazy-initialize the async OpenAI client."""
        if self._client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError as err:
                raise ImportError("openai package not installed. Run: pip install openai") from err

            kwargs: dict[str, Any] = {
                "api_key": self._api_key,
                "max_retries": self._max_retries,
            }
            if self._base_url:
                kwargs["base_url"] = self._base_url
            if self._timeout:
                kwargs["timeout"] = self._timeout
            self._client = AsyncOpenAI(**kwargs)
        return self._client

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        response_model: type[BaseModel] | None = None,
        response_schema: dict | None = None,
        **kwargs,
    ) -> LLMResponse:
        client = self._ensure_client()

        request_params: dict[str, Any] = {
            "model": model or self._default_model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            request_params["max_tokens"] = max_tokens

        wire_schema = response_schema or (
            response_model.model_json_schema() if response_model else None
        )
        if wire_schema is not None:
            request_params["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": (response_model.__name__ if response_model else "response_schema"),
                    "schema": wire_schema,
                    "strict": False,
                },
            }

        request_params.update(kwargs)

        # Proactive tier check + throttle. If the configured TPM cap can never
        # fit this request, fail fast before any network call. Otherwise block
        # until sending it stays within the rolling-window budget.
        reservation = await acquire_reservation(
            self._rate_limiter, messages, max_tokens, self._provider_name
        )

        # SDK's own ``max_retries`` handles 408/409/429/5xx + Retry-After.
        # We only intercept for: terminal request-too-large (413/429 with
        # Requested > Limit), 404 model-not-found, and Groq's 400
        # json_validate_failed quirk. Use ``with_raw_response`` so we can
        # self-tune the limiter from ``x-ratelimit-limit-*`` headers.
        try:
            raw = await client.chat.completions.with_raw_response.create(**request_params)
            response = raw.parse()
        except Exception as exc:
            recovered = self._try_recover_from_chat_error(exc, request_params, response_model)
            if recovered is not None:
                return recovered
            raise

        apply_discovered_caps(
            self._rate_limiter,
            raw.headers,
            rpm_header=OPENAI_RPM_HEADER,
            tpm_header=OPENAI_TPM_HEADER,
        )

        if not response.choices:
            raise ValueError(f"{self._provider_name} returned empty choices")
        content = response.choices[0].message.content or ""
        parsed = parse_response_content(
            content, response_model, response_schema, self._provider_name
        )

        usage = response.usage
        if reservation is not None and usage is not None:
            reservation.close(usage.total_tokens)
        return LLMResponse(
            content=content,
            model=response.model,
            usage={
                "prompt_tokens": usage.prompt_tokens if usage else 0,
                "completion_tokens": usage.completion_tokens if usage else 0,
                "total_tokens": usage.total_tokens if usage else 0,
            },
            finish_reason=response.choices[0].finish_reason,
            parsed=parsed,
        )

    def _try_recover_from_chat_error(
        self,
        exc: Exception,
        request_params: dict[str, Any],
        response_model: type[BaseModel] | None,
    ) -> LLMResponse | None:
        """Translate known errors: too-large raises, 404 raises a clearer
        ValueError, Groq json_validate_failed salvages. Returns ``None`` to
        re-raise the original exception."""
        raise_if_request_too_large(exc, self._provider_name)
        if getattr(exc, "status_code", None) == 404:
            model_name = request_params.get("model", "unknown")
            raise ValueError(
                f"Model '{model_name}' not found on {self._provider_name}. "
                f"Update campaign_config['optimizer_llm']['model'] or "
                f"set EXPERIMENT_ID = None to use current config."
            ) from exc
        return try_groq_json_validate_repair(
            exc, request_params, self._provider_name, response_model
        )
