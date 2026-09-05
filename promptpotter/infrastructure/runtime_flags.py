"""Readers for the per-cycle Control-local flags (ADR-0001) and :func:`derive_run_phase`, the ONE
place run-state is computed — for every reader, live surfaces included."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from promptpotter.domain.phases import RunPhase
from promptpotter.infrastructure.store.io import read_json_tolerant, write_json
from promptpotter.infrastructure.store.layout import CycleLayout


def is_paused(cycle_dir: Path) -> bool:
    """``.runtime/pause.flag`` present — the single operator-interrupt flag (there is no
    ``stop.flag``). The loop exits at the next checkpoint and the cycle stays resumable."""
    return CycleLayout(cycle_dir).pause_flag.is_file()


def is_checkin(cycle_dir: Path) -> bool:
    """``.runtime/checkin.flag`` present — the campaign is still authoring its origin, not
    running. Dropped at skeleton creation, cleared when Start flips ``checkin`` → ``active``."""
    return CycleLayout(cycle_dir).checkin_flag.is_file()


def write_sample_lookahead(cycle_dir: Path, cells: int) -> None:
    """How many samples the walk holds in flight, from now until the round that scores under it
    ends. ``cells <= 1`` removes the file, so "back to sequential" and "never set" are one on-disk
    state rather than two that read alike."""
    path = CycleLayout(cycle_dir).sample_lookahead
    if cells <= 1:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, {"cells": int(cells), "requested_at": time.time()})


def effective_lookahead(requested: int, ceiling: int) -> int:
    """What the walk will ACTUALLY hold in flight — the request bounded by the connector's
    declared ``max_cells_in_flight``.

    **The one clamp.** It lives here rather than inside the walk because two readers need the same
    answer and only one of them has a `Session`: `query_loop._lookahead` runs it, and
    `overlay_armed_controls` serves it. The write side stores the request UNCLAMPED on purpose —
    a ceiling is a property of the backend a cycle is running against, not of the press — so
    every reader that means "the depth in force" has to end up here, and the one that did not was
    serving a number the walk had never agreed to."""
    return max(1, min(requested, ceiling))


def read_sample_lookahead(cycle_dir: Path) -> int:
    """The depth REQUESTED, unclamped — read by the walk at every launch boundary and, through
    :func:`effective_lookahead`, served as ``dashboard.json::sample_lookahead``. So a press applies
    to a walk already running, waits harmlessly for the next one if none is, and is gone once the
    round clears the file.

    Not the depth in force on its own: `POST /commands/set-sample-lookahead` takes any int ≥ 1,
    and the connector's ceiling is what decides how much of it the walk honours.

    ``1`` when absent, unreadable or malformed: the failure direction is "run as normal", never
    "stall"."""
    data = read_json_tolerant(CycleLayout(cycle_dir).sample_lookahead)
    if not isinstance(data, dict):
        return 1
    cells = data.get("cells")
    if not isinstance(cells, int) or isinstance(cells, bool):
        return 1
    return max(1, cells)


def clear_run_control_flags(cycle_dir: Path) -> tuple[float | None, int | None]:
    """Drop every POLLED run-control flag — a fresh launch IS the operator's intent to run at the
    engine's own cadence, and a flag surviving the gesture it answered re-answers the next one.

    **Returns the spend ceiling it dropped, and the caller must compose it.** Alone among these
    flags, ``spend_cap.json`` can carry a decision the run has not acted on yet:
    ``change-spend-budget`` applies to a PAUSED cycle too, and swept like the rest, a lowering the
    operator was acked ``applied`` for simply stopped existing at resume. Reading it HERE rather
    than at the call site is what stops the read and the sweep drifting apart into the wrong order,
    which loses the ceiling with nothing raising."""
    layout = CycleLayout(cycle_dir)
    dropped = read_spend_caps(cycle_dir)
    layout.pause_flag.unlink(missing_ok=True)
    layout.skip_flag.unlink(missing_ok=True)
    layout.sample_lookahead.unlink(missing_ok=True)
    # `entry.py::_usd_cap` prefers this file over the cap the launch just composed, so a ceiling
    # clamped against a richer account governs every later resume unless it goes with the run.
    layout.spend_cap.unlink(missing_ok=True)
    return dropped


def write_spend_caps(cycle_dir: Path, *, usd: float | None, tokens: int | None) -> None:
    """Land the live ceilings, an unmetered arm omitted. Peer of :func:`read_spend_caps` so the
    shape is spelled once — a caller hand-building this dict is a writer that can drift from its
    own reader."""
    path = CycleLayout(cycle_dir).spend_cap
    path.parent.mkdir(parents=True, exist_ok=True)
    caps: dict[str, float | int] = {}
    if usd is not None:
        caps["max_usd"] = usd
    if tokens is not None:
        caps["max_tokens"] = tokens
    write_json(path, caps)


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


def overlay_armed_controls(body: dict[str, Any], cycle_dir: Path) -> None:
    """Re-read every ARMED run-control value into a served ``dashboard.json`` body, so a surface
    shows what the loop will read rather than what the runner last flushed. Mutates in place; a
    body with no ``run_limits`` block simply has no ceilings to correct.

    **The reason is the one ``run_phase`` is derived rather than served, and it is a property of
    the WRITER, not of any one field**: the API process applies the command while
    :class:`LiveDashboardView` projects it from the RUNNER's, so the file answers for the last
    record rather than for the press — forever on a halted cycle. ``.runtime/`` is in the
    conditional-GET validator, so a press expires the cached answer on its own.

    **A REPLAY must not call this.** These are the values in force now, and restating one as a past
    moment's is a fabrication."""
    limits = body.get("run_limits")
    if isinstance(limits, dict):
        armed_usd, armed_tokens = read_spend_caps(cycle_dir)
        if armed_usd is not None:
            limits["spend_budget_usd"] = armed_usd
        if armed_tokens is not None:
            limits["token_budget"] = armed_tokens
    # Clamped against the SERVED ceiling, so this is the depth the walk will hold rather than the
    # depth someone asked for. `max_cells_in_flight` is a WIRING_FIELD stamped at INIT:exit, so it
    # is already in the body being corrected. Unclamped, an out-of-range request rendered as fact:
    # the browser's segmented control offers 1..maxCells, so a served 8 against a ceiling of 2 lit
    # no segment at all, and the panel claimed a depth nothing was running.
    ceiling = body.get("max_cells_in_flight")
    body["sample_lookahead"] = effective_lookahead(
        read_sample_lookahead(cycle_dir),
        ceiling if isinstance(ceiling, int) and not isinstance(ceiling, bool) else 1,
    )


# dashboard.json untouched for longer than this ⇒ an active cycle's producer is
# treated as vanished (detached). The loop bumps the file on every sample /
# progress tick / round boundary, so a healthy run stays well inside the window
# even across long backend calls. This is the sole remaining use of freshness —
# it splits running from detached, it does not define "running".
RUN_FRESH_S = 30.0


def _heartbeat_mtime(cycle_dir: Path) -> float | None:
    """The producer's last sign of life. ``dashboard.json`` is canonical whenever it exists — never
    averaged or maxed against ``index.json``, which is the fallback only until the first dashboard
    write lands. ``None`` when the cycle has neither."""
    layout = CycleLayout(cycle_dir)
    for path in (layout.dashboard, layout.manifest):
        try:
            return path.stat().st_mtime
        except OSError:
            continue
    return None


def _detached_after(cycle_dir: Path, *, fresh_s: float) -> float | None:
    """The clock instant the last heartbeat stops counting as fresh — **the one expression of the
    ``running`` → ``detached`` edge.** Both readers of that edge (the phase itself, and the
    conditional-GET validator that has to expire a cached answer when it passes) ask here, so the
    304 path cannot come to disagree with the body it is short-circuiting."""
    beat = _heartbeat_mtime(cycle_dir)
    return None if beat is None else beat + fresh_s


def _producer_fresh(cycle_dir: Path, *, fresh_s: float) -> bool:
    edge = _detached_after(cycle_dir, fresh_s=fresh_s)
    return edge is not None and time.time() < edge


def _declared_phase(cycle_dir: Path) -> str:
    """``paused`` has TWO writers: the operator's flag, and the runner declaring it (a Ctrl+C out of
    a long phase writes no flag). Consulted for that and ``gate`` only; the rest is derived."""
    data = read_json_tolerant(CycleLayout(cycle_dir).dashboard)
    return str(data.get("declared_phase", "")) if isinstance(data, dict) else ""


def _is_terminal(cycle_dir: Path) -> bool:
    """``index.json::finished_at`` — the lifecycle half, for a caller that does not already hold the
    manifest it was reading anyway."""
    manifest = read_json_tolerant(CycleLayout(cycle_dir).manifest)
    return bool(manifest.get("finished_at")) if isinstance(manifest, dict) else False


def derive_run_phase(
    cycle_dir: Path,
    *,
    is_terminal: bool | None = None,
    declared: str | None = None,
    fresh_s: float = RUN_FRESH_S,
) -> RunPhase:
    """The single run-phase derivation, for EVERY reader — the cycle list, the lineage tree and the
    live surfaces alike. ``paused`` is deliberately NOT freshness-gated (a paused producer has
    exited) while ``gate`` is.

    Both inputs a caller may already be holding are optional, and reading them here is the whole
    point: the live dashboard route and the SSE snapshot hold neither, and serving
    ``dashboard.json``'s stored DECLARATION instead had them saying ``running`` forever after a kill
    (its only writer lives in the runner's own process) while every derived reader said terminal."""
    if is_checkin(cycle_dir):
        return RunPhase.CHECKIN
    if is_terminal is None:
        is_terminal = _is_terminal(cycle_dir)
    if is_terminal:
        return RunPhase.TERMINAL
    if declared is None:
        declared = _declared_phase(cycle_dir)
    if is_paused(cycle_dir) or declared == RunPhase.PAUSED:
        return RunPhase.PAUSED
    fresh = _producer_fresh(cycle_dir, fresh_s=fresh_s)
    if fresh and declared == RunPhase.GATE:
        return RunPhase.GATE
    if fresh:
        return RunPhase.RUNNING
    return RunPhase.DETACHED


def run_phase_validator_epoch(cycle_dir: Path, *, fresh_s: float = RUN_FRESH_S) -> float | None:
    """Every input to :func:`derive_run_phase` folded into ONE monotone epoch a conditional GET can
    compare — stats only, no parse, so it stays cheap enough for the 2 s poll's 304 path.

    Four paths carry a write: the cycle DIRECTORY (its mtime bumps when ``dashboard.json`` is first
    created), ``dashboard.json`` (the declaration), ``index.json`` (the terminal stamp), and the
    ``.runtime`` DIRECTORY (the flags — its mtime bumps on a child create *and* unlink, which a
    per-flag stat cannot see). The fifth term carries no write at all: the ``running`` →
    ``detached`` edge moves with the CLOCK, so without it a stale ``If-Modified-Since`` pins a dead
    producer at ``running`` for as long as the browser keeps polling. It is read from
    :func:`_detached_after`, the same expression the phase itself derives from — restating it here
    is what would let the 304 outlive the answer it stands for."""
    layout = CycleLayout(cycle_dir)
    stamps: list[float] = []
    for path in (layout.cycle_dir, layout.dashboard, layout.manifest, layout.runtime):
        try:
            stamps.append(path.stat().st_mtime)
        except OSError:
            continue
    edge = _detached_after(cycle_dir, fresh_s=fresh_s)
    if edge is not None and time.time() >= edge:
        stamps.append(edge)
    return max(stamps) if stamps else None


__all__ = [
    "RUN_FRESH_S",
    "clear_run_control_flags",
    "derive_run_phase",
    "is_checkin",
    "is_paused",
    "read_sample_lookahead",
    "read_spend_caps",
    "run_phase_validator_epoch",
    "write_sample_lookahead",
    "write_spend_caps",
]
