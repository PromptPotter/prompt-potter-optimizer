"""Shared 429 Retry-After parsing + visible countdown.

Used by both the optimizer LLM call path (``application/optimization/pipeline.py
::llm_call``) and the backend client (``infrastructure/backend/client.py
::BackendClient.run_query``). Providers differ in where they stash the hint —
OpenAI/Anthropic SDK raise exceptions with ``.response.headers['Retry-After']``;
Groq sometimes only spells it in the error body (``"try again in 11m21.264s"``).
TermNorm forwards Groq's response verbatim. ``parse_retry_after`` tries headers
first, then body regex; returns ``None`` when neither is parseable so callers
can fall through to their normal error path rather than hang.
"""

from __future__ import annotations

import asyncio
import re
import sys
import time

__all__ = ["parse_retry_after", "wait_with_countdown"]

_RETRY_AFTER_REGEX = re.compile(r"try again in (?:(\d+)m)?\s*(\d+(?:\.\d+)?)\s*s")
_YELLOW = "\033[93m"
_RESET = "\033[0m"


def parse_retry_after(headers: object | None, body: str) -> float | None:
    """Extract retry-after seconds from response headers (preferred) or body text."""
    if headers is not None:
        for key in ("Retry-After", "retry-after"):
            val = headers.get(key) if hasattr(headers, "get") else None
            if val is None:
                continue
            try:
                return float(val)
            except (TypeError, ValueError):
                continue
    m = _RETRY_AFTER_REGEX.search(body)
    if m:
        minutes = int(m.group(1) or 0)
        seconds = float(m.group(2))
        return minutes * 60 + seconds
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
