"""Readers for the per-cycle Control-local flags under ``.runtime/``.

``pause.flag`` / ``stop.flag`` / ``spend_cap.json`` are the cooperative files
the command dispatcher writes and the runner polls (ADR-0001 § Control-local).
This is the single read surface for them, shared by the ``/runstate`` endpoint
and the ``/api/v1/live`` façade so the web reflects true run-control state
without each call site re-implementing the file reads.

``running`` is derived from ``dashboard.json`` freshness rather than a
dedicated heartbeat: the loop bumps that file on every sample / progress tick /
round boundary, so a healthy run stays fresh while a paused / stopped / idle
run goes stale — no new on-disk artifact needed.
"""

from __future__ import annotations

import json
import time
from pathlib import Path


def is_paused(runtime_dir: Path) -> bool:
    """``.runtime/pause.flag`` present — the loop holds at the next round boundary."""
    return (runtime_dir / "pause.flag").is_file()


def is_stop_requested(runtime_dir: Path) -> bool:
    """``.runtime/stop.flag`` present — the loop exits cleanly at the next check."""
    return (runtime_dir / "stop.flag").is_file()


def read_spend_cap(runtime_dir: Path) -> float | None:
    """Live USD cap from ``spend_cap.json::max_usd``; ``None`` when absent/unreadable."""
    path = runtime_dir / "spend_cap.json"
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("max_usd")
    except (OSError, json.JSONDecodeError):
        return None
    return float(value) if isinstance(value, int | float) else None


# dashboard.json untouched for longer than this ⇒ the run is treated as
# not-running. The loop bumps the file on every sample / progress tick / round
# boundary, so a healthy run stays well inside the window even across long
# backend calls. Shared by the /runstate endpoint and the cycle-list `running`
# flag so both judge liveness against the same threshold.
RUN_FRESH_S = 30.0


def is_running(dashboard_path: Path, *, fresh_s: float) -> bool:
    """True iff ``dashboard.json`` was written within ``fresh_s`` seconds.

    The loop writes it on every sample / progress tick / round boundary, so a
    live run stays inside the window even across long backend calls; a halted
    run's file goes stale.
    """
    try:
        return (time.time() - dashboard_path.stat().st_mtime) < fresh_s
    except OSError:
        return False


__all__ = ["is_paused", "is_running", "is_stop_requested", "read_spend_cap"]
