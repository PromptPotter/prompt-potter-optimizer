from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeVar, cast

from pydantic import AliasPath, BaseModel, Field, ValidationError

from promptpotter.domain.pipeline_params import PARAM_SCOPE_KEYS
from promptpotter.domain.spend import TokenAccount
from promptpotter.infrastructure.llm.base import (
    WIRE_TIMEOUT_S,
    ProviderKey,
    SendPacing,
    hold_ceiling,
    post_json,
    send_bound,
    wire_client,
)
from promptpotter.infrastructure.llm.json_parse import (
    MIN_CONTENT_CHARS,
    RETRY_CLEAN_REASK,
    RETRY_SCHEMA_REPAIR,
    OptimizerPromptParseError,
    parse_response_content,
    try_groq_json_validate_repair,
)
from promptpotter.infrastructure.llm.pricing import PriceTier
from promptpotter.infrastructure.llm.request import ChatMessage, ChatRequest, Tool, ToolCall
from promptpotter.infrastructure.llm.response import LLMResponse
from promptpotter.infrastructure.llm.send_failure import failed_send
from promptpotter.infrastructure.llm.send_pacing import (
    OPENAI_RPM_HEADER,
    OPENAI_TPM_HEADER,
    Backpressure,
    apply_discovered_caps,
    drawn_budget,
    held_wait,
    raise_if_context_overflow,
    raise_if_request_too_large,
    window_slot,
)
from promptpotter.infrastructure.llm.spend_book import Billed, CallLabel, admitted, answered
from promptpotter.shared.answer_text import truncate
from promptpotter.shared.errors import ErrorCategory, SendRefusedError, validation_reason

if TYPE_CHECKING:
    import httpx

    from promptpotter.domain.backend import BackpressureReading

logger = logging.getLogger(__name__)

_R = TypeVar("_R")


def _strip_titles(node: object) -> object:
    if isinstance(node, dict):
        return {k: _strip_titles(v) for k, v in node.items() if k != "title"}
    if isinstance(node, list):
        return [_strip_titles(v) for v in node]
    return node


class _WireToolCall(BaseModel):
    id: str
    name: str = Field(validation_alias=AliasPath("function", "name"))
    # A JSON string on this wire, parsed at the client so no reader sees the wire's spelling.
    arguments: str = Field("{}", validation_alias=AliasPath("function", "arguments"))

    def parsed(self, provider_name: str) -> ToolCall:
        try:
            arguments = json.loads(self.arguments or "{}")
        except json.JSONDecodeError as err:
            raise ValueError(
                f"{provider_name} returned tool call {self.name!r} whose arguments are "
                f"no JSON: {truncate(self.arguments, 300)}"
            ) from err
        if not isinstance(arguments, dict):
            raise ValueError(
                f"{provider_name} returned tool call {self.name!r} whose arguments are "
                f"no object: {truncate(self.arguments, 300)}"
            )
        return ToolCall(id=self.id, name=self.name, arguments=arguments)


class _Choice(BaseModel):
    content: str | None = Field(None, validation_alias=AliasPath("message", "content"))
    reasoning: str | None = Field(None, validation_alias=AliasPath("message", "reasoning"))
    tool_calls: list[_WireToolCall] | None = Field(
        None, validation_alias=AliasPath("message", "tool_calls")
    )
    finish_reason: str | None = None


class ChatReply(BaseModel):
    model: str
    choices: list[_Choice] = Field(default_factory=list)
    # Every `usage` names its prompt count, so its absence is the reply's: a bill that never came.
    prompt_tokens: int | None = Field(None, validation_alias=AliasPath("usage", "prompt_tokens"))
    completion_tokens: int | None = Field(
        None, validation_alias=AliasPath("usage", "completion_tokens")
    )
    reasoning_tokens: int | None = Field(
        None, validation_alias=AliasPath("usage", "completion_tokens_details", "reasoning_tokens")
    )
    cached_tokens: int | None = Field(
        None, validation_alias=AliasPath("usage", "prompt_tokens_details", "cached_tokens")
    )
    cache_write_tokens: int | None = Field(
        None, validation_alias=AliasPath("usage", "prompt_tokens_details", "cache_write_tokens")
    )
    # ``None`` where the provider reports none (Groq, OpenAI) — never a zero nobody measured.
    cost: float | None = Field(None, validation_alias=AliasPath("usage", "cost"))
    served_by: str | None = Field(None, validation_alias="provider")

    @property
    def tokens(self) -> TokenAccount:
        return TokenAccount(
            input=self.prompt_tokens or 0,
            output=self.completion_tokens or 0,
            reasoning=self.reasoning_tokens or 0,
            cache_read=self.cached_tokens or 0,
            cache_write=self.cache_write_tokens or 0,
        )

    def bill(self) -> Billed | None:
        if self.prompt_tokens is None:
            return None
        return Billed(self.tokens, self.cost, self.served_by, self.model)

    @property
    def finish_reason(self) -> str | None:
        return self.choices[0].finish_reason


def billed_cost(first: float | None, second: float | None) -> float | None:
    if first is None and second is None:
        return None
    return (first or 0.0) + (second or 0.0)


def _failure_diagnostics(response: ChatReply, first: TokenAccount) -> dict[str, Any]:
    return {
        "model": response.model,
        "finish_reason": response.finish_reason,
        "usage": response.tokens + first,
        "reasoning_chars": len(response.choices[0].reasoning or ""),
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
    base_url: str
    timeout: float = WIRE_TIMEOUT_S
    # False by default: a gateway body extension is a 400 on a provider that lacks it.
    gateway: bool = False
    # A GET under `base_url` that needs the key and spends nothing: how a dead key is told apart.
    key_check: str = "/models"


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
    broke = validation_reason(err, shown=5)
    return f"finish={finish_reason} || {broke} || emitted: {truncate(content, 1500)}"


@dataclass(frozen=True)
class _ParsedReply:
    response: ChatReply
    content: str
    parsed: Any
    first: TokenAccount = field(default_factory=TokenAccount)
    first_cost: float | None = None
    repair_errors: list[str] = field(default_factory=list)


def _wire_messages(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    wire: list[dict[str, Any]] = []
    for message in messages:
        turn: dict[str, Any] = {"role": message["role"], "content": message["content"]}
        if calls := message.get("tool_calls"):
            turn["tool_calls"] = [
                {
                    "id": call["id"],
                    "type": "function",
                    "function": {"name": call["name"], "arguments": json.dumps(call["arguments"])},
                }
                for call in calls
            ]
        if (answers := message.get("tool_call_id")) is not None:
            turn["tool_call_id"] = answers
        wire.append(turn)
    return wire


def _wire_tools(tools: tuple[Tool, ...]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": _strip_titles(tool.parameters),
            },
        }
        for tool in tools
    ]


def _llm_response(landed: _ParsedReply, provider_name: str) -> LLMResponse:
    response = landed.response
    choice = response.choices[0]
    # ``reasoning_tokens`` is a SUBSET of ``completion_tokens``, never a fourth total.
    return LLMResponse(
        content=landed.content,
        reasoning=choice.reasoning or "",
        tool_calls=[call.parsed(provider_name) for call in choice.tool_calls or ()],
        model=response.model,
        usage=response.tokens + landed.first,
        cost_usd=billed_cost(landed.first_cost, response.cost),
        served_by=response.served_by,
        finish_reason=response.finish_reason,
        parsed=landed.parsed,
        schema_repair_errors=landed.repair_errors,
    )


class OpenAICompatibleClient:
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
            "tools",
        }
    )
    CREDIT_REFUSAL_STATUSES = frozenset({402, 403})

    def __init__(
        self,
        key: ProviderKey,
        pacing: SendPacing,
        *,
        provider: str,
        spec: ProviderSpec,
    ):
        """Lives as long as its caller's key resolution: the key is never held past it."""
        self._provider = provider
        self._provider_name = spec.display_name
        self._key = key
        self._rate_limiter = pacing.rate_limiter
        self._backpressure = pacing.backpressure
        self._spec = spec

    def pushback(self, model: str) -> BackpressureReading | None:
        held = self._backpressure.get(model)
        return None if held is None else held.reading()

    async def chat(self, request: ChatRequest, *, label: CallLabel) -> LLMResponse:
        # A field that silently reaches no wire still scores, crediting a mutation nothing carried.
        if unsent := sorted(request.asked() - self.SENDS):
            raise ValueError(
                f"{self._provider_name} cannot send {', '.join(unsent)}. Close the axis on the "
                "node (`optimizer.param_keys`) or unset the field, so it never reads as searched."
            )
        return await self._chat(request, label)

    async def _admitted_send(
        self,
        label: CallLabel,
        *,
        model: str,
        messages: Sequence[Mapping[str, object]],
        sent: object,
        max_tokens: int | None,
        send: Callable[[], Awaitable[tuple[object | None, _R]]],
        billed: Callable[[_R], Billed | None],
    ) -> _R:
        """``billed`` answers ``None`` for a reply that reports no usage, which leaves the send unreported."""
        bound = send_bound(
            await hold_ceiling(model, self._provider), sent=sent, max_tokens=max_tokens
        )
        backpressure = self._backpressure.setdefault(
            model, Backpressure(f"{self._provider_name} {model}")
        )
        budget = drawn_budget()
        while True:
            # Queued for the window BEFORE it is held: one cancelled while it waits holds nothing.
            async with (
                backpressure.send() as ticket,
                window_slot(
                    self._rate_limiter, messages, max_tokens, provider_name=self._provider_name
                ) as slot,
            ):
                with admitted(
                    label, bound, model=model, provider=self._provider, paid_by=self._key.paid_by
                ) as admission:
                    slot.leaves()
                    try:
                        headers, reply = await send()
                    except Exception as exc:
                        outcome = failed_send(exc, credit_statuses=self.CREDIT_REFUSAL_STATUSES)
                        admission.close(outcome)
                        if outcome.failure is ErrorCategory.PROVIDER_CREDIT:
                            raise SendRefusedError(
                                f"{self._provider_name} refused the call for lack of credit: "
                                f"{outcome.detail[:300]}",
                                category=ErrorCategory.PROVIDER_CREDIT,
                            ) from exc
                        if outcome.failure is ErrorCategory.PROVIDER_THROTTLED and outcome.sent:
                            backpressure.throttled(
                                ticket, headers=outcome.headers, body=outcome.detail
                            )
                            continue
                        wait = budget.resend_wait() if outcome.resendable else None
                        if wait is None:
                            raise
                        logger.warning(
                            "%s: %s on %s (attempt %d/%d); waiting %.1fs",
                            self._provider_name,
                            type(exc).__name__,
                            label.node,
                            budget.resent,
                            budget.attempts,
                            wait,
                        )
                    else:
                        backpressure.eased(ticket)
                        apply_discovered_caps(
                            self._rate_limiter,
                            headers,
                            rpm_header=OPENAI_RPM_HEADER,
                            tpm_header=OPENAI_TPM_HEADER,
                        )
                        spent = billed(reply)
                        admission.close(answered(spent))
                        if spent is not None:
                            slot.billed(spent.usage.total)
                        return reply
            await held_wait(wait, f"{label.node} {self._provider_name}")

    async def _post(self, body: dict[str, Any]) -> httpx.Response:
        return await post_json(
            wire_client(self._spec.base_url, timeout_s=self._spec.timeout),
            "chat/completions",
            body,
            headers={"Authorization": f"Bearer {self._key.secret.get_secret_value()}"},
        )

    async def _chat(self, request: ChatRequest, label: CallLabel) -> LLMResponse:
        response_model, response_schema = request.response_model, request.response_schema
        request_params = await self._request_params(request)
        result = await self._one_attempt(request_params, response_model, response_schema, label)
        if isinstance(result, LLMResponse):
            return result
        response, content, validation_err, parsed = result
        if validation_err is None:
            return _llm_response(_ParsedReply(response, content, parsed), self._provider_name)
        landed = await self._climb_retry_ladder(
            request_params,
            response_model,
            response_schema,
            label,
            response,
            content,
            validation_err,
        )
        return (
            landed
            if isinstance(landed, LLMResponse)
            else _llm_response(landed, self._provider_name)
        )

    async def _request_params(self, request: ChatRequest) -> dict[str, Any]:
        seed, top_p = request.seed, request.top_p
        response_model, response_schema = request.response_model, request.response_schema
        request_params: dict[str, Any] = {
            "model": request.model,
            "messages": _wire_messages(request.messages),
        }
        if request.temperature is not None:
            request_params["temperature"] = request.temperature
        if request.tools:
            request_params["tools"] = _wire_tools(request.tools)
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
            request_params |= body

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
                    "strict": True,
                },
            }
        return request_params

    async def _climb_retry_ladder(
        self,
        request_params: dict[str, Any],
        response_model: type[BaseModel] | None,
        response_schema: dict[str, Any] | None,
        label: CallLabel,
        response: ChatReply,
        content: str,
        rejected: ValidationError,
    ) -> LLMResponse | _ParsedReply:
        validation_err: ValidationError | None
        validation_err = rejected
        repair_errors: list[str] = []
        # Captured before `response` is rebound to a retry's: the FAILING attempt's own account.
        first = response.tokens
        first_cost = response.cost
        first_finish_reason = response.finish_reason
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
                _validation_summary(validation_err, content, response.finish_reason)
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
            result = await self._one_attempt(retry_params, response_model, response_schema, label)
            if isinstance(result, LLMResponse):
                result.schema_repair_errors = list(repair_errors)
                result.usage = result.usage + first
                result.cost_usd = billed_cost(first_cost, result.cost_usd)
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
            first = first + response.tokens
            first_cost = billed_cost(first_cost, response.cost)
        return _ParsedReply(response, content, parsed, first, first_cost, repair_errors)

    async def _one_attempt(
        self,
        request_params: dict[str, Any],
        response_model: type[BaseModel] | None,
        response_schema: dict[str, Any] | None,
        label: CallLabel,
    ) -> LLMResponse | tuple[ChatReply, str, ValidationError | None, Any]:
        import httpx

        async def send() -> tuple[object | None, LLMResponse | ChatReply]:
            try:
                reply = await self._post(request_params)
            except httpx.HTTPStatusError as refused:
                recovered = self._try_recover_from_refusal(refused, request_params, response_model)
                if recovered is None:
                    raise
                return None, recovered
            return reply.headers, ChatReply.model_validate_json(reply.content)

        def billed(reply: LLMResponse | ChatReply) -> Billed | None:
            if isinstance(reply, LLMResponse):
                # A salvaged 400 was generated, and billed, but its body reports no usage.
                return None
            return reply.bill()

        response = await self._admitted_send(
            label,
            model=request_params["model"],
            messages=request_params["messages"],
            sent={
                k: request_params[k]
                for k in ("messages", "response_format", "tools")
                if k in request_params
            },
            max_tokens=request_params.get("max_tokens"),
            send=send,
            billed=billed,
        )
        if isinstance(response, LLMResponse):
            return response

        if not response.choices:
            raise ValueError(f"{self._provider_name} returned empty choices")
        content = response.choices[0].content or ""
        if response.choices[0].tool_calls:
            # The calls are the answer: no schema governs a turn that asks for tools.
            return response, content, None, None

        try:
            parsed = parse_response_content(
                content, response_model, response_schema, self._provider_name
            )
        except ValidationError as err:
            return response, content, err, None
        return response, content, None, parsed

    def _try_recover_from_refusal(
        self,
        exc: httpx.HTTPStatusError,
        request_params: dict[str, Any],
        response_model: type[BaseModel] | None,
    ) -> LLMResponse | None:
        raise_if_request_too_large(exc, self._provider_name)
        raise_if_context_overflow(exc, self._provider_name)
        if exc.response.status_code == 404:
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
