"""The wall-clock helpers. Every UTC timestamp string in the codebase mints here so
the format never drifts, and every wait that must survive a MACHINE SUSPEND times
itself here so the detection rule is written once.

``Z`` suffix (RFC 3339 canonical UTC), not ``+00:00`` — unambiguous and what
the JSON wire + observability surfaces expect. Importable from any layer:
``shared/`` is leaf-level, so domain models may use it without reaching into
infrastructure.
"""

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
    """*dt* as an RFC 3339 UTC string. The ONE place the ``Z`` spelling is applied.

    Not a second helper — the same rule, for an instant the caller already has (a file
    mtime, a parsed record). Split out because that case cannot go through
    :func:`utcnow_iso` and so was hand-formatting ``+00:00`` onto the wire instead.
    """
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def utcnow_iso() -> str:
    """Current UTC instant as an RFC 3339 string, e.g. ``2026-06-04T12:00:00Z``."""
    return iso_z(datetime.now(UTC))


async def sleep_measuring_suspend(seconds: float) -> float:
    """Sleep *seconds*; return how far WALL clock overshot what was asked for.

    An OS suspend freezes the event loop along with everything else, so nothing runs
    to distinguish "still asleep" from "just died" except elapsed wall time. The
    overshoot IS the suspend duration, and it is the one signal available whichever
    way the platform's monotonic clock behaves across S3: if monotonic advances
    through the suspend the timer expires mid-sleep and fires at wake; if it freezes,
    the remainder runs after wake. Either way the wall overshoots.

    So this measures ``time.time()``, deliberately — never ``time.monotonic()``,
    whose behaviour across a suspend is the very thing that differs by platform and
    therefore cannot be the thing the guard rests on.
    """
    before = time.time()
    await asyncio.sleep(seconds)
    return max(0.0, (time.time() - before) - seconds)
