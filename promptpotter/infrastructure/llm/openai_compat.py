"""OpenAI-compatible client (OpenAI, Groq, OpenRouter)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from promptpotter.config.settings import settings
from promptpotter.infrastructure.llm.base import LLMClientBase
from promptpotter.infrastructure.llm.json_parse import (
    MetaPromptParseError,
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

logger = logging.getLogger(__name__)


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

        result = await self._one_attempt(client, request_params, response_model, response_schema)
        if isinstance(result, LLMResponse):
            # Groq json_validate_failed salvage — already parsed + typed.
            return result
        response, content, validation_err = result
        schema_repair_attempts = 0
        if validation_err is not None:
            # First attempt failed Pydantic validation; retry once with the
            # bad output + a repair hint appended to the message list. Keeps
            # the original system/user context intact. The retry is a full
            # second LLM round-trip — surface that to the operator so the
            # extra wall-clock + spend isn't silent.
            schema_name = response_model.__name__ if response_model else "<schema>"
            content_len = len(content.strip())
            cause = (
                "provider returned empty/truncated content"
                if content_len < 20
                else "response is schema-noncompliant"
            )
            logger.warning(
                "%s: %s parse failed (%d errors, %d content chars) on %s — %s. "
                "Repair retry in flight (second full call; cost + latency ~2x).",
                self._provider_name,
                schema_name,
                validation_err.error_count(),
                content_len,
                request_params.get("model", "?"),
                cause,
            )
            repair_messages = [
                *request_params["messages"],
                {"role": "assistant", "content": content},
                {
                    "role": "user",
                    "content": (
                        "Your previous response failed schema validation. Errors:\n"
                        f"{_truncate(str(validation_err), 600)}\n\n"
                        "Return ONLY a JSON object that strictly matches the "
                        "requested schema. No prose, no markdown fences, no "
                        "extra fields."
                    ),
                },
            ]
            retry_params = {**request_params, "messages": repair_messages}
            result = await self._one_attempt(client, retry_params, response_model, response_schema)
            schema_repair_attempts = 1
            if isinstance(result, LLMResponse):
                result.schema_repair_attempts = schema_repair_attempts
                return result
            response, content, validation_err = result
            if validation_err is not None:
                content_len = len(content.strip())
                cause = (
                    "provider degraded — empty/truncated response after repair retry"
                    if content_len < 20
                    else "schema-noncompliant after repair retry"
                )
                logger.error(
                    "%s: %s parse failed AGAIN after repair retry (%d errors, "
                    "%d content chars) — %s. Round will record the failure and "
                    "continue with zero candidates.",
                    self._provider_name,
                    schema_name,
                    validation_err.error_count(),
                    content_len,
                    cause,
                )
                raise MetaPromptParseError(
                    raw=content, error=validation_err, attempts=2
                ) from validation_err

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
            schema_repair_attempts=schema_repair_attempts,
        )

    async def _one_attempt(
        self,
        client: AsyncOpenAI,
        request_params: dict[str, Any],
        response_model: type[BaseModel] | None,
        response_schema: dict | None,
    ) -> LLMResponse | tuple[Any, str, ValidationError | None]:
        """One round-trip to the provider plus a parse pre-flight.

        Returns either:
          * an :class:`LLMResponse` when the Groq ``json_validate_failed``
            salvage path fired (the salvaged content was already Pydantic-
            validated inside :func:`try_groq_json_validate_repair`); the
            caller returns it verbatim.
          * a ``(response, content, validation_err)`` tuple otherwise. When
            ``validation_err`` is ``None`` the content passed validation and
            the caller continues to the final ``LLMResponse`` assembly;
            otherwise the caller decides retry vs raise.

        SDK ``max_retries`` handles 408/409/429/5xx + Retry-After. We only
        intercept terminal request-too-large, 404 model-not-found, and Groq's
        400 ``json_validate_failed`` quirk. ``with_raw_response`` exposes
        ``x-ratelimit-limit-*`` headers so the limiter can self-tune.
        """
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

        if response_model is None:
            return response, content, None
        try:
            parse_response_content(content, response_model, response_schema, self._provider_name)
        except ValidationError as err:
            return response, content, err
        return response, content, None

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


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


__all__ = ["OpenAICompatibleClient"]
