from __future__ import annotations

import functools
import json
import socket
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, NamedTuple

from pydantic import SecretStr

from promptpotter.domain.pipeline_schema import token_bound_of_bytes
from promptpotter.domain.spend import KeySource
from promptpotter.infrastructure.llm.pricing import (
    PriceListUnreachableError,
    RateCeiling,
    rate_ceiling,
)
from promptpotter.infrastructure.llm.send_pacing import Backpressure, RateLimiter
from promptpotter.infrastructure.llm.spend_book import FRAMING_TOKENS, SendBound
from promptpotter.shared.run_scope import run_scope
from promptpotter.shared.tls import tls_context

if TYPE_CHECKING:
    import httpx

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


@functools.cache
def wire_client(base_url: str, *, timeout_s: float) -> httpx.AsyncClient:
    """One pool per host, holding no key: each send carries its own (`post_json`).

    Resends nothing: `OpenAICompatibleClient._admitted_send` is the one loop, and each resend a bill."""
    import httpx

    return httpx.AsyncClient(
        base_url=base_url,
        timeout=httpx.Timeout(timeout_s, connect=5.0),
        transport=httpx.AsyncHTTPTransport(
            verify=tls_context(),
            limits=httpx.Limits(max_connections=1000, max_keepalive_connections=100),
            socket_options=KEEPALIVE_OPTIONS,
        ),
    )


async def post_json(
    http: httpx.AsyncClient, path: str, body: dict[str, Any], *, headers: dict[str, str]
) -> httpx.Response:
    import httpx

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
