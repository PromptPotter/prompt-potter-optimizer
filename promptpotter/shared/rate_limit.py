"""Shared 429 ``Retry-After`` parsing + visible countdown.

RFC 7231 §7.1.3: the server tells the client when to come back via the
``Retry-After`` header; the client honors it with a bounded retry count.
TermNorm normalizes Groq's body-only ``"try again in Xm Ys"`` hint into a
proper header at its boundary, so this module never parses bodies.
"""

from __future__ import annotations

import asyncio
import sys
import time

__all__ = ["MAX_429_ATTEMPTS", "parse_retry_after", "wait_with_countdown"]

MAX_429_ATTEMPTS: int = 5
_YELLOW = "\033[93m"
_RESET = "\033[0m"


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


async def wait_with_countdown(total_sec: float, label: str) -> None:
    """Sleep `total_sec` while emitting a yellow single-line countdown to stderr."""
    end = time.monotonic() + total_sec
    while True:
        remaining = max(0.0, end - time.monotonic())
        mins_total, secs = divmod(int(remaining + 0.5), 60)
        hours, mins = divmod(mins_total, 60)
        stamp = f"{hours:d}:{mins:02d}:{secs:02d}" if hours else f"{mins:02d}:{secs:02d}"
        sys.stderr.write(
            f"\r{_YELLOW}⚠ rate-limit ({label}): waiting {stamp}  (Ctrl+C to abort){_RESET}"
        )
        sys.stderr.flush()
        if remaining <= 0:
            break
        await asyncio.sleep(min(1.0, remaining))
    sys.stderr.write(f"\r{_YELLOW}⚠ rate-limit ({label}): resuming.{' ' * 30}{_RESET}\n")
    sys.stderr.flush()
