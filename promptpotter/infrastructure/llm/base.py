from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, ClassVar, NamedTuple, TypeVar

from pydantic import SecretStr

from promptpotter.domain.pipeline_schema import token_bound_of_bytes
from promptpotter.domain.spend import KeySource
from promptpotter.infrastructure.llm.pricing import (
    PriceListUnreachableError,
    RateCeiling,
    rate_ceiling,
)
from promptpotter.infrastructure.llm.request import ChatRequest
from promptpotter.infrastructure.llm.response import LLMResponse
from promptpotter.infrastructure.llm.send_failure import failed_send
from promptpotter.infrastructure.llm.send_pacing import (
    Backpressure,
    RateLimiter,
    apply_discovered_caps,
    drawn_budget,
    held_wait,
    window_slot,
)
from promptpotter.infrastructure.llm.spend_book import (
    FRAMING_TOKENS,
    Billed,
    CallLabel,
    SendBound,
    admitted,
    answered,
    bound_spend_book,
)
from promptpotter.shared.errors import ErrorCategory, SendRefusedError
from promptpotter.shared.tls import tls_context

if TYPE_CHECKING:
    import httpx

    from promptpotter.domain.backend import BackpressureReading

logger = logging.getLogger(__name__)

_R = TypeVar("_R")

# Per read gap: a reasoning model may think for minutes before its first byte.
WIRE_TIMEOUT_S = 600.0


def wire_client(base_url: str, headers: dict[str, str], *, timeout_s: float) -> httpx.AsyncClient:
    """Resends nothing: `LLMClientBase._admitted_send` is the one loop, and each resend a bill."""
    import httpx

    return httpx.AsyncClient(
        base_url=base_url,
        headers=headers,
        timeout=httpx.Timeout(timeout_s, connect=5.0),
        limits=httpx.Limits(max_connections=1000, max_keepalive_connections=100),
        verify=tls_context(),
    )


async def post_json(http: httpx.AsyncClient, path: str, body: dict[str, Any]) -> httpx.Response:
    import httpx

    reply = await http.post(path, json=body)
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
        book = bound_spend_book()
        if book is not None and book.prices_sends():
            raise
        return None


class ProviderKey(NamedTuple):
    """The key a client sends on, never empty, and whose wallet each of its sends draws."""

    secret: SecretStr
    # The account whose own key it is; ``None`` is the box's.
    holder: str | None

    @property
    def paid_by(self) -> KeySource:
        return "host" if self.holder is None else "user"


class LLMClientBase(ABC):
    SENDS: ClassVar[frozenset[str]]
    CREDIT_REFUSAL_STATUSES: ClassVar[frozenset[int]]
    RATE_CAP_HEADERS: ClassVar[tuple[str, str]]

    def __init__(
        self,
        *,
        provider: str,
        display_name: str,
        key: ProviderKey,
        rate_limiter: RateLimiter | None,
    ):
        self._provider = provider
        self._provider_name = display_name
        self._key = key
        self._rate_limiter = rate_limiter
        # Per MODEL: a gateway throttles one model's upstream while the rest answer.
        self._backpressure: dict[str, Backpressure] = {}

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

    @abstractmethod
    async def _chat(self, request: ChatRequest, label: CallLabel) -> LLMResponse: ...

    async def _admitted_send(
        self,
        label: CallLabel,
        *,
        model: str,
        messages: list[dict[str, str]],
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
                        rpm_header, tpm_header = self.RATE_CAP_HEADERS
                        apply_discovered_caps(
                            self._rate_limiter,
                            headers,
                            rpm_header=rpm_header,
                            tpm_header=tpm_header,
                        )
                        spent = billed(reply)
                        admission.close(answered(spent))
                        if spent is not None:
                            slot.billed(spent.usage.total)
                        return reply
            await held_wait(wait, f"{label.node} {self._provider_name}")
