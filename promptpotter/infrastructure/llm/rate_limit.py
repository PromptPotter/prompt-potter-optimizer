"""Rolling-window RPM/TPM throttle + 429 ``Retry-After`` handling (RFC 7231 §7.1.3). Blocks before
sending when a cap would be exceeded; the ``chars//4`` estimate reconciles via the reservation's
``close()``."""

from __future__ import annotations

import asyncio
import logging
import re
import sys
import time
from collections import deque
from collections.abc import Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from promptpotter.shared.errors import RequestTooLargeError

logger = logging.getLogger(__name__)

MAX_429_ATTEMPTS: int = 5
_YELLOW = "\033[93m"
_RESET = "\033[0m"

# Cooperative-abort predicate the countdown waits poll. Per-asyncio-task
# (ContextVar) so concurrent cycles in one process — the API server runs each
# run as its own task — never cross predicates, and a task that ends drops its
# copy (no reset needed). Bound once at the runner seam to ``session.pause_check``
# (reads ``.runtime/pause.flag``). ``None`` (the default — CLI/tests) leaves the
# wait a plain sleep, broken only by a propagating ``KeyboardInterrupt``.
_ABORT_CHECK: ContextVar[Callable[[], bool] | None] = ContextVar(
    "rate_limit_abort_check", default=None
)

# 429 windows that a retry budget cannot wait out — a per-day / per-hour bucket
# does not clear inside a multi-minute Retry-After. Blocking on these burns
# wall-clock on a quota that needs a provider switch, not patience.
_UNWAITABLE_SCOPES: frozenset[str] = frozenset({"TPD", "RPD", "TPH", "RPH", "daily", "hourly"})


# Time this task spent BLOCKED in the shared throttle. Per-task because the limiter is
# process-global: an attribute would credit a stall to whichever cycle read it. ``None`` discards.
_THROTTLE_STALL_SINK: ContextVar[Callable[[float], None] | None] = ContextVar(
    "rate_limit_throttle_stall_sink", default=None
)


def set_throttle_stall_sink(sink: Callable[[float], None] | None) -> None:
    """Bind who is told about time this task spent queued behind the shared throttle. An L4 cell
    binds it (``runner/inner/spawn.py``) so its wall-clock deadline measures its OWN work."""
    _THROTTLE_STALL_SINK.set(sink)


def _report_throttle_stall(seconds: float) -> None:
    if seconds <= 0:
        return
    sink = _THROTTLE_STALL_SINK.get()
    if sink is None:
        return
    try:
        sink(seconds)
    except Exception:  # pragma: no cover - a reporting path may not break the run
        logger.debug("throttle-stall sink raised; continuing", exc_info=True)


def set_abort_check(predicate: Callable[[], bool] | None) -> None:
    """Bind the cooperative-abort predicate :func:`wait_with_countdown` polls. The pause button writes
    ``.runtime/pause.flag`` CROSS-PROCESS, so a wait breaks even when a hosting uvicorn eats SIGINT."""
    _ABORT_CHECK.set(predicate)


def get_abort_check() -> Callable[[], bool] | None:
    """The abort predicate bound in THIS task's context, so a nested run composes rather than overwrites.
    An L4 inner cycle is a child task, so reading it is how the inner learns its owner was stopped."""
    return _ABORT_CHECK.get()


# Standard OpenAI/Groq rate-limit header keys.
OPENAI_RPM_HEADER = "x-ratelimit-limit-requests"
OPENAI_TPM_HEADER = "x-ratelimit-limit-tokens"
# Anthropic uses its own header naming.
ANTHROPIC_RPM_HEADER = "anthropic-ratelimit-requests-limit"
ANTHROPIC_TPM_HEADER = "anthropic-ratelimit-tokens-limit"


def parse_retry_after(headers: object | None) -> float | None:
    """RFC 7231 §7.1.3 — read ``Retry-After`` (seconds) from response headers."""
    if headers is None:
        return None
    for key in ("Retry-After", "retry-after"):
        val = headers.get(key) if hasattr(headers, "get") else None
        if val is None:
            continue
        try:
            return float(val)
        except (TypeError, ValueError):
            continue
    return None


# 429 body scopes — check longest window first ("per day" before "per minute") so a multi-window
# body classifies as the bucket actually blocked on.
_SCOPE_BODY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("TPD", re.compile(r"tokens?\s*per\s*day|TPD\b", re.IGNORECASE)),
    ("RPD", re.compile(r"requests?\s*per\s*day|RPD\b", re.IGNORECASE)),
    ("TPH", re.compile(r"tokens?\s*per\s*hour|TPH\b", re.IGNORECASE)),
    ("RPH", re.compile(r"requests?\s*per\s*hour|RPH\b", re.IGNORECASE)),
    ("TPM", re.compile(r"tokens?\s*per\s*minute|TPM\b", re.IGNORECASE)),
    ("RPM", re.compile(r"requests?\s*per\s*minute|RPM\b", re.IGNORECASE)),
    # Generic fallbacks — body specifies window but not tokens/requests.
    ("daily", re.compile(r"per\s*day|daily\s*(?:limit|quota)", re.IGNORECASE)),
    ("hourly", re.compile(r"per\s*hour|hourly\s*(?:limit|quota)", re.IGNORECASE)),
    ("per-minute", re.compile(r"per\s*minute", re.IGNORECASE)),
]


_DURATION_RE = re.compile(
    r"^(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m(?!s))?(?:(\d+(?:\.\d+)?)s)?$",
    re.IGNORECASE,
)


def _parse_duration_seconds(s: str) -> float | None:
    """Parse a Groq-style ``"1h2m3.5s"`` / ``"45s"`` / ``"30"`` duration into seconds."""
    s = s.strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        pass
    m = _DURATION_RE.match(s)
    if not m or not any(m.groups()):
        return None
    h, mn, sc = m.groups()
    return float(h or 0) * 3600 + float(mn or 0) * 60 + float(sc or 0)


def diagnose_rate_limit_scope(headers: object | None, body: str | None) -> str:
    """Classify the 429 window (TPD/RPD/TPH/RPH/TPM/RPM/…). Reads body first (Groq/OpenAI name
    the bucket), then `x-ratelimit-reset-*` magnitude. Returns `"429"` on no signal.
    """
    if body:
        for label, pat in _SCOPE_BODY_PATTERNS:
            if pat.search(body):
                return label
    for kind, hdr in (
        ("R", "x-ratelimit-reset-requests"),
        ("T", "x-ratelimit-reset-tokens"),
    ):
        val = headers.get(hdr) if headers is not None and hasattr(headers, "get") else None
        if not val:
            continue
        sec = _parse_duration_seconds(str(val))
        if sec is None:
            continue
        if sec > 3600:
            return f"{kind}PD"
        if sec > 60:
            return f"{kind}PH"
        return f"{kind}PM"
    return "429"


@dataclass(frozen=True)
class RateLimitWait:
    """A decided 429 retry pause. ``seconds`` already carries the +1s settle cushion."""

    seconds: float
    scope: str


def decide_429_wait(
    headers: object | None,
    body: str | None,
    attempt: int,
    *,
    max_attempts: int = MAX_429_ATTEMPTS,
) -> RateLimitWait | None:
    """The pause for one 429 retry, or ``None`` (no usable ``Retry-After``, non-positive, or exhausted) —
    on which the caller surfaces the original failure. Shared by the backend wire and the optimizer."""
    wait = parse_retry_after(headers)
    if wait is None or wait <= 0 or attempt >= max_attempts - 1:
        return None
    scope = diagnose_rate_limit_scope(headers, body)
    # A per-day/per-hour quota does not reset inside a retry budget — surface it
    # as a fast 429 error (the operator's cue to switch provider) instead of a
    # silent multi-minute countdown. Per-minute/second windows still wait.
    if scope in _UNWAITABLE_SCOPES:
        return None
    return RateLimitWait(seconds=wait + 1.0, scope=scope)


async def wait_with_countdown(total_sec: float, label: str) -> None:
    """Sleep while emitting a countdown to stderr. Cooperatively abortable: a requested pause raises
    ``asyncio.CancelledError`` so the loop unwinds through its existing interrupt path."""
    abort = _ABORT_CHECK.get()
    started = time.monotonic()
    end = started + total_sec
    # Same give-back as `acquire`: under concurrency the traffic that filled the window is largely
    # the neighbours'. Reported on EVERY exit, abort included — waiting is not working.
    try:
        while True:
            if abort is not None and abort():
                sys.stderr.write(f"\r{_YELLOW}⚠ rate-limit ({label}): aborted.{' ' * 30}{_RESET}\n")
                sys.stderr.flush()
                raise asyncio.CancelledError(f"rate-limit wait aborted ({label})")
            remaining = max(0.0, end - time.monotonic())
            mins_total, secs = divmod(int(remaining + 0.5), 60)
            hours, mins = divmod(mins_total, 60)
            stamp = f"{hours:d}:{mins:02d}:{secs:02d}" if hours else f"{mins:02d}:{secs:02d}"
            hint = "Ctrl+C / stop to abort" if abort is not None else "Ctrl+C to abort"
            sys.stderr.write(
                f"\r{_YELLOW}⚠ rate-limit ({label}): waiting {stamp}  ({hint}){_RESET}"
            )
            sys.stderr.flush()
            if remaining <= 0:
                break
            await asyncio.sleep(min(1.0, remaining))
        sys.stderr.write(f"\r{_YELLOW}⚠ rate-limit ({label}): resuming.{' ' * 30}{_RESET}\n")
        sys.stderr.flush()
    finally:
        _report_throttle_stall(time.monotonic() - started)


def estimate_tokens(messages: list[dict[str, str]], max_output: int | None) -> int:
    """Rough prompt+output estimate (~4 chars/token). `max_output=None` skips the output reservation
    (TPM precheck loses it; provider's own 429 still surfaces on overshoot).
    """
    char_count = sum(len(m.get("content", "")) for m in messages)
    return char_count // 4 + (max_output or 0)


def _parse_tpm_overflow(err_str: str) -> tuple[int, int] | None:
    m = re.search(r"Limit\s+(\d+),\s*Requested\s+(\d+)", err_str)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def _parse_int_header(headers: Mapping[str, Any] | Any, key: str) -> int | None:
    if headers is None:
        return None
    val = headers.get(key) if hasattr(headers, "get") else None
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def raise_if_request_too_large(exc: Exception, provider_name: str) -> None:
    status = getattr(exc, "status_code", None)
    if status not in (413, 429):
        return
    parsed = _parse_tpm_overflow(str(exc))
    if parsed is None:
        return
    limit, requested = parsed
    if requested <= limit:
        return
    raise RequestTooLargeError(
        provider_name=provider_name,
        limit=limit,
        requested=requested,
    ) from exc


async def acquire_reservation(
    rate_limiter: RateLimiter | None,
    messages: list[dict[str, str]],
    max_tokens: int | None,
    provider_name: str,
) -> RateLimitReservation | None:
    """Block until ``messages`` fits the rolling window; returns ``None`` when no limiter is set."""
    if rate_limiter is None:
        return None
    return await rate_limiter.acquire_with_estimation(
        messages, max_tokens, provider_name=provider_name
    )


def apply_discovered_caps(
    rate_limiter: RateLimiter | None,
    headers: object | None,
    *,
    rpm_header: str,
    tpm_header: str,
) -> None:
    if rate_limiter is None:
        return
    rpm = _parse_int_header(headers, rpm_header)
    tpm = _parse_int_header(headers, tpm_header)
    rate_limiter.apply_discovered(rpm, tpm)


@dataclass
class _TokenReservation:
    """One entry in the rolling token window, MUTABLE so its own caller reconciles estimate against
    actual by IDENTITY — with calls outstanding concurrently, the last entry is someone else's."""

    ts: float
    tokens: int


@dataclass
class RateLimiter:
    """Rolling-window request/token limiter. `*_pinned` slots are caller-configured and never
    overwritten by `apply_discovered()`. `None` cap disables that axis.
    """

    rpm: int | None = None
    tpm: int | None = None
    window_s: float = 60.0
    rpm_pinned: bool = False
    tpm_pinned: bool = False

    _requests: deque[float] = field(default_factory=deque)
    _tokens: deque[_TokenReservation] = field(default_factory=deque)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def apply_discovered(self, rpm: int | None, tpm: int | None) -> None:
        if rpm is not None and not self.rpm_pinned:
            self.rpm = rpm
        if tpm is not None and not self.tpm_pinned:
            self.tpm = tpm

    async def acquire(self, estimated_tokens: int) -> _TokenReservation:
        """Block until the request fits RPM+TPM; reserves both. Timed from OUTSIDE ``_lock``, which
        is held across the sleep — the convoy behind a throttled caller is most of the stall under
        concurrency and is invisible from within it."""
        started = time.monotonic()
        try:
            async with self._lock:
                while True:
                    now = time.monotonic()
                    self._prune(now)
                    wait = self._wait_needed(now, estimated_tokens)
                    if wait <= 0:
                        self._requests.append(now)
                        entry = _TokenReservation(ts=now, tokens=estimated_tokens)
                        self._tokens.append(entry)
                        return entry
                    await asyncio.sleep(wait)
        finally:
            _report_throttle_stall(time.monotonic() - started)

    async def acquire_with_estimation(
        self,
        messages: list[dict[str, str]],
        max_output: int | None,
        *,
        provider_name: str,
    ) -> RateLimitReservation:
        """Estimate → fail-fast on over-cap → throttle → reservation. Must ``close()`` with actual tokens."""
        estimated = estimate_tokens(messages, max_output)
        if self.tpm is not None and estimated > self.tpm:
            raise RequestTooLargeError(
                provider_name=provider_name,
                limit=self.tpm,
                requested=estimated,
            )
        entry = await self.acquire(estimated)
        return RateLimitReservation(entry=entry)

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_s
        while self._requests and self._requests[0] < cutoff:
            self._requests.popleft()
        while self._tokens and self._tokens[0].ts < cutoff:
            self._tokens.popleft()

    def _rpm_wait(self, now: float) -> float:
        if self.rpm is None or len(self._requests) < self.rpm:
            return 0.0
        return self._requests[0] + self.window_s - now

    def _tpm_wait(self, now: float, estimated_tokens: int) -> float:
        if self.tpm is None:
            return 0.0
        current = sum(t.tokens for t in self._tokens)
        if current + estimated_tokens <= self.tpm:
            return 0.0
        needed = current + estimated_tokens - self.tpm
        shed = 0
        for entry in self._tokens:
            shed += entry.tokens
            if shed >= needed:
                return entry.ts + self.window_s - now
        return 0.0

    def _wait_needed(self, now: float, estimated_tokens: int) -> float:
        return max(self._rpm_wait(now), self._tpm_wait(now, estimated_tokens))


@dataclass(frozen=True)
class RateLimitReservation:
    """One outstanding throttle reservation — call ``close()`` after the response."""

    entry: _TokenReservation

    def close(self, actual: int) -> None:
        self.entry.tokens = actual


def build_rate_limiter(rpm: int | None, tpm: int | None) -> RateLimiter:
    return RateLimiter(
        rpm=rpm,
        tpm=tpm,
        rpm_pinned=rpm is not None,
        tpm_pinned=tpm is not None,
    )


__all__ = [
    "ANTHROPIC_RPM_HEADER",
    "ANTHROPIC_TPM_HEADER",
    "MAX_429_ATTEMPTS",
    "OPENAI_RPM_HEADER",
    "OPENAI_TPM_HEADER",
    "RateLimiter",
    "acquire_reservation",
    "apply_discovered_caps",
    "build_rate_limiter",
    "decide_429_wait",
    "get_abort_check",
    "parse_retry_after",
    "raise_if_request_too_large",
    "set_abort_check",
    "set_throttle_stall_sink",
    "wait_with_countdown",
]
