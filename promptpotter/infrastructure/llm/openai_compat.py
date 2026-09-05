"""OpenAI-compatible client (OpenAI, Groq, OpenRouter)."""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel, ValidationError

from promptpotter.domain.spend import TokenAccount
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


def _strip_titles(node: object) -> object:
    """Drop Pydantic's auto-emitted ``title`` keys from a wire schema. The schema is serialized
    into the input, so these are prompt tokens the model reads and learns nothing from."""
    if isinstance(node, dict):
        return {k: _strip_titles(v) for k, v in node.items() if k != "title"}
    if isinstance(node, list):
        return [_strip_titles(v) for v in node]
    return node


def _attempt_usage(response: ChatCompletion) -> TokenAccount:
    """Usage for ONE round-trip, per-attempt on purpose. ``reasoning`` is the tell — a reasoning model can spend its
    whole budget thinking and emit nothing — but only if read off the attempt that actually failed.

    Where the OpenAI wire's spelling ENDS: ``prompt_tokens`` / ``cached_tokens`` become ``input`` /
    ``cache_read`` here and travel as that account everywhere after."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return TokenAccount()
    out_details = getattr(usage, "completion_tokens_details", None)
    in_details = getattr(usage, "prompt_tokens_details", None)
    return TokenAccount(
        input=usage.prompt_tokens,
        output=usage.completion_tokens,
        reasoning=int(getattr(out_details, "reasoning_tokens", 0) or 0),
        cache_read=int(getattr(in_details, "cached_tokens", 0) or 0),
        cache_write=int(getattr(in_details, "cache_write_tokens", 0) or 0),
    )


def _attempt_cost(response: ChatCompletion) -> float | None:
    """What the provider says this ONE round-trip cost. OpenRouter reports it as an extra on the usage object (the SDK's models
    are ``extra="allow"``); Groq and OpenAI report nothing, and ``None`` sends the reader to the rate table rather than
    quoting a zero it never measured."""
    usage = getattr(response, "usage", None)
    cost = getattr(usage, "cost", None) if usage is not None else None
    return float(cost) if cost is not None else None


def _billed_cost(first: float | None, second: float | None) -> float | None:
    """Both round-trips of a repaired call are billed, same contract the token sums follow. ``None`` only when NEITHER side
    reported — one silent half must not drag a real number down to nothing."""
    if first is None and second is None:
        return None
    return (first or 0.0) + (second or 0.0)


def _served_by(response: ChatCompletion) -> str | None:
    """The upstream host the gateway routed to. OpenRouter reports it on the response root (the
    SDK's models are ``extra="allow"``); a provider that IS the host reports nothing, and ``None``
    says we do not know rather than naming the gateway a second time."""
    served = (getattr(response, "model_extra", None) or {}).get("provider")
    return str(served) if served else None


def _finish_reason(response: ChatCompletion) -> str | None:
    return response.choices[0].finish_reason if getattr(response, "choices", None) else None


def _failure_diagnostics(response: ChatCompletion, first: TokenAccount) -> dict[str, Any]:
    """The failed call's BILLED account. ``usage`` sums both round-trips — that is the billing
    contract, and a failed call is billed like a good one; only ``model`` and ``finish_reason``
    describe the second attempt alone, being quantities that do not add."""
    message = response.choices[0].message if getattr(response, "choices", None) else None
    return {
        "model": getattr(response, "model", None),
        "finish_reason": _finish_reason(response),
        "usage": _attempt_usage(response) + first,
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
        usage_accounting: bool = False,
    ):
        self._api_key = api_key
        self._base_url = base_url
        self._max_retries = max_retries
        self._timeout = timeout
        self._provider_name = provider_name
        self._rate_limiter = rate_limiter
        self._usage_accounting = usage_accounting
        self._client: AsyncOpenAI | None = None

    def _ensure_client(self) -> AsyncOpenAI:
        if self._client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError as err:
                raise ImportError(
                    f"openai is a core dependency, so its absence means {sys.executable} is not "
                    "the interpreter promptpotter was installed into. Re-run from the repo venv — "
                    "installing openai here would hide the broken install, not fix it."
                ) from err

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
        route_order: list[str] | None = None,
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
        # Ask for the cost + cache breakdown rather than hoping it rides along. Via `extra_body`
        # because `create()` takes named params only: a bare `usage=` is a TypeError in the SDK,
        # never a request the provider gets to answer.
        extra_body: dict[str, Any] = {}
        if self._usage_accounting:
            extra_body["usage"] = {"include": True}
        # A provider's implicit prefix cache is per-REPLICA, so it pays only where ONE route is hit
        # repeatedly. A throughput sort (`:nitro`) re-ranks per call, which on `deepseek-v4-flash`
        # put three of four live optimizer calls on Baidu — an endpoint that never caches, measured
        # at 0% over four consecutive identical prompts. Naming the hosts IN ORDER is the only
        # deterministic lever; `allow_fallbacks` keeps a dead endpoint degrading the route rather
        # than failing the run. Measured on the real l1_generate prompt: a pinned Alibaba held
        # 77.4% capture across 31.7 min — wider than the gap the loop leaves between optimizer
        # calls — against 0% scattered. Names are OpenRouter's own `provider_name`; read them off
        # `served_by` in the ledger, never from the catalogue, and never gate on
        # `supports_implicit_caching`, which reads False on 14 of 15 deepseek endpoints including
        # the one measured at 96.7%.
        if route_order:
            extra_body["provider"] = {"order": list(route_order), "allow_fallbacks": True}
        if extra_body:
            request_params["extra_body"] = extra_body

        wire_schema = response_schema or (
            response_model.model_json_schema() if response_model else None
        )
        if wire_schema is not None:
            # The JSON Schema is serialized into the INPUT, so every key in it is prompt text.
            # Pydantic auto-emits a `title` per field and per model that no provider needs and
            # no model learns from — `l1_wire_schema.py::_inline_refs` already strips them, but
            # only `l1_generate` passes through it, so the other four nodes shipped them on
            # every call. Stripped once here, at the one seam every schema crosses.
            wire_schema = cast("dict[str, Any]", _strip_titles(wire_schema))
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
                reservation.close(result.usage.total)
            return result
        response, content, validation_err, parsed = result
        schema_repair_attempts = 0
        # The failed first attempt still burned tokens; carry them so the returned usage
        # meters BOTH round-trips (emit_token_usage otherwise under-reports a repaired call
        # by one full call). Zero unless a repair fires below.
        first = TokenAccount()
        first_cost: float | None = None
        if validation_err is not None:
            # The FAILING attempt's own account. Captured here because `response` is about
            # to be rebound to the retry's, and the retry cannot answer why this one was
            # rejected — `finish_reason="length"` here is the difference between "the
            # optimizer prompt outgrew max_tokens" and "the provider degraded", which classify
            # to opposite owners and opposite fixes (`OptimizerPromptParseError.is_empty`).
            first = _attempt_usage(response)
            first_cost = _attempt_cost(response)
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
            cause = (
                "truncated at max_tokens — the prompt asks for more than the budget carries"
                if first_finish_reason == "length"
                else "provider returned empty/truncated content"
                if content_len < MIN_CONTENT_CHARS
                else "response is schema-noncompliant"
            )
            repair_params = {
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
            # Noncompliance gets the repair AND THEN a clean re-ask, because one failed repair
            # is not evidence the PROMPT is at fault — and the re-ask is the rung that actually
            # rescues a flaky provider, by the same independent-sample argument made above. It
            # also gives this branch a `reproduced` reading, which is what separates a bad
            # prompt from a bad moment. A size- or emptiness-driven failure still gets the clean
            # re-ask alone: the repair is the move that cannot help there.
            ladder = (
                [(RETRY_CLEAN_REASK, dict(request_params))]
                if clean_reask
                else [
                    (RETRY_SCHEMA_REPAIR, repair_params),
                    (RETRY_CLEAN_REASK, dict(request_params)),
                ]
            )
            for attempt_no, (retry_kind, retry_params) in enumerate(ladder, start=1):
                logger.warning(
                    "%s: %s parse failed (%d errors, %d content chars, finish=%s) on %s — %s. "
                    "Retrying via %s (rung %d of %d; each is a full call).",
                    self._provider_name,
                    schema_name,
                    validation_err.error_count(),
                    content_len,
                    first_finish_reason,
                    request_params.get("model", "?"),
                    cause,
                    retry_kind,
                    attempt_no,
                    len(ladder),
                )
                result = await self._one_attempt(
                    client, retry_params, response_model, response_schema
                )
                schema_repair_attempts = attempt_no
                if isinstance(result, LLMResponse):
                    result.schema_repair_attempts = schema_repair_attempts
                    # Fold every failed attempt's tokens onto the salvaged response; the account
                    # owns the summing rule, so no field can be forgotten here.
                    result.usage = result.usage + first
                    result.cost_usd = _billed_cost(first_cost, result.cost_usd)
                    # Reconcile the rolling-window reservation with the ACTUAL multi-round-trip
                    # total, not the cheap chars//4 estimate — else the TPM self-throttle
                    # under-counts on exactly the heaviest (repaired) calls. Mirrors line ~204.
                    if reservation is not None:
                        reservation.close(result.usage.total)
                    return result
                response, content, validation_err, parsed = result
                if validation_err is None:
                    break
                if attempt_no == len(ladder):
                    err = OptimizerPromptParseError(
                        raw=content,
                        error=validation_err,
                        attempts=attempt_no + 1,
                        first_finish_reason=first_finish_reason,
                        first_content_chars=content_len,
                        first=first,
                        retry_kind=retry_kind,
                        **_failure_diagnostics(response, first),
                    )
                    # The cause names the FIRST attempt's failure — a later rung's own emptiness
                    # is downstream of it and is already in `diagnosis()`.
                    #
                    # This layer does NOT say what the caller will do about it — that is true
                    # only for `l1_generate`. An `l1_critique` failure is swallowed by
                    # `graceful(...)` and an L2/L3 one never touches candidates, so naming a
                    # consequence here misreports most of these lines as zero-candidate rounds.
                    logger.error(
                        "%s: %s parse failed on every rung (%d errors, %d content chars on the "
                        "last) — %s. Raising to the caller. [%s]",
                        self._provider_name,
                        schema_name,
                        validation_err.error_count(),
                        len(content.strip()),
                        cause,
                        err.diagnosis(),
                    )
                    raise err from validation_err
                # This rung is spent; carry its account so the next one's billing still sums.
                first = first + _attempt_usage(response)
                first_cost = _billed_cost(first_cost, _attempt_cost(response))

        usage = response.usage
        if reservation is not None and usage is not None:
            reservation.close(usage.total_tokens)
        billed = _attempt_usage(response) + first
        # ``reasoning_tokens`` is a SUBSET of ``completion_tokens``, not a fourth total — the
        # provider bills the thinking as output. It rides the success path because that is the
        # only path on which the share is worth anything: until now it survived only on
        # ``OptimizerPromptParseError``, so the one call that could report it was the one that
        # had already failed. Measured on the shipped optimizer route, an ``l1_critique`` call
        # billed 4790 completion tokens for a 1044-character answer — ~94% of the call, and of
        # its 108 s, spent thinking, at ``reasoning_effort: low``. That is the fact behind the
        # optimizer owning a third of every L4 cell's wall-clock, and no surface could say it.
        return LLMResponse(
            content=content,
            reasoning=(
                (getattr(response.choices[0].message, "reasoning", None) or "")
                if response.choices
                else ""
            ),
            model=response.model,
            usage=billed,
            cost_usd=_billed_cost(first_cost, _attempt_cost(response)),
            served_by=_served_by(response),
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
