from __future__ import annotations

import asyncio
import functools
import json
import socket
from dataclasses import dataclass, field, fields
from typing import Any, Literal, NamedTuple, NotRequired, TypedDict, cast

import httpx
from pydantic import BaseModel, Field, SecretStr

from promptpotter.domain.pipeline_schema import token_bound_of_bytes
from promptpotter.domain.spend import KeySource, TokenAccount
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.llm.pricing import (
    PriceListUnreachableError,
    RateCeiling,
    rate_ceiling,
)
from promptpotter.infrastructure.llm.send_pacing import Backpressure, RateLimiter
from promptpotter.infrastructure.llm.spend_book import FRAMING_TOKENS, SendBound
from promptpotter.infrastructure.run_scope import run_scope
from promptpotter.shared.tls import tls_context


class ToolCall(TypedDict):
    """One call a model asked for: ``arguments`` parsed, never the wire's JSON string."""

    id: str
    name: str
    arguments: dict[str, Any]


class ChatMessage(TypedDict):
    """One turn in the provider-neutral shape each client translates to its own wire."""

    role: Literal["system", "user", "assistant", "tool"]
    content: str
    # An assistant turn's calls, replayed so the tool turns after it answer them.
    tool_calls: NotRequired[list[ToolCall]]
    # A tool turn: the call it answers.
    tool_call_id: NotRequired[str]


# Each key under these is a name (a property, a definition), never a keyword.
_NAMED = frozenset({"properties", "patternProperties", "$defs", "definitions"})
# Each value under these is data, never a schema.
_DATA = frozenset({"default", "const", "enum", "examples"})


def strip_titles(schema: object) -> object:
    """A schema is serialized into the INPUT, so Pydantic's auto `title`s are prompt tokens."""
    if isinstance(schema, list):
        return [strip_titles(v) for v in schema]
    if not isinstance(schema, dict):
        return schema
    return {
        k: v
        if k in _DATA
        else {name: strip_titles(s) for name, s in v.items()}
        if k in _NAMED
        else strip_titles(v)
        for k, v in schema.items()
        if k != "title"
    }


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    # A JSON Schema object, and prompt text: the model reads every field of it.
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ChatRequest:
    """``None`` = not asked for; a client refuses an asked field it cannot send (``OpenAICompatibleClient.SENDS``)."""

    messages: list[ChatMessage]
    model: str
    temperature: float | None = None
    max_tokens: int | None = None
    response_model: type[BaseModel] | None = None
    response_schema: dict[str, Any] | None = None
    reasoning_effort: str | None = None
    top_p: float | None = None
    seed: int | None = None
    route_order: list[str] | None = None
    tools: tuple[Tool, ...] | None = None

    @functools.cached_property
    def wire_schema(self) -> dict[str, Any] | None:
        """The schema SENT, titles stripped: the client sends it and `hash_call` keys on it."""
        schema = self.response_schema or (
            self.response_model.model_json_schema() if self.response_model else None
        )
        return None if schema is None else cast("dict[str, Any]", strip_titles(schema))

    def asked(self) -> frozenset[str]:
        return frozenset(f.name for f in fields(self) if getattr(self, f.name) is not None)


class LLMResponse(StrictModel):
    content: str = Field(..., description="Response content")
    reasoning: str = Field(
        "",
        description=(
            "The model's own thinking channel (``message.reasoning`` on the "
            "OpenAI-compat wire); empty for non-reasoning models. A CORE field: a "
            "model with nowhere to put its internal process answers without one, so "
            "the slot is part of the ask and capturing it is part of the contract. "
            "Strictly ANALYTICAL — it rides the ledger to the audit trail and the "
            "operator's node detail, and must NEVER feed a gate, metric, validator, "
            "scorer, or cache key. Having no code reader is by design; do not delete "
            "it as dead surface."
        ),
    )
    model: str = Field(..., description="Model used")
    usage: TokenAccount | None = Field(
        default_factory=TokenAccount,
        description=(
            "What this call consumed. A provider's own spelling stops at the client that "
            "parses it: every client normalizes to this account, and no consumer converts. "
            "``None``: the provider reported no count, never a 0 nor an estimate."
        ),
    )
    cost_usd: float | None = Field(
        None,
        description=(
            "What the PROVIDER says the call cost, summed across a repair retry (both "
            "round-trips are billed). Its own field rather than a ``usage`` key because "
            "``usage`` counts tokens and this is money. ``None`` means the provider "
            "reported nothing — the honest answer, never a zero nobody measured. The call is "
            "then priced at our rate in its own field (``telemetry.py::rate_priced_usd``), "
            "never in this one."
        ),
    )
    served_by: str | None = Field(
        None,
        description=(
            "WHICH upstream host answered, where the provider is a gateway that routes "
            "onward; ``None`` where it is its own host. Distinct from the provider, which "
            "names who we called and so reads the same for every route through it. "
            "Analytical like ``reasoning`` — never a gate, metric or cache key."
        ),
    )
    finish_reason: str | None = Field(
        None,
        description=(
            "Why the provider stopped the FINAL attempt, in the OpenAI spelling every client "
            "normalizes to — ``length`` is a ``max_tokens`` hit. An earlier attempt's own reason "
            "leads its ``schema_repair_errors`` entry. ``None`` where the provider reported none."
        ),
    )
    parsed: Any | None = Field(
        None,
        description=(
            "Parsed response. Typed Pydantic instance when ``chat`` was "
            "called with ``response_model``; plain dict when only "
            "``response_schema`` was supplied; ``None`` for text-mode."
        ),
    )
    tool_calls: list[ToolCall] = Field(
        default_factory=list,
        description=(
            "The calls the model asked for, in its order; empty where it answered in text or was "
            "offered no tools. A tool call's ``content`` may be empty: the calls are the answer."
        ),
    )
    schema_repair_errors: list[str] = Field(
        default_factory=list,
        description=(
            "The schema rules each retried attempt broke, one entry per paid retry before the "
            "parsed response landed — empty on a clean first parse. Its length is the retry "
            "count: bad templates produce schema-noncompliant JSON and silently double-up the "
            "LLM spend, and the entries say which rule the prompt fails to teach."
        ),
    )


# Per read gap: a reasoning model may think for minutes before its first byte.
WIRE_TIMEOUT_S = 600.0

# A wait no read clock cuts short (a slow first byte, a remote cell) tells a dropped link from an
# answer still coming only by TCP keepalive: about 30s of unanswered probes and the read raises
# a broken connection, which `send_failure.py` resends. A platform lacking a knob keeps its own.
KEEPALIVE_OPTIONS = [(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)] + [
    (socket.IPPROTO_TCP, getattr(socket, knob), value)
    for knob, value in (("TCP_KEEPIDLE", 15), ("TCP_KEEPINTVL", 5), ("TCP_KEEPCNT", 3))
    if hasattr(socket, knob)
]


# Per running loop: a pooled connection is its opening loop's, and a teleprompter compile runs its
# sends under an `asyncio.run` of its own. A closed loop's pools are dropped.
_POOLS: dict[asyncio.AbstractEventLoop, dict[tuple[str, float], httpx.AsyncClient]] = {}


def wire_client(base_url: str, *, timeout_s: float) -> httpx.AsyncClient:
    """One pool per host on the running loop, holding no key: each send carries its own (`post_json`).

    Resends nothing: `OpenAICompatibleClient._admitted_send` is the one loop, and each resend a bill."""
    for closed in [loop for loop in _POOLS if loop.is_closed()]:
        del _POOLS[closed]
    pools = _POOLS.setdefault(asyncio.get_running_loop(), {})
    if (client := pools.get((base_url, timeout_s))) is None:
        client = pools[base_url, timeout_s] = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_s, connect=5.0),
            transport=httpx.AsyncHTTPTransport(
                verify=tls_context(),
                limits=httpx.Limits(max_connections=1000, max_keepalive_connections=100),
                socket_options=KEEPALIVE_OPTIONS,
            ),
        )
    return client


async def post_json(
    http: httpx.AsyncClient, path: str, body: dict[str, Any], *, headers: dict[str, str]
) -> httpx.Response:
    reply = await http.post(path, json=body, headers=headers)
    if not reply.is_success:
        # httpx's own message names the status alone; the provider's words are the diagnosis.
        raise httpx.HTTPStatusError(
            f"HTTP {reply.status_code} from {reply.url}: {reply.text[:500]}",
            request=reply.request,
            response=reply,
        )
    return reply


def send_bound(ceiling: RateCeiling | None, *, sent: object, max_tokens: int | None) -> SendBound:
    sent_bytes = len(json.dumps(sent, ensure_ascii=False, default=str).encode())
    input_tokens = token_bound_of_bytes(sent_bytes) + FRAMING_TOKENS
    output = max_tokens
    if output is None and ceiling is not None:
        output = ceiling.max_output
    if ceiling is None or output is None:
        return SendBound(input_tokens=input_tokens, output_tokens=output, usd=None)
    tier = ceiling.at(input_tokens)
    usd = ceiling.per_request + input_tokens * tier.input + output * tier.output
    return SendBound(input_tokens=input_tokens, output_tokens=output, usd=usd)


async def hold_ceiling(
    model: str, provider: str, *, hosts: tuple[str, ...] | None = None
) -> RateCeiling | None:
    try:
        return await rate_ceiling(model, provider, hosts=hosts)
    except PriceListUnreachableError:
        # Under a USD limit an unfetched price list is an OUTAGE, never "a call no rate bounds".
        book = run_scope().book
        if book is not None and book.prices_sends():
            raise
        return None


class ProviderKey(NamedTuple):
    """The key a client sends on, never empty, and whose wallet each of its sends draws."""

    secret: SecretStr
    paid_by: KeySource


@dataclass
class SendPacing:
    """What one provider ACCOUNT's sends share across clients and cycles; it holds no key."""

    rate_limiter: RateLimiter | None
    # Per MODEL: a gateway throttles one model's upstream while the rest answer.
    backpressure: dict[str, Backpressure] = field(default_factory=dict)
