"""Readers for the per-cycle Control-local flags + the single run-phase derivation.

``pause.flag`` / ``spend_cap.json`` are the cooperative files the command
dispatcher writes and the runner polls (ADR-0001 § Control-local). This is the
single read surface for them.

:func:`derive_run_phase` is the *one* place run-state is computed for the cycle
list and any non-live reader — there is no second "is it running?" derivation.
It composes lifecycle (terminal, from ``index.json::finished_at``) with the
control flags and a freshness fallback. Freshness (``is_running``) is not the
*definition* of running — the runner declares ``running`` / ``paused`` onto
the ledger and the dashboard projection writes them — it is only the signal
that splits ``running`` from ``detached`` (an active cycle whose producer
vanished without a terminal record).
"""

from __future__ import annotations

import time
from pathlib import Path

from promptpotter.domain.phases import RunPhase
from promptpotter.infrastructure.store.io import read_json_tolerant
from promptpotter.infrastructure.store.layout import CycleLayout


def is_paused(cycle_dir: Path) -> bool:
    """``.runtime/pause.flag`` present — the operator paused; the loop exits
    cleanly at the next checkpoint and the cycle stays resumable (non-terminal).
    The single operator-interrupt flag (no separate ``stop.flag``)."""
    return CycleLayout(cycle_dir).pause_flag.is_file()


def is_checkin(cycle_dir: Path) -> bool:
    """``.runtime/checkin.flag`` present — the campaign is still authoring its origin
    (pre-loop), not running. Dropped at skeleton creation, cleared at Start when the
    campaign flips ``checkin`` → ``active``."""
    return CycleLayout(cycle_dir).checkin_flag.is_file()


def clear_run_control_flags(cycle_dir: Path) -> None:
    """Drop any consumed ``pause.flag`` / ``skip.flag`` left by a prior run.

    A fresh launch through the runner seam IS the operator's intent to run, so it
    supersedes prior run-control intent. The flags are one-shot requests, not
    persistent state — once the loop that they targeted has exited they are stale,
    and a stale ``pause.flag`` would otherwise pause the very next resume on its
    first poll (a paused cycle could never be resumed). Idempotent."""
    layout = CycleLayout(cycle_dir)
    layout.pause_flag.unlink(missing_ok=True)
    layout.skip_flag.unlink(missing_ok=True)


def read_spend_caps(cycle_dir: Path) -> tuple[float | None, int | None]:
    """Live ``(usd, tokens)`` ceilings from ``spend_cap.json``; ``None`` per key when
    absent / unreadable / the wrong type.

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
    """True iff the producer's heartbeat surface was written within
    ``fresh_s`` seconds.

    ``dashboard.json`` is canonical whenever it exists — never averaged or
    maxed against ``index.json``. Only when it is entirely ABSENT (the
    dashboard-less window between mint and the first round's write) does this
    fall back to ``index.json``'s mtime, so a just-minted cycle reads
    ``running`` instead of ``detached`` before its first dashboard write
    lands."""
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
    """The phase the RUNNER declared, read off ``dashboard.json::run_phase``.

    ``gate`` has no flag because only the runner can know it. ``paused`` has TWO
    writers: the OPERATOR asks via ``.runtime/pause.flag``, and the runner declares
    it — both when it honours that flag at a checkpoint and when a Ctrl+C unwinds it
    out of a long phase, which writes no flag at all. Consulted for those two phases
    (see :func:`derive_run_phase`), never as a general second opinion: everything
    else here is derived, and reading a declared value where a derived one exists is
    how two vocabularies start disagreeing.
    """
    data = read_json_tolerant(CycleLayout(cycle_dir).dashboard)
    return str(data.get("run_phase", "")) if isinstance(data, dict) else ""


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
    2. paused   — ``pause.flag`` present OR the runner declared ``paused``
       (operator interrupt; resumable). Both writers count: a Ctrl+C out of a
       long phase declares without ever writing the flag, and reading only the
       flag stamped those cycles ``detached`` and let the reaper terminate a
       run that had shut down cleanly. Deliberately NOT freshness-gated — the
       exact opposite of ``gate`` below, because a paused producer has EXITED,
       so requiring freshness would read every pause as ``detached``.
    3. gate     — fresh AND the runner declared ``gate`` (held at the round-0
       origin gate, awaiting an operator decision; ``gate`` is declared, never
       flagged). Freshness is required so a cycle that DIED while gated still
       derives ``detached`` and is reapable; without that this would pin a dead
       cycle at ``gate`` forever.
    4. running  — producer fresh (``_producer_fresh``: ``dashboard.json``'s
       mtime, falling back to ``index.json``'s only while no dashboard has
       been written yet — so a just-minted cycle reads ``running``, not
       ``detached``, before its first round closes).
    5. detached — active but the producer stopped writing (died without a
       terminal record). The only branch that consults freshness.

    The live single-cycle view reads ``dashboard.json::run_phase`` directly
    (declared by the runner) rather than calling this — connection loss there
    is presentation, not a run-phase re-derivation (frontend-surface-contract
    I6). This is the sole liveness derivation for every other reader: the
    cycle list/picker and the reaper's staleness sweep (``_is_dead``) both
    call this — no second "is it running?" computation.
    """
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
