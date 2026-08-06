"""Every UTC timestamp mints here so the format never drifts, and every wait that must survive a MACHINE SUSPEND times itself
here. ``Z`` suffix, not ``+00:00``. Leaf-level, so domain models may use it without reaching into infrastructure."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

__all__ = ["SUSPEND_GRACE_S", "iso_z", "sleep_measuring_suspend", "utcnow_iso"]


SUSPEND_GRACE_S = 60.0
"""Overshoot above which a sleep is read as a machine suspend rather than jitter.

Well above ordinary event-loop lateness (milliseconds) and above the 15 s heartbeat
interval, so a tick that merely ran late never reads as a suspend."""


def iso_z(dt: datetime) -> str:
    """*dt* as an RFC 3339 UTC string — the ONE place the ``Z`` spelling is applied to an instant the caller already has.
    Not a second helper: that case cannot go through :func:`utcnow_iso`, and was hand-formatting the offset instead."""
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def utcnow_iso() -> str:
    """Current UTC instant as an RFC 3339 string, e.g. ``2026-06-04T12:00:00Z``."""
    return iso_z(datetime.now(UTC))


async def sleep_measuring_suspend(seconds: float) -> float:
    """Sleep, returning how far WALL clock overshot — which IS the suspend duration. Measures ``time.time()`` deliberately:
    ``monotonic``'s behaviour across S3 is the platform-dependent thing the guard cannot rest on."""
    before = time.time()
    await asyncio.sleep(seconds)
    return max(0.0, (time.time() - before) - seconds)
