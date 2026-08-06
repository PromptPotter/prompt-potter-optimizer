"""OpenAI-compatible client (OpenAI, Groq, OpenRouter)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from promptpotter.infrastructure.llm.base import LLMClientBase
from promptpotter.infrastructure.llm.json_parse import (
    MIN_CONTENT_CHARS,
    RETRY_CLEAN_REASK,
    RETRY_SCHEMA_REPAIR,
    OptimizerPromptParseError,
    parse_response_content,
    try_groq_json_validate_repair,
)
from promptpotter.infrastructure.llm.rate_limit import (
    OPENAI_RPM_HEADER,
    OPENAI_TPM_HEADER,
    RateLimiter,
    acquire_reservation,
    apply_discovered_caps,
    raise_if_request_too_large,
)
from promptpotter.infrastructure.llm.response import LLMResponse
from promptpotter.shared import truncate

if TYPE_CHECKING:
    from openai import AsyncOpenAI
    from openai.types.chat import ChatCompletion

logger = logging.getLogger(__name__)


def _attempt_usage(response: ChatCompletion) -> tuple[int, int, int]:
    """Usage for ONE round-trip, per-attempt on purpose. ``reasoning_tokens`` is the tell — a reasoning model can spend its
    whole budget thinking and emit nothing — but only if read off the attempt that actually failed."""
    usage = getattr(response, "usage", None)
    details = getattr(usage, "completion_tokens_details", None)
    return (
        usage.prompt_tokens if usage else 0,
        usage.completion_tokens if usage else 0,
        int(getattr(details, "reasoning_tokens", 0) or 0),
    )


def _finish_reason(response: ChatCompletion) -> str | None:
    return response.choices[0].finish_reason if getattr(response, "choices", None) else None


def _failure_diagnostics(
    response: ChatCompletion, first_prompt: int, first_completion: int
) -> dict[str, Any]:
    """The SECOND attempt's account plus the BILLED totals. The token counts are deliberately sums across both round-trips —
    that is the billing contract, and a failed call is billed the same as a good one."""
    _, completion, reasoning_tokens = _attempt_usage(response)
    usage = getattr(response, "usage", None)
    message = response.choices[0].message if getattr(response, "choices", None) else None
    return {
        "model": getattr(response, "model", None),
        "finish_reason": _finish_reason(response),
        "prompt_tokens": (usage.prompt_tokens if usage else 0) + first_prompt,
        "completion_tokens": completion + first_completion,
        "reasoning_tokens": reasoning_tokens,
        "reasoning_chars": len(getattr(message, "reasoning", None) or "" if message else ""),
    }


class OpenAICompatibleClient(LLMClientBase):
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
        first_reasoning = 0
        if validation_err is not None:
            # The FAILING attempt's own account. Captured here because `response` is about
            # to be rebound to the retry's, and the retry cannot answer why this one was
            # rejected — `finish_reason="length"` here is the difference between "the
            # optimizer prompt outgrew max_tokens" and "the provider degraded", which classify
            # to opposite owners and opposite fixes (`OptimizerPromptParseError.is_empty`).
            first_prompt, first_completion, first_reasoning = _attempt_usage(response)
            first_finish_reason = _finish_reason(response)
            schema_name = response_model.__name__ if response_model else "<schema>"
            content_len = len(content.strip())

            # RETRY STRATEGY — chosen from how the first attempt failed, because one
            # strategy is actively harmful to the other failure mode.
            #
            # The schema-repair retry re-sends the original prompt PLUS the entire failed
            # output PLUS the error text, then asks for the same answer again under an
            # unchanged `max_tokens`. When the failure was that the answer did not FIT
            # (truncated at the cap) or that nothing came back at all, that is the one
            # thing guaranteed not to help: the request grows by the size of the failure
            # while the budget stays put. Measured: three L1 zero-candidate rounds whose
            # first attempts returned 27,939 / 32 / 28,778 chars had repairs come back at
            # 18 / 0 / 0 — the retry was likelier to fail than the call it was repairing.
            #
            # So a size- or emptiness-driven failure gets a CLEAN RE-ASK: the identical
            # request, once more. No optimizer node pins a seed and all run at temperature
            # 0.3-0.5, so that is a second independent sample — which both stands a real
            # chance of succeeding AND answers the question the classifier would otherwise
            # have to guess at. Fails the same way twice ⇒ a property of the prompt. Fails
            # differently, or succeeds ⇒ the moment, not the prompt (`.reproduced`).
            #
            # Genuine schema-noncompliance — substantial content that parsed but did not
            # bind — keeps the repair: there, showing the model its own error is the
            # informative move, and the output is small enough that re-sending it is cheap.
            clean_reask = first_finish_reason == "length" or content_len < MIN_CONTENT_CHARS
            retry_kind = RETRY_CLEAN_REASK if clean_reask else RETRY_SCHEMA_REPAIR
            cause = (
                "truncated at max_tokens — the prompt asks for more than the budget carries"
                if first_finish_reason == "length"
                else "provider returned empty/truncated content"
                if content_len < MIN_CONTENT_CHARS
                else "response is schema-noncompliant"
            )
            logger.warning(
                "%s: %s parse failed (%d errors, %d content chars, finish=%s) on %s — %s. "
                "Retrying via %s (second full call; cost + latency ~2x).",
                self._provider_name,
                schema_name,
                validation_err.error_count(),
                content_len,
                first_finish_reason,
                request_params.get("model", "?"),
                cause,
                retry_kind,
            )
            if clean_reask:
                retry_params = dict(request_params)
            else:
                retry_params = {
                    **request_params,
                    "messages": [
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
                    ],
                }
            result = await self._one_attempt(client, retry_params, response_model, response_schema)
            schema_repair_attempts = 1
            if isinstance(result, LLMResponse):
                result.schema_repair_attempts = schema_repair_attempts
                # Fold the failed first attempt's tokens onto the salvaged repair response.
                result.usage["prompt_tokens"] += first_prompt
                result.usage["completion_tokens"] += first_completion
                result.usage["reasoning_tokens"] = (
                    result.usage.get("reasoning_tokens", 0) + first_reasoning
                )
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
                err = OptimizerPromptParseError(
                    raw=content,
                    error=validation_err,
                    attempts=2,
                    first_finish_reason=first_finish_reason,
                    first_content_chars=content_len,
                    first_completion_tokens=first_completion,
                    first_reasoning_tokens=first_reasoning,
                    retry_kind=retry_kind,
                    **_failure_diagnostics(response, first_prompt, first_completion),
                )
                # The cause names the FIRST attempt's failure — the repair's own emptiness
                # is downstream of it and is already in `diagnosis()`.
                #
                # This layer does NOT say what the caller will do about it — that is true only
                # for `l1_generate`. An `l1_critique` failure is swallowed by `graceful(...)`
                # and an L2/L3 one never touches candidates, so naming a consequence here
                # misreports most of these lines as zero-candidate rounds.
                logger.error(
                    "%s: %s parse failed AGAIN after repair retry (%d errors, "
                    "%d content chars on the repair) — %s. Raising to the caller. [%s]",
                    self._provider_name,
                    schema_name,
                    validation_err.error_count(),
                    len(content.strip()),
                    cause,
                    err.diagnosis(),
                )
                raise err from validation_err

        usage = response.usage
        if reservation is not None and usage is not None:
            reservation.close(usage.total_tokens)
        attempt_prompt, attempt_completion, attempt_reasoning = _attempt_usage(response)
        prompt_tokens = attempt_prompt + first_prompt
        completion_tokens = attempt_completion + first_completion
        # ``reasoning_tokens`` is a SUBSET of ``completion_tokens``, not a fourth total — the
        # provider bills the thinking as output. It rides the success path because that is the
        # only path on which the share is worth anything: until now it survived only on
        # ``OptimizerPromptParseError``, so the one call that could report it was the one that
        # had already failed. Measured on the shipped optimizer route, an ``l1_critique`` call
        # billed 4790 completion tokens for a 1044-character answer — ~94% of the call, and of
        # its 108 s, spent thinking, at ``reasoning_effort: low``. That is the fact behind the
        # optimizer owning a third of every L4 cell's wall-clock, and no surface could say it.
        reasoning_tokens = attempt_reasoning + first_reasoning
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
                "reasoning_tokens": reasoning_tokens,
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
        """One provider round-trip + parse; ``parsed`` is consumed directly and never re-validated by the caller. SDK retries cover
        408/409/429/5xx — this layer intercepts only request-too-large, 404 model-not-found, and Groq's 400 quirk."""
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
                f"promptpotter/assets/optimizer/pipeline.yaml (or the dataset's pipeline "
                f"overlay for a backend node)."
            ) from exc
        return try_groq_json_validate_repair(
            exc, request_params, self._provider_name, response_model
        )


__all__ = ["OpenAICompatibleClient"]
