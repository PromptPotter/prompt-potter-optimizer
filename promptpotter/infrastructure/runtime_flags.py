"""Readers for the per-cycle Control-local flags + the single run-phase derivation.

``pause.flag`` / ``stop.flag`` / ``spend_cap.json`` are the cooperative files
the command dispatcher writes and the runner polls (ADR-0001 § Control-local).
This is the single read surface for them.

:func:`derive_run_phase` is the *one* place run-state is computed for the cycle
list and any non-live reader — there is no second "is it running?" derivation.
It composes lifecycle (terminal, from ``index.json::finished_at``) with the
control flags and a freshness fallback. Freshness (``is_running``) is no longer
the *definition* of running — the runner declares ``running`` / ``paused`` /
``stopping`` onto the ledger and the dashboard projection writes them — it
survives only as the signal that splits ``running`` from ``detached`` (an active
cycle whose producer vanished without a terminal record).
"""

from __future__ import annotations

import time
from pathlib import Path

from promptpotter.domain.phases import RunPhase
from promptpotter.infrastructure.store.base import read_json_tolerant


def is_paused(runtime_dir: Path) -> bool:
    """``.runtime/pause.flag`` present — the loop holds at the next round boundary."""
    return (runtime_dir / "pause.flag").is_file()


def is_stop_requested(runtime_dir: Path) -> bool:
    """``.runtime/stop.flag`` present — the loop exits cleanly at the next check."""
    return (runtime_dir / "stop.flag").is_file()


def is_checkin(runtime_dir: Path) -> bool:
    """``.runtime/checkin.flag`` present — the campaign is still authoring its origin
    (pre-loop), not running. Dropped at skeleton creation, cleared at Start when the
    campaign flips ``checkin`` → ``active``."""
    return (runtime_dir / "checkin.flag").is_file()


def clear_run_control_flags(runtime_dir: Path) -> None:
    """Drop any consumed ``stop.flag`` / ``pause.flag`` / ``skip.flag`` left by a prior run.

    A fresh launch through the runner seam IS the operator's intent to run, so it
    supersedes prior run-control intent. The flags are one-shot requests, not
    persistent state — once the loop that they targeted has exited they are stale,
    and a stale ``stop.flag`` would otherwise kill the very next resume on its first
    poll (a stopped cycle could never be resumed). Idempotent."""
    (runtime_dir / "stop.flag").unlink(missing_ok=True)
    (runtime_dir / "pause.flag").unlink(missing_ok=True)
    (runtime_dir / "skip.flag").unlink(missing_ok=True)


def read_spend_cap(runtime_dir: Path) -> float | None:
    """Live USD cap from ``spend_cap.json::max_usd``; ``None`` when absent/unreadable."""
    data = read_json_tolerant(runtime_dir / "spend_cap.json")
    value = data.get("max_usd") if isinstance(data, dict) else None
    return float(value) if isinstance(value, int | float) else None


# dashboard.json untouched for longer than this ⇒ an active cycle's producer is
# treated as vanished (detached). The loop bumps the file on every sample /
# progress tick / round boundary, so a healthy run stays well inside the window
# even across long backend calls. This is the sole remaining use of freshness —
# it splits running from detached, it does not define "running".
RUN_FRESH_S = 30.0


def _producer_fresh(dashboard_path: Path, *, fresh_s: float) -> bool:
    """True iff ``dashboard.json`` was written within ``fresh_s`` seconds."""
    try:
        return (time.time() - dashboard_path.stat().st_mtime) < fresh_s
    except OSError:
        return False


def derive_run_phase(
    cycle_dir: Path, *, is_terminal: bool, fresh_s: float = RUN_FRESH_S
) -> RunPhase:
    """The single run-phase derivation for the cycle list + non-live readers.

    Composes lifecycle (``is_terminal`` — from ``index.json::finished_at``) with
    the control flags + a freshness fallback, in priority order:

    0. checkin  — ``checkin.flag`` present (pre-loop origin authoring). Wins over
       every other branch: a check-in cycle has no ``dashboard.json`` and no
       ``finished_at``, so without this it would derive ``detached``.
    1. terminal — the cycle finished (a terminal record / ``finished_at`` exists).
    2. stopping — ``stop.flag`` present (terminal-intent wins over a pause).
    3. paused   — ``pause.flag`` present (reversible).
    4. running  — producer fresh.
    5. detached — active but the producer stopped writing (died without a
       terminal record). The only branch that consults freshness.

    The live single-cycle view does *not* call this — it reads
    ``dashboard.json::run_phase`` (declared by the runner) and overlays its own
    connection-freshness for ``detached``.
    """
    runtime = cycle_dir / ".runtime"
    if is_checkin(runtime):
        return RunPhase.CHECKIN
    if is_terminal:
        return RunPhase.TERMINAL
    if is_stop_requested(runtime):
        return RunPhase.STOPPING
    if is_paused(runtime):
        return RunPhase.PAUSED
    if _producer_fresh(cycle_dir / "dashboard.json", fresh_s=fresh_s):
        return RunPhase.RUNNING
    return RunPhase.DETACHED


__all__ = [
    "RUN_FRESH_S",
    "clear_run_control_flags",
    "derive_run_phase",
    "is_checkin",
    "is_paused",
    "is_stop_requested",
    "read_spend_cap",
]
