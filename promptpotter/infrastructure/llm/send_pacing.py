from __future__ import annotations

import asyncio
import logging
import random
import re
import threading
import time
from collections import deque
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from promptpotter.domain.backend import BackpressureReading
from promptpotter.shared.errors import ErrorCategory, RequestTooLargeError, SendRefusedError

logger = logging.getLogger(__name__)

# The first send and each resend after a 5xx or a broken connection; a 429 spends none.
SEND_ATTEMPTS: int = 5
# Longer than a relay's own retries can run, so no reply is terminal.
CELL_WAIT_S: float = 600.0
HELD_POLL_S: float = 0.25


class SendBudget:
    def __init__(self, deadline_s: float | None, *, attempts: int) -> None:
        self.deadline_s = deadline_s
        self.attempts = attempts
        self.clock = None if deadline_s is None else asyncio.timeout(deadline_s)
        self.resent = 0
        self.given_back = 0.0

    @property
    def attempt(self) -> int:
        return self.resent + 1

    def resend_wait(self, *, base_s: float = 1.0) -> float | None:
        if self.attempt >= self.attempts:
            return None
        self.resent += 1
        return base_s * float(2 ** (self.resent - 1))

    def give_back(self, seconds: float) -> None:
        """The clock bounds the provider WORKING, never the wait for it."""
        when = None if self.clock is None else self.clock.when()
        if self.clock is None or when is None:
            return
        try:
            self.clock.reschedule(when + seconds)
        except RuntimeError:
            return
        self.given_back += seconds


_SEND_BUDGET: ContextVar[SendBudget | None] = ContextVar("send_budget", default=None)


@contextmanager
def under_budget(budget: SendBudget) -> Iterator[SendBudget]:
    token = _SEND_BUDGET.set(budget)
    try:
        yield budget
    finally:
        _SEND_BUDGET.reset(token)


def drawn_budget() -> SendBudget:
    """Never minted here: a default would hand every rung of a repair ladder its own attempts."""
    budget = _SEND_BUDGET.get()
    if budget is None:
        raise RuntimeError(
            "a send was made under no SendBudget — its unit of work (a cell, an optimizer call, "
            "a grading, a probe) opens one with `under_budget` before anything under it sends"
        )
    return budget


# The runner binds ``RunControl.pause_requested``; per task, so concurrent cycles never cross.
_ABORT_CHECK: ContextVar[Callable[[], bool] | None] = ContextVar("send_abort_check", default=None)

_UNWAITABLE_SCOPES: frozenset[str] = frozenset({"TPD", "RPD", "TPH", "RPH", "daily", "hourly"})


# Per task: the limiter is process-global, so an attribute would credit a stall to any cycle.
_THROTTLE_STALL_SINK: ContextVar[Callable[[float], None] | None] = ContextVar(
    "throttle_stall_sink", default=None
)


def set_throttle_stall_sink(sink: Callable[[float], None] | None) -> None:
    _THROTTLE_STALL_SINK.set(sink)


@contextmanager
def throttle_stall_given_to(sink: Callable[[float], None]) -> Iterator[None]:
    """COMPOSES with the enclosing sink; a cell replaces it, or siblings on one clock each bill it."""
    outer = _THROTTLE_STALL_SINK.get()

    def both(seconds: float) -> None:
        sink(seconds)
        if outer is not None:
            outer(seconds)

    token = _THROTTLE_STALL_SINK.set(both)
    try:
        yield
    finally:
        _THROTTLE_STALL_SINK.reset(token)


# Unbound is the default share, which the check-in resolver's turns draw on (`jobs/quota.py`).
_RATE_TENANT: ContextVar[str] = ContextVar("rate_tenant", default="")


def set_rate_tenant(tenant: str) -> None:
    """The ACCOUNT, never the campaign: N campaigns must not take one user N shares."""
    _RATE_TENANT.set(tenant)


# Locked: the reader runs in FastAPI's threadpool, every writer on the event loop.
_STALL_WINDOW_S = 60.0
_stall_events: deque[tuple[float, float]] = deque()
_stall_lock = threading.Lock()


def _prune_stalls(now: float, window_s: float) -> None:
    cutoff = now - window_s
    while _stall_events and _stall_events[0][0] < cutoff:
        _stall_events.popleft()


def throttle_stall_seconds(window_s: float = _STALL_WINDOW_S) -> float:
    """A LAGGING signal: ``jobs/capacity`` may only LOWER a ceiling with it, never raise one."""
    now = time.monotonic()
    with _stall_lock:
        _prune_stalls(now, window_s)
        return sum(seconds for _, seconds in _stall_events)


def report_throttle_stall(seconds: float) -> None:
    if seconds <= 0:
        return
    now = time.monotonic()
    with _stall_lock:
        _prune_stalls(now, _STALL_WINDOW_S)
        _stall_events.append((now, seconds))
    sink = _THROTTLE_STALL_SINK.get()
    if sink is None:
        return
    try:
        sink(seconds)
    except Exception:  # pragma: no cover - a reporting path may not break the run
        logger.debug("throttle-stall sink raised; continuing", exc_info=True)


def set_abort_check(predicate: Callable[[], bool] | None) -> None:
    _ABORT_CHECK.set(predicate)


def get_abort_check() -> Callable[[], bool] | None:
    return _ABORT_CHECK.get()


OPENAI_RPM_HEADER = "x-ratelimit-limit-requests"
OPENAI_TPM_HEADER = "x-ratelimit-limit-tokens"
ANTHROPIC_RPM_HEADER = "anthropic-ratelimit-requests-limit"
ANTHROPIC_TPM_HEADER = "anthropic-ratelimit-tokens-limit"


def parse_retry_after(headers: object | None) -> float | None:
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


# Longest window first, so a multi-window body classifies as the bucket actually blocked on.
_SCOPE_BODY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("TPD", re.compile(r"tokens?\s*per\s*day|TPD\b", re.IGNORECASE)),
    ("RPD", re.compile(r"requests?\s*per\s*day|RPD\b", re.IGNORECASE)),
    ("TPH", re.compile(r"tokens?\s*per\s*hour|TPH\b", re.IGNORECASE)),
    ("RPH", re.compile(r"requests?\s*per\s*hour|RPH\b", re.IGNORECASE)),
    ("TPM", re.compile(r"tokens?\s*per\s*minute|TPM\b", re.IGNORECASE)),
    ("RPM", re.compile(r"requests?\s*per\s*minute|RPM\b", re.IGNORECASE)),
    # `[\s-]`: OpenRouter names its free tier's daily bucket `free-models-per-day`.
    ("daily", re.compile(r"per[\s-]*day|daily\s*(?:limit|quota)", re.IGNORECASE)),
    ("hourly", re.compile(r"per[\s-]*hour|hourly\s*(?:limit|quota)", re.IGNORECASE)),
    ("per-minute", re.compile(r"per[\s-]*minute", re.IGNORECASE)),
]


_DURATION_RE = re.compile(
    r"^(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m(?!s))?(?:(\d+(?:\.\d+)?)s)?$",
    re.IGNORECASE,
)


def _parse_duration_seconds(s: str) -> float | None:
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


# Long on purpose: on an episodic backend every probe starts a container.
_COOLDOWN_BASE_S: float = 15.0
_COOLDOWN_CAP_S: float = 300.0
_GIVE_UP_S: float = 1800.0
# `raw` first: OpenRouter's `message` says only "Provider returned error" for an upstream host's.
_PROVIDER_SENTENCES = tuple(
    re.compile(rf'"{key}"\s*:\s*"((?:[^"\\]|\\.)*)"') for key in ("raw", "message")
)


def _cooldown_s(strikes: int) -> float:
    base = min(_COOLDOWN_BASE_S * 2.0 ** (strikes - 1), _COOLDOWN_CAP_S)
    return base + random.uniform(0.0, base * 0.25)


def _provider_words(body: str) -> str:
    said = next((m.group(1) for p in _PROVIDER_SENTENCES if (m := p.search(body))), body)
    return " ".join(said.split())[:300]


class Backpressure:
    """A 429 asks the SENDER to slow down, so a per-call retry budget answers the wrong party."""

    def __init__(self, sender: str) -> None:
        self.sender = sender
        self.on_change: Callable[[], None] | None = None
        self._out = 0
        self._queued = 0
        self._at_once: int | None = None
        self._opened = 0
        self._strikes = 0
        self._resumes_at = 0.0
        self._since: float | None = None
        self._since_epoch: float | None = None
        self._resumes_at_epoch: float | None = None
        self._detail = ""

    @asynccontextmanager
    async def send(self) -> AsyncIterator[int]:
        abort = _ABORT_CHECK.get()
        self._queued += 1
        try:
            if self._hold() > 0:
                self._changed()
            while (hold := self._hold()) > 0:
                if abort is not None and abort():
                    raise asyncio.CancelledError(f"backpressure wait aborted ({self.sender})")
                started = time.monotonic()
                await asyncio.sleep(min(1.0, hold))
                report_throttle_stall(time.monotonic() - started)
        finally:
            self._queued -= 1
        self._out += 1
        self._changed()
        try:
            yield self._opened
        finally:
            self._out -= 1

    def _hold(self) -> float:
        cooling = self._resumes_at - time.monotonic()
        if cooling > 0:
            return cooling
        if self._at_once is not None and self._out >= self._at_once:
            return HELD_POLL_S
        return 0.0

    def throttled(self, ticket: int, *, headers: object | None, body: str) -> None:
        now = time.monotonic()
        detail = _provider_words(body)
        scope = diagnose_rate_limit_scope(headers, body)
        if scope in _UNWAITABLE_SCOPES:
            raise SendRefusedError(
                f"{self.sender} is throttled by a {scope} quota, which no wait outlasts: {detail}",
                category=ErrorCategory.PROVIDER_THROTTLED,
            )
        # Only a send made since the latest cooldown opened speaks for the provider now.
        if ticket != self._opened:
            return
        if self._since is None:
            self._since, self._since_epoch = now, time.time()
        elif now - self._since >= _GIVE_UP_S:
            raise SendRefusedError(
                f"{self.sender} has answered nothing but throttles for "
                f"{(now - self._since) / 60:.0f} min: {detail}",
                category=ErrorCategory.PROVIDER_THROTTLED,
            )
        self._opened += 1
        self._strikes += 1
        self._at_once = max(1, self._out // 2)
        retry_after = parse_retry_after(headers)
        wait = retry_after + 1.0 if retry_after else _cooldown_s(self._strikes)
        self._resumes_at, self._resumes_at_epoch = now + wait, time.time() + wait
        self._detail = detail
        logger.warning(
            "%s: the provider is throttling (%s) — every send held %.0fs, then %d at once: %s",
            self.sender,
            scope,
            wait,
            self._at_once,
            detail,
        )
        self._changed()

    def eased(self, ticket: int) -> None:
        if ticket != self._opened:
            return
        if self._since is not None:
            logger.warning("%s: the provider answers again", self.sender)
        self._since = self._since_epoch = self._resumes_at_epoch = None
        self._resumes_at = 0.0
        self._strikes = 0
        # A reading still renders while sends queue on the cap; it must not carry a finished episode's.
        self._detail = ""
        if self._at_once is not None and self._out >= self._at_once:
            self._at_once += 1
        self._changed()

    def reading(self) -> BackpressureReading | None:
        if self._since_epoch is None and self._queued == 0:
            return None
        return BackpressureReading(
            sender=self.sender,
            at_once=self._at_once,
            since=self._since_epoch,
            resumes_at=self._resumes_at_epoch,
            detail=self._detail,
        )

    def _changed(self) -> None:
        if self.on_change is not None:
            self.on_change()


async def held_wait(total_sec: float, label: str) -> None:
    abort = _ABORT_CHECK.get()
    started = time.monotonic()
    end = started + total_sec
    # Reported on EVERY exit, abort included: waiting is not working.
    try:
        while (remaining := end - time.monotonic()) > 0:
            if abort is not None and abort():
                raise asyncio.CancelledError(f"resend wait aborted ({label})")
            await asyncio.sleep(min(1.0, remaining))
    finally:
        report_throttle_stall(time.monotonic() - started)


def estimate_tokens(messages: list[dict[str, str]], max_output: int | None) -> int:
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
    """Mutable, reconciled by IDENTITY: with calls out concurrently, the last entry is another's."""

    ts: float
    tokens: int
    tenant: str = ""


class WindowSlot:
    def __init__(self, entry: _TokenReservation | None) -> None:
        self._entry = entry
        self.left = False

    def leaves(self) -> None:
        self.left = True

    def billed(self, actual: int) -> None:
        if self._entry is not None:
            self._entry.tokens = actual


@asynccontextmanager
async def window_slot(
    rate_limiter: RateLimiter | None,
    messages: list[dict[str, str]],
    max_output: int | None,
    *,
    provider_name: str,
) -> AsyncIterator[WindowSlot]:
    entry = (
        None
        if rate_limiter is None
        else await rate_limiter._reserve(messages, max_output, provider_name=provider_name)
    )
    slot = WindowSlot(entry)
    try:
        yield slot
    finally:
        # A send that never left was counted by no provider; its estimate would shut the window.
        if rate_limiter is not None and entry is not None and not slot.left:
            rate_limiter._give_back(entry)


@dataclass(eq=False)
class _Waiter:
    """``eq=False``: the list holds it by IDENTITY — equal waiters are still different callers."""

    tenant: str
    tokens: int


_YIELD_S = 0.05


@dataclass
class RateLimiter:
    """One bucket per provider — the cap is the KEY's, not our users'; only the ORDER in is shared."""

    rpm: int | None = None
    tpm: int | None = None
    window_s: float = 60.0
    rpm_pinned: bool = False
    tpm_pinned: bool = False

    _requests: deque[tuple[float, str]] = field(default_factory=deque)
    _tokens: deque[_TokenReservation] = field(default_factory=deque)
    _waiting: list[_Waiter] = field(default_factory=list)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def apply_discovered(self, rpm: int | None, tpm: int | None) -> None:
        if rpm is not None and not self.rpm_pinned:
            self.rpm = rpm
        if tpm is not None and not self.tpm_pinned:
            self.tpm = tpm

    async def _acquire(self, estimated_tokens: int) -> _TokenReservation:
        started = time.monotonic()
        waiter = _Waiter(tenant=_RATE_TENANT.get(), tokens=estimated_tokens)
        self._waiting.append(waiter)
        try:
            while True:
                # Never held across the sleep: there, ``asyncio.Lock``'s FIFO becomes the scheduler.
                async with self._lock:
                    now = time.monotonic()
                    self._prune(now)
                    if self._next_up(now) is waiter:
                        self._waiting.remove(waiter)
                        self._requests.append((now, waiter.tenant))
                        entry = _TokenReservation(
                            ts=now, tokens=estimated_tokens, tenant=waiter.tenant
                        )
                        self._tokens.append(entry)
                        return entry
                    wait = self._wait_needed(now, estimated_tokens)
                await asyncio.sleep(wait if wait > 0 else _YIELD_S)
        finally:
            if waiter in self._waiting:
                self._waiting.remove(waiter)
            report_throttle_stall(time.monotonic() - started)

    def _served(self, tenant: str) -> float:
        if self.tpm is not None:
            return float(sum(e.tokens for e in self._tokens if e.tenant == tenant))
        return float(sum(1 for _, t in self._requests if t == tenant))

    def _next_up(self, now: float) -> _Waiter | None:
        ready = [w for w in self._waiting if self._wait_needed(now, w.tokens) <= 0]
        if not ready:
            return None
        # ``min`` returns the first minimum, so the earliest arrival breaks a tie.
        return min(ready, key=lambda w: self._served(w.tenant))

    async def _reserve(
        self,
        messages: list[dict[str, str]],
        max_output: int | None,
        *,
        provider_name: str,
    ) -> _TokenReservation:
        estimated = estimate_tokens(messages, max_output)
        if self.tpm is not None and estimated > self.tpm:
            raise RequestTooLargeError(
                provider_name=provider_name,
                limit=self.tpm,
                requested=estimated,
            )
        return await self._acquire(estimated)

    def _give_back(self, entry: _TokenReservation) -> None:
        self._tokens = deque(e for e in self._tokens if e is not entry)
        if (stamp := (entry.ts, entry.tenant)) in self._requests:
            self._requests.remove(stamp)

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_s
        while self._requests and self._requests[0][0] < cutoff:
            self._requests.popleft()
        while self._tokens and self._tokens[0].ts < cutoff:
            self._tokens.popleft()

    def _rpm_wait(self, now: float) -> float:
        if self.rpm is None or len(self._requests) < self.rpm:
            return 0.0
        return self._requests[0][0] + self.window_s - now

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
    "CELL_WAIT_S",
    "HELD_POLL_S",
    "OPENAI_RPM_HEADER",
    "OPENAI_TPM_HEADER",
    "SEND_ATTEMPTS",
    "Backpressure",
    "RateLimiter",
    "SendBudget",
    "apply_discovered_caps",
    "build_rate_limiter",
    "drawn_budget",
    "get_abort_check",
    "held_wait",
    "parse_retry_after",
    "raise_if_request_too_large",
    "report_throttle_stall",
    "set_abort_check",
    "set_rate_tenant",
    "set_throttle_stall_sink",
    "throttle_stall_given_to",
    "throttle_stall_seconds",
    "under_budget",
    "window_slot",
]
