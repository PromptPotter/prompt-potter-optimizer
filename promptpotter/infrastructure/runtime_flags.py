"""Readers for the per-cycle Control-local flags (ADR-0001) and :func:`derive_run_phase`, the ONE
place run-state is computed — for every reader, live surfaces included."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from promptpotter.domain.launch_limits import RoundsCap
from promptpotter.domain.phases import RunPhase
from promptpotter.domain.run_records import RunLimitsRecord
from promptpotter.domain.spend import BudgetChange
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


def write_sample_lookahead(cycle_dir: Path, cells: int, *, auto: bool = False) -> None:
    """How many calls the round holds in flight, until the round that scores under it ends — or,
    with ``auto``, as many as its stop rules allow, every round until the operator says otherwise.
    ``cells <= 1`` without ``auto`` removes the file, so "back to sequential" and "never set" are
    one on-disk state rather than two that read alike."""
    path = CycleLayout(cycle_dir).sample_lookahead
    if cells <= 1 and not auto:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, {"cells": int(cells), "auto": auto, "requested_at": time.time()})


def sample_lookahead_auto(cycle_dir: Path) -> bool:
    """Whether the arming outlives its round. A mode, where the plain press is a gesture."""
    data = read_json_tolerant(CycleLayout(cycle_dir).sample_lookahead)
    return isinstance(data, dict) and data.get("auto") is True


def spend_sample_lookahead(cycle_dir: Path) -> None:
    """The round that scored under the arming spends it — a press, never an ``auto`` one. The walk
    wastes at most one call per cut at any depth (``StopRule.earliest_stop``), which is what makes
    an arming that never ends safe to leave on."""
    if not sample_lookahead_auto(cycle_dir):
        CycleLayout(cycle_dir).sample_lookahead.unlink(missing_ok=True)


def effective_lookahead(requested: int, ceiling: int) -> int:
    """What the round will ACTUALLY hold in flight — the request bounded by the connector's
    declared ``max_cells_in_flight``.

    **The one clamp**, and every reader meaning "the depth in force" ends here: the scoring phase
    (`query_loop._armed_cells`), the served overlay (`overlay_armed_controls`), and the dashboard
    file (`live_dashboard/projection.py::_persist`) — which was the one that did not, and wrote a depth
    nothing was running beside the ceiling refusing it. The write side stores the request UNCLAMPED
    on purpose: a ceiling is a property of the backend a cycle runs against, not of the press."""
    return max(1, min(requested, ceiling))


def read_sample_lookahead(cycle_dir: Path) -> int:
    """The depth REQUESTED, unclamped — read by the walk at every launch boundary and, through
    :func:`effective_lookahead`, served as ``dashboard.json::sample_lookahead``. So a press applies
    to a walk already running, waits harmlessly for the next one if none is, and is gone once the
    round clears the file.

    Not the depth in force on its own: `POST /commands/set-sample-lookahead` takes any int ≥ 1,
    and the connector's ceiling is what decides how much of it the walk honours. ``auto`` asks for
    no bound at all, so the ceiling alone decides and the stop rules limit the rest.

    ``1`` when absent, unreadable or malformed: the failure direction is "run as normal", never
    "stall"."""
    data = read_json_tolerant(CycleLayout(cycle_dir).sample_lookahead)
    if not isinstance(data, dict):
        return 1
    if data.get("auto") is True:
        return sys.maxsize
    cells = data.get("cells")
    if not isinstance(cells, int) or isinstance(cells, bool):
        return 1
    return max(1, cells)


def clear_run_control_flags(cycle_dir: Path) -> None:
    """Drop every POLLED run-control flag a fresh launch supersedes — a launch IS the operator's
    intent to run at the engine's own cadence, and a flag surviving the gesture it answered
    re-answers the next one. An ``auto`` look-ahead answered no gesture, so it stays until toggled
    off.

    ``run_limits.json`` goes too, and loses nothing: it only MIRRORS the ledger's standing ceiling,
    which the launch has already declared and admitted, and re-lands the mirror at the value it
    holds (`runner/entry.py::_prepare_run`)."""
    layout = CycleLayout(cycle_dir)
    layout.pause_flag.unlink(missing_ok=True)
    layout.skip_flag.unlink(missing_ok=True)
    layout.run_limits.unlink(missing_ok=True)
    spend_sample_lookahead(cycle_dir)


def write_run_limits_mirror(
    cycle_dir: Path, change: BudgetChange, *, rounds: RoundsCap | None
) -> None:
    """Land the POLLED MIRROR of the cycle's standing operator ceiling, an unset arm omitted — so
    ``max_rounds: null`` (a lifted round cap) and no ``max_rounds`` key are two answers.

    The mirror has one job: carrying a ceiling moved in another process to a run already in
    flight, read on every paid call (`runner/entry.py::_build_budget_gate`), every round boundary
    (`runner/loop.py`) and every served dashboard (:func:`overlay_armed_controls`), where
    rescanning the ledger each time costs the whole log. What the operator DECLARED is the ledger's
    ``RunLimitsRecord`` alone — `CampaignStore.write_run_limits` writes both, and nothing
    else declares one."""
    path = CycleLayout(cycle_dir).run_limits
    path.parent.mkdir(parents=True, exist_ok=True)
    caps: dict[str, float | int | None] = {}
    if change.usd is not None:
        caps["max_usd"] = change.usd
    if change.tokens is not None:
        caps["max_tokens"] = change.tokens
    if rounds is not None:
        caps["max_rounds"] = rounds.max_rounds
    write_json(path, caps)


def read_run_limits_mirror(cycle_dir: Path) -> RunLimitsRecord:
    """The mirrored ceilings, ``None`` per arm when absent, unreadable or the wrong type — for the
    pollers only; a launch reads the ledger (`CampaignStore.read_run_limits`). The one place that
    knows this file's shape."""
    data = read_json_tolerant(CycleLayout(cycle_dir).run_limits)
    if not isinstance(data, dict):
        return RunLimitsRecord()
    usd = data.get("max_usd")
    tokens = data.get("max_tokens")
    rounds = None
    if "max_rounds" in data:
        try:
            rounds = RoundsCap(max_rounds=data["max_rounds"])
        except ValidationError:
            rounds = None
    return RunLimitsRecord(
        usd=float(usd) if isinstance(usd, int | float) and not isinstance(usd, bool) else None,
        tokens=int(tokens) if isinstance(tokens, int) and not isinstance(tokens, bool) else None,
        rounds=rounds,
    )


def armed_run_limits(cycle_dir: Path) -> dict[str, float | int | None]:
    """The mirror as ``run_limits`` updates, an unmoved arm omitted — the ARMED ceilings, which
    both writers of a dashboard body lay over the ones INIT declared."""
    mirror = read_run_limits_mirror(cycle_dir)
    armed: dict[str, float | int | None] = {}
    if mirror.usd is not None:
        armed["spend_budget_usd"] = mirror.usd
    if mirror.tokens is not None:
        armed["token_budget"] = mirror.tokens
    if mirror.rounds is not None:
        armed["max_rounds"] = mirror.rounds.max_rounds
    return armed


def overlay_armed_controls(body: dict[str, Any], cycle_dir: Path) -> None:
    """Re-read every ARMED run-control value into a served ``dashboard.json`` body, so a surface
    shows what the loop will read rather than what the runner last flushed. Mutates in place; a
    body with no ``run_limits`` block simply has no ceilings to correct.

    **The reason is the one ``run_phase`` is derived rather than served, and it is a property of
    the WRITER, not of any one field**: the API process applies the command while
    :class:`LiveDashboardProjection` projects it from the RUNNER's, so the file answers for the last
    record rather than for the press — forever on a halted cycle. ``.runtime/`` is in the
    conditional-GET validator, so a press expires the cached answer on its own.

    **A REPLAY must not call this.** These are the values in force now, and restating one as a past
    moment's is a fabrication. Call it after ``run_phase`` is set on the body."""
    limits = body.get("run_limits")
    if isinstance(limits, dict):
        limits.update(armed_run_limits(cycle_dir))
    # Clamped against the SERVED ceiling, so this is the depth the walk will hold rather than the
    # depth someone asked for. `max_cells_in_flight` is a WIRING_FIELD stamped at INIT:exit, so it
    # is already in the body being corrected. Unclamped, an out-of-range request rendered as fact —
    # a served 8 against a ceiling of 2 claimed a depth nothing was running — and an `auto` arming,
    # which asks for no bound at all, would serve a number no backend holds.
    ceiling = body.get("max_cells_in_flight")
    body["sample_lookahead"] = effective_lookahead(
        read_sample_lookahead(cycle_dir),
        ceiling if isinstance(ceiling, int) and not isinstance(ceiling, bool) else 1,
    )
    body["sample_lookahead_auto"] = sample_lookahead_auto(cycle_dir)
    # The flight gauge is the one FOLDED value that goes stale the same way: a killed or crashed
    # run never publishes its closing zero, so a dead producer would go on reporting calls out.
    if body.get("run_phase") != RunPhase.RUNNING:
        body.update(
            in_flight=0, lookahead_allowed=0, waiting_on=None, waiting_since=None, backpressure=None
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
    "armed_run_limits",
    "clear_run_control_flags",
    "derive_run_phase",
    "is_checkin",
    "is_paused",
    "read_run_limits_mirror",
    "read_sample_lookahead",
    "run_phase_validator_epoch",
    "sample_lookahead_auto",
    "spend_sample_lookahead",
    "write_run_limits_mirror",
    "write_sample_lookahead",
]
