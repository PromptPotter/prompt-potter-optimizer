"""Rolling-window RPM/TPM throttle (blocks before sending when a cap we set would be exceeded; the
``chars//4`` estimate reconciles via the reservation's ``close()``) and :class:`Backpressure`, the
answer to a provider's 429 (``Retry-After``, RFC 7231 §7.1.3)."""

from __future__ import annotations

import asyncio
import logging
import random
import re
import sys
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

# Sends of one call after a 5xx or a connection never made. A 429 spends none of them: it is the
# provider's pushback, answered by :class:`Backpressure` rather than by a retry count.
MAX_SEND_ATTEMPTS: int = 5
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
    """Bind who is told about time this task spent queued behind the shared throttle. A measured
    cell binds it (``scoring/cell_envelope.py``) so its wall-clock envelope measures its OWN work."""
    _THROTTLE_STALL_SINK.set(sink)


@contextmanager
def throttle_stall_given_to(sink: Callable[[float], None]) -> Iterator[None]:
    """For the life of the block, also tell *sink* about time this task spends blocked — beside the
    sink an enclosing scope bound, which keeps hearing every second. Composes where a cell's
    envelope replaces (:func:`set_throttle_stall_sink`): a cell stalls beside siblings on one wall
    clock, so passing its stall up would credit the enclosing scope once per sibling, while a call
    awaited in sequence spends its scope's own time."""
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


# WHOSE share of the shared provider window this task draws on. Per-task like the two above, and
# for the same reason: one ``RateLimiter`` per provider serves every concurrent campaign on the box,
# so an attribute would credit the wrong account. Unset — the CLI, tests, a standalone install —
# reads as one tenant, which is what makes the single-operator deployment pay nothing for fairness.
_RATE_TENANT: ContextVar[str] = ContextVar("rate_limit_tenant", default="")


def set_rate_tenant(tenant: str) -> None:
    """Bind whose share of the shared window this run draws on — the ACCOUNT, not the campaign, so
    one user cannot take N shares by starting N campaigns. Bound at the same seam as the cycle
    ledger (``application/run_observers.py::build_run_observers``), so it covers every run on every
    entry point. **Not** the two one-shot check-in resolver turns, which draw on the default share:
    they are single calls bounded by their own per-user token bucket (``jobs/quota.py``), not a
    stream that could starve anyone."""
    _RATE_TENANT.set(tenant)


# The SAME stall seconds, accumulated across every task as a rolling window — the one readable
# saturation signal this module offers. It rides `report_throttle_stall` rather than a second
# ContextVar sink because a sink is per-task and an L4 cell already binds one (`runner/inner/
# spawn.py`); a second `set()` would shadow whichever bound last. Module-global on purpose: "is this
# box oversubscribed" is a question about the machine, not about a cycle. Locked because the reader
# runs in FastAPI's threadpool (via `JobRegistry.reserve`) while every writer runs on the event loop.
_STALL_WINDOW_S = 60.0
_stall_events: deque[tuple[float, float]] = deque()
_stall_lock = threading.Lock()


def _prune_stalls(now: float, window_s: float) -> None:
    cutoff = now - window_s
    while _stall_events and _stall_events[0][0] < cutoff:
        _stall_events.popleft()


def throttle_stall_seconds(window_s: float = _STALL_WINDOW_S) -> float:
    """Seconds every task TOGETHER spent blocked in the shared throttle over the trailing window.

    Deliberately a LAGGING signal: it rises only once the box is already oversubscribed, and says
    nothing about headroom before then. That is why its one consumer (``application/jobs/capacity``)
    may only ever LOWER a configured ceiling with it, never raise one."""
    now = time.monotonic()
    with _stall_lock:
        _prune_stalls(now, window_s)
        return sum(seconds for _, seconds in _stall_events)


def report_throttle_stall(seconds: float) -> None:
    """Tell the task's sink and the machine's rolling window about *seconds* this task spent held
    by a shared bound — this module's throttles, or a machine slot another run holds."""
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
    # Generic fallbacks — body specifies window but not tokens/requests. A hyphen joins them too:
    # OpenRouter names its free tier's daily bucket `free-models-per-day`.
    ("daily", re.compile(r"per[\s-]*day|daily\s*(?:limit|quota)", re.IGNORECASE)),
    ("hourly", re.compile(r"per[\s-]*hour|hourly\s*(?:limit|quota)", re.IGNORECASE)),
    ("per-minute", re.compile(r"per[\s-]*minute", re.IGNORECASE)),
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
    max_attempts: int,
) -> RateLimitWait | None:
    """The pause for one 429 retry, or ``None`` — the budget is spent, or the window is one no retry
    can outlast — on which the caller surfaces the original failure. For a best-effort caller that
    may drop what it sends (the Langfuse sink); a run's own sends answer a 429 with
    :class:`Backpressure` instead."""
    if attempt >= max_attempts - 1:
        return None
    scope = diagnose_rate_limit_scope(headers, body)
    # A per-day/per-hour quota does not reset inside a retry budget — surface it
    # as a fast 429 error (the operator's cue to switch provider) instead of a
    # silent multi-minute countdown. Per-minute/second windows still wait.
    if scope in _UNWAITABLE_SCOPES:
        return None
    wait = parse_retry_after(headers)
    if wait is not None and wait > 0:
        return RateLimitWait(seconds=wait + 1.0, scope=scope)
    # No usable header, which is NOT a refusal to be retried: a provider answers a throttled
    # upstream with "Please retry shortly" and no ``Retry-After`` at all. Reading that absence as
    # "give up" spent ZERO of the five attempts, and one such blip then voided a whole panel.
    # Back off by policy instead; an unwaitable quota already returned above.
    return RateLimitWait(seconds=_unheaded_backoff(attempt), scope=scope)


# Backoff for a 429 that named no window: 2s, 4s, 8s, 16s, each with up to 25% jitter. The LAST of
# ``max_attempts`` buys no pause — ``decide_429_wait`` returns ``None`` on it and the caller drops
# the send — and the cap binds only if the attempt budget grows.
_UNHEADED_BASE_S: float = 2.0
_UNHEADED_CAP_S: float = 16.0


def _unheaded_backoff(attempt: int) -> float:
    base = min(_UNHEADED_BASE_S * (2.0**attempt), _UNHEADED_CAP_S)
    return base + random.uniform(0.0, base * 0.25)


# The hold after a throttle that named no `Retry-After`, per consecutive throttled episode: 15s,
# 30s, 60s … up to five minutes, each with up to 25% jitter. Long on purpose — the sender waiting is
# a whole run, and on an episodic backend every probe starts a container.
_COOLDOWN_BASE_S: float = 15.0
_COOLDOWN_CAP_S: float = 300.0
# How long a provider may answer nothing but throttles before the run stops asking. Past it the cure
# is the operator's — their own key, another host — and no wait here can apply it for them.
_GIVE_UP_S: float = 1800.0
# How often a send queued on the cap looks for a free slot.
_SLOT_POLL_S: float = 0.25
# The provider's own sentence, most telling first. OpenRouter carries an upstream host's in
# `metadata.raw` — the model, the host and the remedy — where the envelope's `message` says only
# "Provider returned error"; a quota of its own it names in `message`.
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
    """One provider's pushback on one sender, answered once for every send it has out.

    A 429 asks the SENDER to slow down, so a per-call retry budget answers the wrong party: each
    call out ran its own clock, retried in step with the others, and gave up alone — as a hole the
    next cell met again, or as a row charging the candidate for the provider's load. Here the first
    throttle of an episode opens ONE cooldown every send waits out and halves how many may go at
    once, never below the one that probes. An unthrottled answer ends the episode, and the cap
    climbs back one send per such answer while it still binds. Only a send made since the latest
    cooldown opened speaks for the provider NOW: one already out when it pushed back was answered
    by that cooldown, and its success may predate the pushback. What no wait clears refuses the RUN
    (:class:`SendRefusedError`): a per-hour or per-day quota at once, anything else once the
    provider has answered nothing but throttles for ``_GIVE_UP_S``.

    ``on_change`` is told whenever what :meth:`reading` returns may have moved — the change happens
    inside a send, where nobody publishing it would otherwise look."""

    def __init__(self, sender: str) -> None:
        self.sender = sender
        self.on_change: Callable[[], None] | None = None
        self._out = 0
        self._queued = 0
        self._at_once: int | None = None
        # Cooldowns ever opened — a send's ticket is the count when it went out.
        self._opened = 0
        self._strikes = 0
        self._resumes_at = 0.0
        # The episode's clock, and its wall-clock twins for the operator.
        self._since: float | None = None
        self._since_epoch: float | None = None
        self._resumes_at_epoch: float | None = None
        self._detail = ""

    @asynccontextmanager
    async def send(self) -> AsyncIterator[int]:
        """Hold one send until the cooldown has run out and a slot under the cap is free, then yield
        its ticket for :meth:`throttled` / :meth:`eased`. The wait breaks on a pause, and is
        reported tick by tick as throttle stall, so a cell's wall-clock envelope gives it back
        rather than spending itself on the provider's queue."""
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
            return _SLOT_POLL_S
        return 0.0

    def throttled(self, ticket: int, *, headers: object | None, body: str) -> None:
        """A send came back throttled; called while it still holds its slot. A quota refuses the
        run whichever send met it; otherwise only a send made since the latest cooldown opened a
        new one — every other was already answered."""
        now = time.monotonic()
        detail = _provider_words(body)
        scope = diagnose_rate_limit_scope(headers, body)
        if scope in _UNWAITABLE_SCOPES:
            raise SendRefusedError(
                f"{self.sender} is throttled by a {scope} quota, which no wait outlasts: {detail}",
                category=ErrorCategory.PROVIDER_THROTTLED,
            )
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
        """A send came back answered; called while it still holds its slot. Made since the latest
        cooldown opened, it ends the episode: the provider has taken a send since pushing back."""
        if ticket != self._opened:
            return
        if self._since is not None:
            logger.warning("%s: the provider answers again", self.sender)
        self._since = self._since_epoch = self._resumes_at_epoch = None
        self._resumes_at = 0.0
        self._strikes = 0
        # The provider's words belong to the episode that opened them. A reading still renders
        # while sends sit queued on the cap, so keeping them here served a finished throttle's
        # message beside a null `since` — a live quota warning for a provider that is answering.
        self._detail = ""
        if self._at_once is not None and self._out >= self._at_once:
            self._at_once += 1
        self._changed()

    def reading(self) -> BackpressureReading | None:
        """``None`` unless the pushback is holding something — an episode running, or a send
        queued on the cap — so a served reading alone says sends are being held."""
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
        report_throttle_stall(time.monotonic() - started)


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
    tenant: str = ""


@dataclass(eq=False)
class _Waiter:
    """One caller queued for the window. ``eq=False`` so the list holds it by IDENTITY — two
    waiters of one tenant with the same estimate are different callers, and removing the wrong one
    would leave a caller waiting on a slot already handed out."""

    tenant: str
    tokens: int


# How long a waiter that fits the window but lost the pick sleeps before re-checking. Only ever
# paid while the window is contended, and only against calls that take seconds to minutes.
_YIELD_S = 0.05


@dataclass
class RateLimiter:
    """Rolling-window request/token limiter, **admitting tenants fairly rather than in arrival
    order**. `*_pinned` slots are caller-configured and never overwritten by `apply_discovered()`.
    `None` cap disables that axis.

    One bucket per provider, because the bucket models the provider's cap — a property of the KEY,
    not of our users — and dividing it per tenant cannot manufacture quota. What is divided is the
    ORDER into it: :meth:`_next_up` picks the least-served tenant, so a burst cannot push a
    neighbour behind it. That is deficit round-robin, and it is work-conserving: an idle tenant
    costs nothing and a lone tenant takes the fast path with no sleep at all.
    """

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

    async def acquire(self, estimated_tokens: int) -> _TokenReservation:
        """Block until the request fits RPM+TPM **and this tenant's turn comes**; reserves both.

        The lock guards state mutation only and is never held across a sleep. It used to be, which
        made ``asyncio.Lock``'s FIFO queue the scheduler: a tenant issuing ten calls put ten of them
        ahead of a neighbour's one, so the greedier account took the window and the quieter one
        waited behind all of it. Timed from OUTSIDE the lock either way — the queue is most of the
        stall under concurrency and is invisible from within it."""
        started = time.monotonic()
        waiter = _Waiter(tenant=_RATE_TENANT.get(), tokens=estimated_tokens)
        self._waiting.append(waiter)
        try:
            while True:
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
                # Whoever was picked wakes on its own sleep and takes the slot, so a lost pick
                # costs one short yield rather than a place at the back of a queue.
                await asyncio.sleep(wait if wait > 0 else _YIELD_S)
        finally:
            if waiter in self._waiting:
                self._waiting.remove(waiter)
            report_throttle_stall(time.monotonic() - started)

    def _served(self, tenant: str) -> float:
        """What *tenant* has already taken from the current window, in the axis that BINDS. Derived
        from the window entries themselves rather than a second ledger, so ``_prune`` keeps it
        honest for free and a tenant that goes quiet forfeits its deficit as its entries age out."""
        if self.tpm is not None:
            return float(sum(e.tokens for e in self._tokens if e.tenant == tenant))
        return float(sum(1 for _, t in self._requests if t == tenant))

    def _next_up(self, now: float) -> _Waiter | None:
        """Whose turn it is: the least-served tenant among the waiters that fit RIGHT NOW, earliest
        arrival breaking a tie (``min`` over an arrival-ordered list returns the first minimum)."""
        ready = [w for w in self._waiting if self._wait_needed(now, w.tokens) <= 0]
        if not ready:
            return None
        return min(ready, key=lambda w: self._served(w.tenant))

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
    "MAX_SEND_ATTEMPTS",
    "OPENAI_RPM_HEADER",
    "OPENAI_TPM_HEADER",
    "Backpressure",
    "RateLimiter",
    "acquire_reservation",
    "apply_discovered_caps",
    "build_rate_limiter",
    "decide_429_wait",
    "get_abort_check",
    "parse_retry_after",
    "raise_if_request_too_large",
    "report_throttle_stall",
    "set_abort_check",
    "set_rate_tenant",
    "set_throttle_stall_sink",
    "throttle_stall_given_to",
    "throttle_stall_seconds",
    "wait_with_countdown",
]
