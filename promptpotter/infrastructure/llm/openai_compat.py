"""OpenAI-compatible client (OpenAI, Groq, OpenRouter)."""

from __future__ import annotations

import logging
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel, ValidationError

from promptpotter.domain.search_point import PARAM_SCOPE_KEYS
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
from promptpotter.infrastructure.llm.pricing import PriceTier, rate_ceiling
from promptpotter.infrastructure.llm.rate_limit import (
    OPENAI_RPM_HEADER,
    OPENAI_TPM_HEADER,
    RateLimiter,
    raise_if_request_too_large,
)
from promptpotter.infrastructure.llm.request import ChatRequest
from promptpotter.infrastructure.llm.response import LLMResponse
from promptpotter.infrastructure.llm.spend_book import Billed, CallLabel
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


def reply_usage(response: ChatCompletion) -> TokenAccount:
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


def reply_cost(response: ChatCompletion) -> float | None:
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


def reply_served_by(response: ChatCompletion) -> str | None:
    """The upstream host the gateway routed to. OpenRouter reports it on the response root (the
    SDK's models are ``extra="allow"``); a provider that IS the host reports nothing, and ``None``
    says we do not know rather than naming the gateway a second time."""
    served = (getattr(response, "model_extra", None) or {}).get("provider")
    return str(served) if served else None


def reply_bill(response: ChatCompletion, *, model: str | None) -> Billed | None:
    """The bill ONE round-trip's reply reports, or ``None`` where it reports no usage — a send
    whose bill never came, never one that used nothing."""
    if getattr(response, "usage", None) is None:
        return None
    return Billed(reply_usage(response), reply_cost(response), reply_served_by(response), model)


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
        "usage": reply_usage(response) + first,
        "reasoning_chars": len(getattr(message, "reasoning", None) or "" if message else ""),
    }


# The node-config keys that ride the LLM REQUEST — exactly what `OpenAICompatibleClient.chat` puts
# on the wire, which is the only set a provider's `supported_parameters` can speak about. Declared
# beside the sender because that is what makes it answerable: a key added to `chat` and not added
# here is one nothing can report as ignored. Everything else a node declares (`max_sites`,
# `scrape_timeout`, …) belongs to the BACKEND, and no model catalogue has an opinion on it —
# marking one of those would be a confident wrong answer.
PROVIDER_REQUEST_PARAMS: frozenset[str] = frozenset(
    {"temperature", "max_tokens", "reasoning_effort", "seed", "response_format", "top_p"}
)

# Every tunable AXIS must be a key we actually send. The reverse does not hold — `seed` and
# `response_format` ride the wire from HERE without being core search axes (a backend node may
# still open `response_format` as one, and TermNorm does) — but an axis outside this set is one
# the optimizer can open, search and never move: every value produces an identical call, and the
# round still scores the difference. An assert rather than a comment because nothing else fails.
# The same hazard with the MODEL refusing a key we do send is enforced in
# `PipelineSchema._refused`, which leaves such an axis the one value it is running.
assert PARAM_SCOPE_KEYS <= PROVIDER_REQUEST_PARAMS

PROVIDER_DEFAULT_EFFORT = "default"
"""The rung that OMITS the field — ours, and the only one. Every other rung is a value the provider
defines, ``none`` included, which means reasoning genuinely OFF rather than absent. Declared beside
the sender because it is a wire fact: stated anywhere else, it goes out as a literal string."""


def sent_effort(effort: str | None) -> str | None:
    """The rung a request carries, in any spelling: none where the node set none or the rung that
    omits the field."""
    return None if effort == PROVIDER_DEFAULT_EFFORT else effort


@dataclass(frozen=True)
class ProviderSpec:
    display_name: str  # e.g. "Groq" — used in error messages + logs
    api_key_attr: str  # settings field holding the API key
    base_url: str | None = None  # None ⇒ SDK default (OpenAI)
    timeout: float | None = None
    # Whether this provider is OpenRouter's gateway, answering its body extensions: `usage:
    # {include: true}`, which itemizes what the call cost and how much of the prompt its cache
    # served, and `provider.max_price`, which caps what any host may charge. False by default: an
    # unknown body key is a 400 on the providers that lack the extensions.
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
    """``None`` where ``provider`` is no gateway — the ONE place a route is refused there, for
    every sender: a field that reaches no wire would still read as searched."""
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
    # Hosts are the gateway's own `provider_name`, read off `served_by` on the ledger — never its
    # catalogue, whose `supports_implicit_caching` misreports the hosts that do cache.
    if route_order:
        route |= {"order": list(route_order), "allow_fallbacks": allow_fallbacks}
    if route:
        body["provider"] = route
    # The gateway's own spelling: a third party's sender drops the OpenAI-compatible top-level
    # field for a model it does not list, and one rung may not travel two ways.
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
    """The body a send of OURS carries to a gateway. It is admitted at the dearest host its model
    routes to, so a route keeps its fallbacks — a dead host degrades the route, not the run — and
    ``max_price``, the price that hold was taken at, caps whichever host answers."""
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
    """The body a THIRD PARTY's sender carries inside a cell of ours. The cell is held at the
    dearest of the hosts its route names (``LLMSpendBound.hosts``) and nothing here caps a host,
    so no other may answer."""
    return _gateway_body(
        spec,
        provider,
        route_order=route_order,
        allow_fallbacks=False,
        max_price=None,
        reasoning_effort=reasoning_effort,
    )


def _validation_summary(err: ValidationError, content: str, finish_reason: str | None) -> str:
    """Why one attempt stopped, which schema rules it broke, and what it emitted — kept on the
    response so a paid retry's cause is on disk, even when a later rung rescued the call."""
    broke = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in err.errors()[:5])
    return f"finish={finish_reason} || {broke} || emitted: {truncate(content, 1500)}"


@dataclass(frozen=True)
class _ParsedReply:
    response: ChatCompletion
    content: str
    parsed: Any
    # The failed first attempt still burned tokens; carry them so the returned usage
    # counts BOTH round-trips, as each one's own usage record already did. Zero unless a
    # repair fires.
    first: TokenAccount = field(default_factory=TokenAccount)
    first_cost: float | None = None
    repair_errors: list[str] = field(default_factory=list)


def _llm_response(landed: _ParsedReply) -> LLMResponse:
    response = landed.response
    billed = reply_usage(response) + landed.first
    # ``reasoning_tokens`` is a SUBSET of ``completion_tokens`` (thinking is billed as output),
    # not a fourth total; it rides the success path too, where the share is worth reporting.
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
                from openai import AsyncOpenAI
            except ImportError as err:
                raise ImportError(
                    f"openai is a core dependency, so its absence means {sys.executable} is not "
                    "the interpreter promptpotter was installed into. Re-run from the repo venv — "
                    "installing openai here would hide the broken install, not fix it."
                ) from err

            # No SDK retries: a retried send is a second bill, so the one retry loop is the
            # admitted one (`LLMClientBase._admitted_send`), which knows which failures billed.
            kwargs: dict[str, Any] = {"api_key": self._api_key, "max_retries": 0}
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
            # Groq json_validate_failed salvage — already typed.
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
        ceiling = await rate_ceiling(request.model, self._provider)
        body = gateway_body(
            self._spec,
            self._provider,
            route_order=request.route_order,
            reasoning_effort=request.reasoning_effort,
            max_price=None if ceiling is None else ceiling.dearest(),
        )
        # Bounded reasoning is a survival guard (the openrouter/gpt-oss optimizer nodes blow the
        # call deadline at unbounded effort). Off a gateway the OpenAI-compatible field is
        # top-level, omitted when unset so a provider that doesn't accept it never sees a null —
        # and on `PROVIDER_DEFAULT_EFFORT`, the rung that MEANS omission.
        if body is None and (effort := sent_effort(request.reasoning_effort)) is not None:
            request_params["reasoning_effort"] = effort
        # Temperature 0 pins the distribution, not the draw — without a seed the provider is
        # still free to sample differently on identical input. Omitted when unset, same as above.
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
            # The schema is serialized into the INPUT, so every key is prompt text: Pydantic's
            # auto-emitted `title`s are stripped here, the one seam every schema crosses.
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
        # The FAILING attempt's own account. Captured here because `response` is about
        # to be rebound to the retry's, and the retry cannot answer why this one was
        # rejected — `finish_reason="length"` here is the difference between "the
        # optimizer prompt outgrew max_tokens" and "the provider degraded", which classify
        # to opposite owners and opposite fixes (`OptimizerPromptParseError.is_empty`).
        first = reply_usage(response)
        first_cost = reply_cost(response)
        first_finish_reason = _finish_reason(response)
        schema_name = response_model.__name__ if response_model else "<schema>"
        content_len = len(content.strip())

        # Ladder by failure kind. Truncated or empty: a clean re-ask alone, because a repair
        # re-sends the whole failed output under the same `max_tokens` and cannot fit. Noncompliant
        # but substantial: repair, then a clean re-ask. A repeat failure marks the prompt, a
        # differing one the moment (`.reproduced`).
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
        # The re-ask is a second independent sample: a pinned seed is ADVANCED, not dropped,
        # because dropping it takes the rescue off the route the campaign declared.
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
                # Fold every failed attempt's tokens onto the salvaged response; the account
                # owns the summing rule, so no field can be forgotten here.
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
        """One admitted provider round-trip + parse; ``parsed`` is consumed directly and never
        re-validated by the caller. Beyond the send seam's retries, this layer intercepts only
        request-too-large, 404 model-not-found, a spent account and Groq's 400 quirk."""

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
        """Known-error translation: too-large and 404 raise clearer, Groq json_validate_failed
        salvages, else ``None`` ⇒ re-raise."""
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
