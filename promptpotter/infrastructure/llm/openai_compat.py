from __future__ import annotations

import logging
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel, ValidationError

from promptpotter.domain.search_point import PARAM_SCOPE_KEYS
from promptpotter.domain.spend import TokenAccount
from promptpotter.infrastructure.llm.base import LLMClientBase, hold_ceiling
from promptpotter.infrastructure.llm.json_parse import (
    MIN_CONTENT_CHARS,
    RETRY_CLEAN_REASK,
    RETRY_SCHEMA_REPAIR,
    OptimizerPromptParseError,
    parse_response_content,
    try_groq_json_validate_repair,
)
from promptpotter.infrastructure.llm.pricing import PriceTier
from promptpotter.infrastructure.llm.request import ChatRequest
from promptpotter.infrastructure.llm.response import LLMResponse
from promptpotter.infrastructure.llm.send_pacing import (
    OPENAI_RPM_HEADER,
    OPENAI_TPM_HEADER,
    RateLimiter,
    raise_if_request_too_large,
)
from promptpotter.infrastructure.llm.spend_book import Billed, CallLabel
from promptpotter.infrastructure.tls import tls_context
from promptpotter.shared.answer_text import truncate

if TYPE_CHECKING:
    from openai import AsyncOpenAI
    from openai.types.chat import ChatCompletion

logger = logging.getLogger(__name__)


def _strip_titles(node: object) -> object:
    if isinstance(node, dict):
        return {k: _strip_titles(v) for k, v in node.items() if k != "title"}
    if isinstance(node, list):
        return [_strip_titles(v) for v in node]
    return node


def reply_usage(response: ChatCompletion) -> TokenAccount:
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


def reply_cost(response: ChatCompletion) -> float | None:
    """``None`` where the provider reports none (Groq, OpenAI) — never a zero nobody measured."""
    usage = getattr(response, "usage", None)
    cost = getattr(usage, "cost", None) if usage is not None else None
    return float(cost) if cost is not None else None


def _billed_cost(first: float | None, second: float | None) -> float | None:
    if first is None and second is None:
        return None
    return (first or 0.0) + (second or 0.0)


def reply_served_by(response: ChatCompletion) -> str | None:
    served = (getattr(response, "model_extra", None) or {}).get("provider")
    return str(served) if served else None


def reply_bill(response: ChatCompletion, *, model: str | None) -> Billed | None:
    """``None`` is a send whose bill never came, never one that used nothing."""
    if getattr(response, "usage", None) is None:
        return None
    return Billed(reply_usage(response), reply_cost(response), reply_served_by(response), model)


def _finish_reason(response: ChatCompletion) -> str | None:
    return response.choices[0].finish_reason if getattr(response, "choices", None) else None


def _failure_diagnostics(response: ChatCompletion, first: TokenAccount) -> dict[str, Any]:
    message = response.choices[0].message if getattr(response, "choices", None) else None
    return {
        "model": getattr(response, "model", None),
        "finish_reason": _finish_reason(response),
        "usage": reply_usage(response) + first,
        "reasoning_chars": len(getattr(message, "reasoning", None) or "" if message else ""),
    }


# Exactly the node-config keys `_request_params` puts on the wire; every other is the BACKEND's.
PROVIDER_REQUEST_PARAMS: frozenset[str] = frozenset(
    {"temperature", "max_tokens", "reasoning_effort", "seed", "response_format", "top_p"}
)

# An axis outside this set is one the optimizer can open, search and never move.
assert PARAM_SCOPE_KEYS <= PROVIDER_REQUEST_PARAMS

PROVIDER_DEFAULT_EFFORT = "default"
"""The one rung that OMITS the field; ``none`` is a provider value, reasoning OFF, never absent."""


def sent_effort(effort: str | None) -> str | None:
    return None if effort == PROVIDER_DEFAULT_EFFORT else effort


@dataclass(frozen=True)
class ProviderSpec:
    display_name: str
    api_key_attr: str
    base_url: str | None = None
    timeout: float | None = None
    # False by default: a gateway body extension is a 400 on a provider that lacks it.
    gateway: bool = False


def _gateway_body(
    spec: ProviderSpec | None,
    provider: str,
    *,
    route_order: Sequence[str] | None,
    allow_fallbacks: bool,
    max_price: PriceTier | None,
    reasoning_effort: str | None,
) -> dict[str, Any] | None:
    if spec is None or not spec.gateway:
        if route_order:
            raise ValueError(
                f"route_order names a gateway's hosts, and {provider!r} is not a gateway. Unset "
                "it on the node, or name a gateway as its `provider`."
            )
        return None
    body: dict[str, Any] = {"usage": {"include": True}}
    route: dict[str, Any] = {}
    if max_price is not None:
        route["max_price"] = {
            "prompt": max_price.input * 1_000_000,
            "completion": max_price.output * 1_000_000,
        }
    if route_order:
        route |= {"order": list(route_order), "allow_fallbacks": allow_fallbacks}
    if route:
        body["provider"] = route
    # The gateway's spelling: a third party's sender drops the top-level field for unlisted models.
    if (effort := sent_effort(reasoning_effort)) is not None:
        body["reasoning"] = {"effort": effort}
    return body


def gateway_body(
    spec: ProviderSpec | None,
    provider: str,
    *,
    route_order: Sequence[str] | None,
    reasoning_effort: str | None,
    max_price: PriceTier | None,
) -> dict[str, Any] | None:
    """OUR send: held at the dearest host, so fallbacks stay on and ``max_price`` caps the answerer."""
    return _gateway_body(
        spec,
        provider,
        route_order=route_order,
        allow_fallbacks=True,
        max_price=max_price,
        reasoning_effort=reasoning_effort,
    )


def cell_gateway_body(
    spec: ProviderSpec | None,
    provider: str,
    *,
    route_order: Sequence[str] | None,
    reasoning_effort: str | None,
) -> dict[str, Any] | None:
    """A THIRD PARTY's sender: nothing here caps a host, so none off the route may answer."""
    return _gateway_body(
        spec,
        provider,
        route_order=route_order,
        allow_fallbacks=False,
        max_price=None,
        reasoning_effort=reasoning_effort,
    )


def _validation_summary(err: ValidationError, content: str, finish_reason: str | None) -> str:
    broke = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in err.errors()[:5])
    return f"finish={finish_reason} || {broke} || emitted: {truncate(content, 1500)}"


@dataclass(frozen=True)
class _ParsedReply:
    response: ChatCompletion
    content: str
    parsed: Any
    first: TokenAccount = field(default_factory=TokenAccount)
    first_cost: float | None = None
    repair_errors: list[str] = field(default_factory=list)


def _llm_response(landed: _ParsedReply) -> LLMResponse:
    response = landed.response
    billed = reply_usage(response) + landed.first
    # ``reasoning_tokens`` is a SUBSET of ``completion_tokens``, never a fourth total.
    return LLMResponse(
        content=landed.content,
        reasoning=(
            (getattr(response.choices[0].message, "reasoning", None) or "")
            if response.choices
            else ""
        ),
        model=response.model,
        usage=billed,
        cost_usd=_billed_cost(landed.first_cost, reply_cost(response)),
        served_by=reply_served_by(response),
        finish_reason=_finish_reason(response),
        parsed=landed.parsed,
        schema_repair_errors=landed.repair_errors,
    )


class OpenAICompatibleClient(LLMClientBase):
    SENDS = frozenset(
        {
            "messages",
            "model",
            "temperature",
            "max_tokens",
            "response_model",
            "response_schema",
            "reasoning_effort",
            "top_p",
            "seed",
            "route_order",
        }
    )
    CREDIT_REFUSAL_STATUSES = frozenset({402, 403})
    RATE_CAP_HEADERS = (OPENAI_RPM_HEADER, OPENAI_TPM_HEADER)

    def __init__(
        self,
        api_key: str,
        *,
        provider: str,
        spec: ProviderSpec,
        rate_limiter: RateLimiter | None = None,
    ):
        super().__init__(
            provider=provider, display_name=spec.display_name, rate_limiter=rate_limiter
        )
        self._api_key = api_key
        self._spec = spec
        self._client: AsyncOpenAI | None = None

    def _ensure_client(self) -> AsyncOpenAI:
        if self._client is None:
            try:
                from openai import AsyncOpenAI, DefaultAsyncHttpxClient
            except ImportError as err:
                raise ImportError(
                    f"openai is a core dependency, so its absence means {sys.executable} is not "
                    "the interpreter promptpotter was installed into. Re-run from the repo venv — "
                    "installing openai here would hide the broken install, not fix it."
                ) from err

            # No SDK retries: each is a second bill; `LLMClientBase._admitted_send` is the one loop.
            kwargs: dict[str, Any] = {
                "api_key": self._api_key,
                "max_retries": 0,
                "http_client": DefaultAsyncHttpxClient(verify=tls_context()),
            }
            if self._spec.base_url:
                kwargs["base_url"] = self._spec.base_url
            if self._spec.timeout:
                kwargs["timeout"] = self._spec.timeout
            self._client = AsyncOpenAI(**kwargs)
        return self._client

    async def _chat(self, request: ChatRequest, label: CallLabel) -> LLMResponse:
        client = self._ensure_client()
        response_model, response_schema = request.response_model, request.response_schema
        request_params = await self._request_params(request)
        result = await self._one_attempt(
            client, request_params, response_model, response_schema, label
        )
        if isinstance(result, LLMResponse):
            return result
        response, content, validation_err, parsed = result
        if validation_err is None:
            return _llm_response(_ParsedReply(response, content, parsed))
        landed = await self._climb_retry_ladder(
            client,
            request_params,
            response_model,
            response_schema,
            label,
            response,
            content,
            validation_err,
        )
        return landed if isinstance(landed, LLMResponse) else _llm_response(landed)

    async def _request_params(self, request: ChatRequest) -> dict[str, Any]:
        seed, top_p = request.seed, request.top_p
        response_model, response_schema = request.response_model, request.response_schema
        request_params: dict[str, Any] = {
            "model": request.model,
            "messages": request.messages,
            "temperature": request.temperature,
        }
        if request.max_tokens is not None:
            request_params["max_tokens"] = request.max_tokens
        # The price the call is admitted on (`base.py::_admitted_send`), so no host bills past it.
        ceiling = await hold_ceiling(request.model, self._provider)
        body = gateway_body(
            self._spec,
            self._provider,
            route_order=request.route_order,
            reasoning_effort=request.reasoning_effort,
            max_price=None if ceiling is None else ceiling.dearest(),
        )
        # Top-level only off a gateway, and omitted when unset: a provider must never see a null.
        if body is None and (effort := sent_effort(request.reasoning_effort)) is not None:
            request_params["reasoning_effort"] = effort
        if seed is not None:
            request_params["seed"] = seed
        if top_p is not None:
            request_params["top_p"] = top_p
        if body is not None:
            request_params["extra_body"] = body

        wire_schema = response_schema or (
            response_model.model_json_schema() if response_model else None
        )
        if wire_schema is not None:
            # The schema is serialized into the INPUT, so Pydantic's auto `title`s are prompt tokens.
            wire_schema = cast("dict[str, Any]", _strip_titles(wire_schema))
            request_params["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": (response_model.__name__ if response_model else "response_schema"),
                    "schema": wire_schema,
                    "strict": False,
                },
            }
        return request_params

    async def _climb_retry_ladder(
        self,
        client: AsyncOpenAI,
        request_params: dict[str, Any],
        response_model: type[BaseModel] | None,
        response_schema: dict[str, Any] | None,
        label: CallLabel,
        response: ChatCompletion,
        content: str,
        rejected: ValidationError,
    ) -> LLMResponse | _ParsedReply:
        validation_err: ValidationError | None
        validation_err = rejected
        repair_errors: list[str] = []
        # Captured before `response` is rebound to a retry's: the FAILING attempt's own account.
        first = reply_usage(response)
        first_cost = reply_cost(response)
        first_finish_reason = _finish_reason(response)
        schema_name = response_model.__name__ if response_model else "<schema>"
        content_len = len(content.strip())

        # No repair rung: it re-sends the whole failed output under the same `max_tokens`.
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
        # A pinned seed is ADVANCED, not dropped: dropping it leaves the route the campaign declared.
        reask_params = dict(request_params)
        if (pinned_seed := reask_params.get("seed")) is not None:
            reask_params["seed"] = pinned_seed + 1
        ladder = (
            [(RETRY_CLEAN_REASK, reask_params)]
            if clean_reask
            else [
                (RETRY_SCHEMA_REPAIR, repair_params),
                (RETRY_CLEAN_REASK, reask_params),
            ]
        )
        for attempt_no, (retry_kind, retry_params) in enumerate(ladder, start=1):
            repair_errors.append(
                _validation_summary(validation_err, content, _finish_reason(response))
            )
            logger.warning(
                "%s: %s parse failed (%d errors, %d content chars, finish=%s) on %s — %s. "
                "Retrying via %s (rung %d of %d; each is a full call). Errors: %s",
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
                truncate(repair_errors[-1], 900),
            )
            result = await self._one_attempt(
                client, retry_params, response_model, response_schema, label
            )
            if isinstance(result, LLMResponse):
                result.schema_repair_errors = list(repair_errors)
                result.usage = result.usage + first
                result.cost_usd = _billed_cost(first_cost, result.cost_usd)
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
            first = first + reply_usage(response)
            first_cost = _billed_cost(first_cost, reply_cost(response))
        return _ParsedReply(response, content, parsed, first, first_cost, repair_errors)

    async def _one_attempt(
        self,
        client: AsyncOpenAI,
        request_params: dict[str, Any],
        response_model: type[BaseModel] | None,
        response_schema: dict[str, Any] | None,
        label: CallLabel,
    ) -> LLMResponse | tuple[Any, str, ValidationError | None, Any]:

        async def send() -> tuple[object | None, LLMResponse | ChatCompletion]:
            try:
                raw = await client.chat.completions.with_raw_response.create(**request_params)
            except Exception as exc:
                recovered = self._try_recover_from_chat_error(exc, request_params, response_model)
                if recovered is None:
                    raise
                return None, recovered
            return raw.headers, raw.parse()

        def billed(reply: LLMResponse | ChatCompletion) -> Billed | None:
            if isinstance(reply, LLMResponse):
                # A salvaged 400 was generated, and billed, but its body reports no usage.
                return None
            return reply_bill(reply, model=reply.model)

        response = await self._admitted_send(
            label,
            model=request_params["model"],
            messages=request_params["messages"],
            sent={
                k: request_params[k] for k in ("messages", "response_format") if k in request_params
            },
            max_tokens=request_params.get("max_tokens"),
            send=send,
            billed=billed,
        )
        if isinstance(response, LLMResponse):
            return response

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
        raise_if_request_too_large(exc, self._provider_name)
        if getattr(exc, "status_code", None) == 404:
            model_name = request_params.get("model", "unknown")
            raise ValueError(
                f"Model '{model_name}' not found on {self._provider_name}. "
                f"Update the optimizer node `model` in "
                f"promptpotter/assets/optimizers/potter/pipeline.yaml (or the dataset's pipeline "
                f"overlay for a backend node)."
            ) from exc
        return try_groq_json_validate_repair(
            exc, request_params, self._provider_name, response_model
        )


__all__ = [
    "PROVIDER_DEFAULT_EFFORT",
    "PROVIDER_REQUEST_PARAMS",
    "OpenAICompatibleClient",
    "gateway_body",
    "reply_bill",
    "reply_cost",
    "reply_served_by",
    "reply_usage",
    "sent_effort",
]
