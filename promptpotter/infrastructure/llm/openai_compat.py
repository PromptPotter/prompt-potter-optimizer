"""OpenAI-compatible client (OpenAI, Groq, OpenRouter)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

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
from promptpotter.shared import truncate

if TYPE_CHECKING:
    from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


def _failure_diagnostics(response: Any, first_prompt: int, first_completion: int) -> dict[str, Any]:
    """Provider's own account of a parse failure, for ``MetaPromptParseError``.

    ``reasoning_tokens`` is the tell: a reasoning model can spend the whole
    ``max_tokens`` budget thinking and emit zero content tokens, which is a
    prompt-size problem, not a flaky provider. Both round-trips are metered —
    the caller bills them, and a failed call is billed the same as a good one."""
    usage = getattr(response, "usage", None)
    details = getattr(usage, "completion_tokens_details", None)
    message = response.choices[0].message if getattr(response, "choices", None) else None
    return {
        "model": getattr(response, "model", None),
        "finish_reason": (
            response.choices[0].finish_reason if getattr(response, "choices", None) else None
        ),
        "prompt_tokens": (usage.prompt_tokens if usage else 0) + first_prompt,
        "completion_tokens": (usage.completion_tokens if usage else 0) + first_completion,
        "reasoning_tokens": int(getattr(details, "reasoning_tokens", 0) or 0),
        "reasoning_chars": len(getattr(message, "reasoning", None) or "" if message else ""),
    }


class OpenAICompatibleClient(LLMClientBase):
    """Client for any OpenAI-compatible API (OpenAI, Groq, etc.)."""

    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        max_retries: int = 5,
        timeout: float | None = None,
        provider_name: str = "openai",
        rate_limiter: RateLimiter | None = None,
    ):
        self._api_key = api_key
        self._base_url = base_url
        self._max_retries = max_retries
        self._timeout = timeout
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
        model: str,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        response_model: type[BaseModel] | None = None,
        response_schema: dict[str, Any] | None = None,
        reasoning_effort: str | None = None,
        seed: int | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        client = self._ensure_client()

        request_params: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            request_params["max_tokens"] = max_tokens
        # Bounded reasoning is a survival guard (the openrouter/gpt-oss optimizer nodes blow the
        # call deadline at unbounded effort); the OpenAI-compatible field is top-level. Omitted
        # when unset so a provider that doesn't accept it never sees a null.
        if reasoning_effort is not None:
            request_params["reasoning_effort"] = reasoning_effort
        # Temperature 0 pins the distribution, not the draw — without a seed the provider is
        # still free to sample differently on identical input. Omitted when unset, same as above.
        if seed is not None:
            request_params["seed"] = seed

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

        # Fail-fast on un-fittable TPM; otherwise block until inside the rolling window.
        reservation = await acquire_reservation(
            self._rate_limiter, messages, max_tokens, self._provider_name
        )

        result = await self._one_attempt(client, request_params, response_model, response_schema)
        if isinstance(result, LLMResponse):
            # Groq json_validate_failed salvage — already typed. Reconcile the
            # reservation with the salvage's real token count (mirrors the repair +
            # normal exits) so the TPM window doesn't keep the cheap chars//4 estimate.
            if reservation is not None:
                reservation.close(result.usage["total_tokens"])
            return result
        response, content, validation_err, parsed = result
        schema_repair_attempts = 0
        # The failed first attempt still burned tokens; carry them so the returned usage
        # meters BOTH round-trips (emit_token_usage otherwise under-reports a repaired call
        # by one full call). Zero unless a repair fires below.
        first_prompt = 0
        first_completion = 0
        if validation_err is not None:
            first_usage = response.usage
            first_prompt = first_usage.prompt_tokens if first_usage else 0
            first_completion = first_usage.completion_tokens if first_usage else 0
            # Repair retry: full second round-trip with the bad output + hint
            # appended. Logged so the ~2× cost + latency isn't silent.
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
                        f"{truncate(str(validation_err), 600)}\n\n"
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
                # Fold the failed first attempt's tokens onto the salvaged repair response.
                result.usage["prompt_tokens"] += first_prompt
                result.usage["completion_tokens"] += first_completion
                result.usage["total_tokens"] = (
                    result.usage["prompt_tokens"] + result.usage["completion_tokens"]
                )
                # Reconcile the rolling-window reservation with the ACTUAL two-round-trip
                # total, not the cheap chars//4 estimate — else the TPM self-throttle
                # under-counts on exactly the heaviest (repaired) calls. Mirrors line ~204.
                if reservation is not None:
                    reservation.close(result.usage["total_tokens"])
                return result
            response, content, validation_err, parsed = result
            if validation_err is not None:
                content_len = len(content.strip())
                cause = (
                    "provider degraded — empty/truncated response after repair retry"
                    if content_len < 20
                    else "schema-noncompliant after repair retry"
                )
                err = MetaPromptParseError(
                    raw=content,
                    error=validation_err,
                    attempts=2,
                    **_failure_diagnostics(response, first_prompt, first_completion),
                )
                logger.error(
                    "%s: %s parse failed AGAIN after repair retry (%d errors, "
                    "%d content chars) — %s. Round will record the failure and "
                    "continue with zero candidates. [%s]",
                    self._provider_name,
                    schema_name,
                    validation_err.error_count(),
                    content_len,
                    cause,
                    err.diagnosis(),
                )
                raise err from validation_err

        usage = response.usage
        if reservation is not None and usage is not None:
            reservation.close(usage.total_tokens)
        prompt_tokens = (usage.prompt_tokens if usage else 0) + first_prompt
        completion_tokens = (usage.completion_tokens if usage else 0) + first_completion
        return LLMResponse(
            content=content,
            reasoning=(
                (getattr(response.choices[0].message, "reasoning", None) or "")
                if response.choices
                else ""
            ),
            model=response.model,
            usage={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
            parsed=parsed,
            schema_repair_attempts=schema_repair_attempts,
        )

    async def _one_attempt(
        self,
        client: AsyncOpenAI,
        request_params: dict[str, Any],
        response_model: type[BaseModel] | None,
        response_schema: dict[str, Any] | None,
    ) -> LLMResponse | tuple[Any, str, ValidationError | None, Any]:
        """One provider round-trip + parse.

        Returns either an :class:`LLMResponse` (Groq ``json_validate_failed``
        salvage — already validated) or ``(response, content, validation_err,
        parsed)`` — caller decides retry vs raise on non-None ``validation_err``
        and consumes ``parsed`` directly on success (parsed once here, never
        re-validated by the caller).

        SDK ``max_retries`` covers 408/409/429/5xx + Retry-After; this layer
        only intercepts request-too-large, 404 model-not-found, and Groq's 400
        quirk. ``with_raw_response`` exposes ``x-ratelimit-limit-*`` for the limiter.
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

        try:
            parsed = parse_response_content(
                content, response_model, response_schema, self._provider_name
            )
        except ValidationError as err:
            return response, content, err, None
        return response, content, None, parsed

    def _try_recover_from_chat_error(
        self,
        exc: Exception,
        request_params: dict[str, Any],
        response_model: type[BaseModel] | None,
    ) -> LLMResponse | None:
        """Known-error translation: too-large + 404 raise clearer, Groq json_validate_failed salvages, else ``None`` ⇒ re-raise."""
        raise_if_request_too_large(exc, self._provider_name)
        if getattr(exc, "status_code", None) == 404:
            model_name = request_params.get("model", "unknown")
            raise ValueError(
                f"Model '{model_name}' not found on {self._provider_name}. "
                f"Update the optimizer node `model` in "
                f"datasets/_optimizer/pipeline.json (or the dataset's pipeline "
                f"overlay for a backend node)."
            ) from exc
        return try_groq_json_validate_repair(
            exc, request_params, self._provider_name, response_model
        )


__all__ = ["OpenAICompatibleClient"]
