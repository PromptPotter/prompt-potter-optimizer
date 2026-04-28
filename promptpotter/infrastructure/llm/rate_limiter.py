"""Client-side rolling-window RPM + TPM limiter for LLM providers.

Proactive throttle to match provider tier caps (e.g. Groq free tier
5 req/min + 8000 tokens/min). Prevents 429 bursts by blocking *before*
sending when either cap would be exceeded.

Token estimation uses a rough ``chars // 4`` approximation — sufficient
for gating decisions; ``record_actual()`` corrects the reservation with
the server's reported usage after the response.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field

from promptpotter.shared.errors import RequestTooLargeError


def estimate_tokens(messages: list[dict[str, str]], max_output: int | None) -> int:
    """Rough prompt + output token estimate (~4 chars/token).

    When ``max_output`` is ``None`` (no caller-side cap), only the input
    side is counted. The TPM pre-check loses its output reservation, but
    the provider's own 429 still surfaces if the actual response overshoots.
    """
    char_count = sum(len(m.get("content", "")) for m in messages)
    return char_count // 4 + (max_output or 0)


@dataclass
class RateLimiter:
    """Rolling-window request/token limiter.

    Attributes:
        rpm: Requests per window. ``None`` disables the request cap.
        tpm: Tokens per window. ``None`` disables the token cap.
        window_s: Window length in seconds (default 60).
        rpm_pinned: When True, ``apply_discovered()`` won't override ``rpm``
            (caller explicitly configured it via settings).
        tpm_pinned: Same for ``tpm``.
    """

    rpm: int | None = None
    tpm: int | None = None
    window_s: float = 60.0
    rpm_pinned: bool = False
    tpm_pinned: bool = False

    _requests: deque[float] = field(default_factory=deque)
    _tokens: deque[tuple[float, int]] = field(default_factory=deque)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def apply_discovered(self, rpm: int | None, tpm: int | None) -> None:
        """Populate caps from server-reported rate-limit headers.

        Pinned slots (explicit user config) are never overwritten. Unpinned
        slots latch to the first non-``None`` value we see and update if the
        server later reports a different cap (tier change mid-run).
        """
        if rpm is not None and not self.rpm_pinned:
            self.rpm = rpm
        if tpm is not None and not self.tpm_pinned:
            self.tpm = tpm

    async def acquire(self, estimated_tokens: int) -> None:
        """Block until sending ``estimated_tokens`` fits within RPM + TPM caps.

        Reserves an RPM slot and a TPM allocation on return. Callers should
        follow up with :meth:`record_actual` once the server reports actual
        usage so the reservation reflects reality.
        """
        async with self._lock:
            while True:
                now = time.monotonic()
                self._prune(now)
                wait = self._wait_needed(now, estimated_tokens)
                if wait <= 0:
                    self._requests.append(now)
                    self._tokens.append((now, estimated_tokens))
                    return
                await asyncio.sleep(wait)

    def record_actual(self, estimated: int, actual: int) -> None:
        """Correct the most recent reservation with the response's actual tokens."""
        if self._tokens and self._tokens[-1][1] == estimated:
            ts, _ = self._tokens[-1]
            self._tokens[-1] = (ts, actual)

    async def acquire_with_estimation(
        self,
        messages: list[dict[str, str]],
        max_output: int | None,
        *,
        provider_name: str,
    ) -> RateLimitReservation:
        """Estimate, fail-fast on over-cap, throttle, and return a closeable reservation.

        Bundles the three-step dance every LLM call needs: estimate prompt
        size, raise ``RequestTooLargeError`` if the configured TPM cap can
        never fit it, and block until the rolling window has room. The
        returned reservation must be ``close()``-d with the server's actual
        token count after the response so the rolling window stays accurate.
        """
        estimated = estimate_tokens(messages, max_output)
        if self.tpm is not None and estimated > self.tpm:
            raise RequestTooLargeError(
                provider_name=provider_name,
                limit=self.tpm,
                requested=estimated,
            )
        await self.acquire(estimated)
        return RateLimitReservation(estimated=estimated, limiter=self)

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_s
        while self._requests and self._requests[0] < cutoff:
            self._requests.popleft()
        while self._tokens and self._tokens[0][0] < cutoff:
            self._tokens.popleft()

    def _wait_needed(self, now: float, estimated_tokens: int) -> float:
        delays: list[float] = [0.0]
        if self.rpm is not None and len(self._requests) >= self.rpm:
            delays.append(self._requests[0] + self.window_s - now)
        if self.tpm is not None:
            current = sum(t for _, t in self._tokens)
            if current + estimated_tokens > self.tpm:
                needed = current + estimated_tokens - self.tpm
                shed = 0
                for ts, toks in self._tokens:
                    shed += toks
                    if shed >= needed:
                        delays.append(ts + self.window_s - now)
                        break
        return max(delays)


@dataclass(frozen=True)
class RateLimitReservation:
    """One outstanding throttle reservation — call ``close()`` after the response."""

    estimated: int
    limiter: RateLimiter

    def close(self, actual: int) -> None:
        """Reconcile the reservation with the server's actual token usage."""
        self.limiter.record_actual(self.estimated, actual)
