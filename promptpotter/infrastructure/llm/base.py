from __future__ import annotations

import itertools
import json
import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from pydantic import BaseModel

from promptpotter.infrastructure.llm.pricing import RateCeiling, rate_ceiling
from promptpotter.infrastructure.llm.rate_limit import (
    MAX_429_ATTEMPTS,
    RateLimiter,
    acquire_reservation,
    decide_429_wait,
    wait_with_countdown,
)
from promptpotter.infrastructure.llm.response import LLMResponse
from promptpotter.infrastructure.llm.spend_book import (
    FRAMING_TOKENS,
    Billed,
    CallLabel,
    SendBound,
    admitted,
    may_have_billed,
    never_sent,
)

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


def _status(exc: BaseException) -> int | None:
    """The HTTP status a failed send came back with, read through the errors raised FROM it."""
    seen: BaseException | None = exc
    while seen is not None:
        if isinstance(status := getattr(seen, "status_code", None), int):
            return status
        seen = seen.__cause__
    return None


def _retry_wait(exc: BaseException, attempt: int) -> float | None:
    """How long to wait before sending again, or ``None`` to give up. A read timeout is never
    retried: the provider may still be generating, and a second send is a second bill."""
    status = _status(exc)
    if status == 429:
        resp = getattr(exc, "response", None)
        body = getattr(resp, "text", None) if resp is not None else None
        decision = decide_429_wait(
            getattr(resp, "headers", None) if resp is not None else None,
            body if body is not None else str(exc),
            attempt,
        )
        return None if decision is None else decision.seconds
    if attempt + 1 >= MAX_429_ATTEMPTS:
        return None
    if (status is not None and status >= 500) or never_sent(exc):
        return float(2**attempt)
    return None


class LLMClientBase(ABC):
    """A provider client. Every request it sends goes through :meth:`_admitted_send` — held against
    the run's spend book, throttled, sent, retried, settled — so no call reaches a provider
    unadmitted and none is billed without a record."""

    def __init__(self, *, provider: str, display_name: str, rate_limiter: RateLimiter | None):
        self._provider = provider
        self._provider_name = display_name
        self._rate_limiter = rate_limiter

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str,
        *,
        label: CallLabel,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        response_model: type[BaseModel] | None = None,
        response_schema: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """``model`` is mandatory and concrete — no model fallback lives below this seam. ``label``
        names whose call it is on its usage record. ``response_schema`` overrides
        ``response_model``'s wire schema; passed alone it means untyped JSON mode."""
        ...

    async def _admitted_send(
        self,
        label: CallLabel,
        *,
        model: str,
        messages: list[dict[str, str]],
        sent: object,
        max_tokens: int | None,
        send: Callable[[], Awaitable[_R]],
        billed: Callable[[_R], Billed | None],
    ) -> _R:
        """One provider round-trip. Retried only where the failed send billed nothing or the
        provider asked for a later one (429, 5xx, a connection never made); each retry is admitted
        on its own. ``billed`` reads what a reply used — ``None`` for a reply that reports none,
        which is charged its whole bound."""
        bound = send_bound(
            await rate_ceiling(model, self._provider), sent=sent, max_tokens=max_tokens
        )
        for attempt in itertools.count():
            wait: float | None = None
            with admitted(label, bound, model=model, provider=self._provider) as admission:
                reservation = await acquire_reservation(
                    self._rate_limiter, messages, max_tokens, self._provider_name
                )
                try:
                    reply = await send()
                except Exception as exc:
                    wait = _retry_wait(exc, attempt)
                    if not may_have_billed(_status(exc), exc):
                        admission.release()
                    if wait is None:
                        raise
                    logger.warning(
                        "%s: %s on %s (attempt %d/%d); waiting %.1fs",
                        self._provider_name,
                        type(exc).__name__,
                        label.node,
                        attempt + 1,
                        MAX_429_ATTEMPTS,
                        wait,
                    )
                else:
                    if (spent := billed(reply)) is None:
                        admission.charge_in_full()
                        return reply
                    admission.settle(spent)
                    if reservation is not None:
                        reservation.close(spent.usage.total)
                    return reply
            await wait_with_countdown(wait, f"{label.node} {self._provider_name}")
        raise AssertionError("unreachable — the loop returns or raises")


__all__ = ["Billed", "LLMClientBase", "send_bound"]
