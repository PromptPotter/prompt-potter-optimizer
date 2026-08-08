"""Readers for the per-cycle Control-local flags (ADR-0001) and :func:`derive_run_phase`, the
ONE place run-state is computed for the cycle list and every other non-live reader."""

from __future__ import annotations

import time
from pathlib import Path

from promptpotter.domain.phases import RunPhase
from promptpotter.infrastructure.store.io import read_json_tolerant
from promptpotter.infrastructure.store.layout import CycleLayout


def is_paused(cycle_dir: Path) -> bool:
    """``.runtime/pause.flag`` present — the single operator-interrupt flag (there is no
    ``stop.flag``). The loop exits at the next checkpoint and the cycle stays resumable."""
    return CycleLayout(cycle_dir).pause_flag.is_file()


def is_checkin(cycle_dir: Path) -> bool:
    """``.runtime/checkin.flag`` present — the campaign is still authoring its origin, not
    running. Dropped at skeleton creation, cleared when Start flips ``checkin`` → ``active``."""
    return CycleLayout(cycle_dir).checkin_flag.is_file()


def is_sample_lookahead(cycle_dir: Path) -> bool:
    """``.runtime/sample_lookahead.flag`` present — the operator's REQUEST that the walk hold a second
    sample in flight. What the loop ran at is ``dashboard.json::sample_lookahead``; never serve
    this as that."""
    return CycleLayout(cycle_dir).sample_lookahead_flag.is_file()


def clear_run_control_flags(cycle_dir: Path) -> None:
    """Drop every POLLED run-control flag — a fresh launch IS the operator's intent to run at the
    engine's own cadence, and a flag surviving the gesture it answered re-answers the next one."""
    layout = CycleLayout(cycle_dir)
    layout.pause_flag.unlink(missing_ok=True)
    layout.skip_flag.unlink(missing_ok=True)
    layout.sample_lookahead_flag.unlink(missing_ok=True)


def read_spend_caps(cycle_dir: Path) -> tuple[float | None, int | None]:
    """Live ``(usd, tokens)`` ceilings, ``None`` per key when absent, unreadable or the wrong type.
    **The one place that knows this file's shape.**"""
    data = read_json_tolerant(CycleLayout(cycle_dir).spend_cap)
    if not isinstance(data, dict):
        return None, None
    usd = data.get("max_usd")
    tokens = data.get("max_tokens")
    return (
        float(usd) if isinstance(usd, int | float) and not isinstance(usd, bool) else None,
        int(tokens) if isinstance(tokens, int) and not isinstance(tokens, bool) else None,
    )


# dashboard.json untouched for longer than this ⇒ an active cycle's producer is
# treated as vanished (detached). The loop bumps the file on every sample /
# progress tick / round boundary, so a healthy run stays well inside the window
# even across long backend calls. This is the sole remaining use of freshness —
# it splits running from detached, it does not define "running".
RUN_FRESH_S = 30.0


def _producer_fresh(cycle_dir: Path, *, fresh_s: float) -> bool:
    """``dashboard.json`` is canonical whenever it exists — never averaged or maxed against
    ``index.json``, which is the fallback only until the first dashboard write lands."""
    layout = CycleLayout(cycle_dir)
    try:
        return (time.time() - layout.dashboard.stat().st_mtime) < fresh_s
    except OSError:
        pass
    try:
        return (time.time() - layout.manifest.stat().st_mtime) < fresh_s
    except OSError:
        return False


def _declared_phase(cycle_dir: Path) -> str:
    """``paused`` has TWO writers: the operator's flag, and the runner declaring it (a Ctrl+C out of
    a long phase writes no flag). Consulted for that and ``gate`` only; the rest is derived."""
    data = read_json_tolerant(CycleLayout(cycle_dir).dashboard)
    return str(data.get("run_phase", "")) if isinstance(data, dict) else ""


def derive_run_phase(
    cycle_dir: Path, *, is_terminal: bool, fresh_s: float = RUN_FRESH_S
) -> RunPhase:
    """The single run-phase derivation for the cycle list + non-live readers. ``paused`` is
    deliberately NOT freshness-gated — a paused producer has exited — while ``gate`` is."""
    if is_checkin(cycle_dir):
        return RunPhase.CHECKIN
    if is_terminal:
        return RunPhase.TERMINAL
    declared = _declared_phase(cycle_dir)
    if is_paused(cycle_dir) or declared == RunPhase.PAUSED:
        return RunPhase.PAUSED
    fresh = _producer_fresh(cycle_dir, fresh_s=fresh_s)
    if fresh and declared == RunPhase.GATE:
        return RunPhase.GATE
    if fresh:
        return RunPhase.RUNNING
    return RunPhase.DETACHED


__all__ = [
    "RUN_FRESH_S",
    "clear_run_control_flags",
    "derive_run_phase",
    "is_checkin",
    "is_paused",
    "read_spend_caps",
]
