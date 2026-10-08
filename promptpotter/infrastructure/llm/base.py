from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, ClassVar, TypeVar

from promptpotter.infrastructure.llm.pricing import RateCeiling, rate_ceiling
from promptpotter.infrastructure.llm.rate_limit import (
    MAX_SEND_ATTEMPTS,
    Backpressure,
    RateLimiter,
    apply_discovered_caps,
    wait_with_countdown,
    window_slot,
)
from promptpotter.infrastructure.llm.request import ChatRequest
from promptpotter.infrastructure.llm.response import LLMResponse
from promptpotter.infrastructure.llm.spend_book import (
    FRAMING_TOKENS,
    Billed,
    CallLabel,
    SendBound,
    admitted,
    connection_broke,
    failed_status,
    may_have_billed,
    never_sent,
)
from promptpotter.shared.errors import (
    ErrorCategory,
    RequestTooLargeError,
    SendRefusedError,
    is_provider_credit_refusal,
)

if TYPE_CHECKING:
    from promptpotter.domain.backend import BackpressureReading

logger = logging.getLogger(__name__)

_R = TypeVar("_R")


def send_bound(ceiling: RateCeiling | None, *, sent: object, max_tokens: int | None) -> SendBound:
    """The most one send of ``sent`` — everything the provider reads as input — can cost. No token
    is shorter than a byte of what was sent, so its UTF-8 length bounds the input count; the reply
    is bounded by ``max_tokens``, else by the longest the model returns."""
    input_tokens = len(json.dumps(sent, ensure_ascii=False, default=str).encode()) + FRAMING_TOKENS
    output = max_tokens
    if output is None and ceiling is not None:
        output = ceiling.max_output
    if ceiling is None or output is None:
        return SendBound(input_tokens=input_tokens, output_tokens=output, usd=None)
    tier = ceiling.at(input_tokens)
    usd = ceiling.per_request + input_tokens * tier.input + output * tier.output
    return SendBound(input_tokens=input_tokens, output_tokens=output, usd=usd)


def _retry_wait(exc: BaseException, attempt: int) -> float | None:
    """How long to wait before sending again after a failure that is not a throttle, or ``None``
    to give up. A read timeout is never retried: the provider may still be generating, and a
    second send is a second bill. A broken connection is, like a 5xx: its send stays held."""
    if attempt + 1 >= MAX_SEND_ATTEMPTS:
        return None
    status = failed_status(exc)
    if (status is not None and status >= 500) or never_sent(exc) or connection_broke(exc):
        return float(2**attempt)
    return None


def _throttle(exc: BaseException) -> tuple[object | None, str] | None:
    """The headers and body of a 429, or ``None`` for any other failure. An over-cap request
    carries its 429 as the cause and is terminal (``RequestTooLargeError``), and a refusal is
    already decided."""
    if isinstance(exc, (RequestTooLargeError, SendRefusedError)) or failed_status(exc) != 429:
        return None
    resp = getattr(exc, "response", None)
    body = getattr(resp, "text", None) if resp is not None else None
    return getattr(resp, "headers", None), body if body is not None else str(exc)


class LLMClientBase(ABC):
    """A provider client. Every request it sends goes through :meth:`_admitted_send` — held against
    the run's spend book, throttled, sent, retried, settled with its bill — so no call reaches a
    provider unadmitted and none is billed without a record."""

    SENDS: ClassVar[frozenset[str]]
    """The ``ChatRequest`` fields this client carries to its provider."""
    CREDIT_REFUSAL_STATUSES: ClassVar[frozenset[int]]
    """The HTTP statuses its provider answers a spent account with."""
    RATE_CAP_HEADERS: ClassVar[tuple[str, str]]
    """The reply headers naming its provider's requests- and tokens-per-minute caps."""

    def __init__(self, *, provider: str, display_name: str, rate_limiter: RateLimiter | None):
        self._provider = provider
        self._provider_name = display_name
        self._rate_limiter = rate_limiter
        # Per MODEL: a gateway throttles one model's upstream while the rest answer. The client is
        # one per provider per process, so every campaign here answers the pushback together.
        self._backpressure: dict[str, Backpressure] = {}

    def pushback(self, model: str) -> BackpressureReading | None:
        """What the provider's pushback on *model* is holding right now — the reading a backend's
        cells serve the dashboard (:meth:`Backpressure.reading`)."""
        held = self._backpressure.get(model)
        return None if held is None else held.reading()

    async def chat(self, request: ChatRequest, *, label: CallLabel) -> LLMResponse:
        """``label`` names whose call it is on its usage record."""
        # A field that evaporates is worse than one that refuses: it reaches no wire, the round
        # still scores, and the difference is credited to a mutation nothing carried.
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
        """One provider round-trip. Retried only where the failed send billed nothing or the
        provider asked for a later one — a throttle whenever this model's backpressure lets it, a
        5xx or a connection never made a bounded number of times; each retry is admitted on its
        own. ``send`` answers the reply's headers and the reply; ``billed`` reads what a reply
        used — ``None`` for a reply that reports none, which leaves the send unreported."""
        bound = send_bound(
            await rate_ceiling(model, self._provider), sent=sent, max_tokens=max_tokens
        )
        backpressure = self._backpressure.setdefault(
            model, Backpressure(f"{self._provider_name} {model}")
        )
        attempt = 0
        while True:
            # Queued for the window BEFORE it is held: a request cancelled or refused while it
            # waits never left, and holds nothing — in the book or, once refused, in the window.
            async with (
                backpressure.send() as ticket,
                window_slot(
                    self._rate_limiter, messages, max_tokens, provider_name=self._provider_name
                ) as slot,
            ):
                with admitted(label, bound, model=model, provider=self._provider) as admission:
                    slot.leaves()
                    try:
                        headers, reply = await send()
                    except Exception as exc:
                        if not may_have_billed(exc):
                            admission.release()
                        if failed_status(
                            exc
                        ) in self.CREDIT_REFUSAL_STATUSES and is_provider_credit_refusal(str(exc)):
                            raise SendRefusedError(
                                f"{self._provider_name} refused the call for lack of credit: "
                                f"{str(exc)[:300]}",
                                category=ErrorCategory.PROVIDER_CREDIT,
                            ) from exc
                        if (throttle := _throttle(exc)) is not None:
                            headers, body = throttle
                            backpressure.throttled(ticket, headers=headers, body=body)
                            continue
                        if (wait := _retry_wait(exc, attempt)) is None:
                            raise
                        logger.warning(
                            "%s: %s on %s (attempt %d/%d); waiting %.1fs",
                            self._provider_name,
                            type(exc).__name__,
                            label.node,
                            attempt + 1,
                            MAX_SEND_ATTEMPTS,
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
                        if (spent := billed(reply)) is None:
                            admission.unreported()
                            return reply
                        admission.settle(spent)
                        slot.billed(spent.usage.total)
                        return reply
            attempt += 1
            await wait_with_countdown(wait, f"{label.node} {self._provider_name}")


__all__ = ["Billed", "LLMClientBase", "send_bound"]
