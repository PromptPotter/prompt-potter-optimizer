from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

__all__ = [
    "SUSPEND_GRACE_S",
    "duration_words",
    "epoch_seconds",
    "iso_z",
    "sleep_measuring_suspend",
    "utcnow_iso",
]


SUSPEND_GRACE_S = 60.0
"""Above the 15 s heartbeat interval, so a tick that merely ran late never reads as a suspend."""


def duration_words(seconds: float) -> str:
    """Must match the browser's ``fmtDuration``."""
    if seconds < 90:
        return f"{round(seconds)}s"
    minutes = round(seconds / 60)
    if minutes < 90:
        return f"{minutes}m"
    hours, rest = divmod(minutes, 60)
    return f"{hours}h" if rest == 0 else f"{hours}h {rest}m"


def iso_z(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def utcnow_iso() -> str:
    return iso_z(datetime.now(UTC))


def epoch_seconds(value: object) -> float | None:
    """Stamps sort by this, never as strings: ``utcnow_iso`` omits a zero fractional part."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    # On Windows an out-of-range datetime raises ``OSError``/``OverflowError``, not ``ValueError``.
    except (ValueError, OSError, OverflowError):
        return None


async def sleep_measuring_suspend(seconds: float) -> float:
    """``time.time()`` deliberately: ``monotonic`` across an S3 suspend is platform-dependent."""
    before = time.time()
    await asyncio.sleep(seconds)
    return max(0.0, (time.time() - before) - seconds)
