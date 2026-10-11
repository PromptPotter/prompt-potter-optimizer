from __future__ import annotations

import sys
import time
from pathlib import Path

from promptpotter.domain.dashboard_rows import RunLimits
from promptpotter.domain.phases import (
    STOP_REASON_INFO,
    LaunchStage,
    PauseCause,
    PauseReading,
    ProducerReading,
    ProducerState,
    RunPhase,
    RunState,
)
from promptpotter.domain.run_records import RunLimitsRecord
from promptpotter.domain.spend import declare_ceiling
from promptpotter.infrastructure.llm.heartbeat import HEARTBEAT_INTERVAL_S
from promptpotter.infrastructure.producer_lock import cycle_held, held
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    Controls,
    Progress,
    scan_cycle_facts,
    scan_launch_claim,
    scan_ledger_controls,
    scan_ledger_declared_phase,
    scan_ledger_progress,
    scan_ledger_run_limits,
)
from promptpotter.infrastructure.store.layout import CampaignLayout, CycleLayout, in_inner_sandbox


def is_checkin(cycle_dir: Path) -> bool:
    return scan_cycle_facts(CycleLayout(cycle_dir).ledger).checkin


def standing_controls(cycle_dir: Path) -> Controls:
    return scan_ledger_controls(CycleLayout(cycle_dir).ledger)


def effective_lookahead(requested: int, ceiling: int) -> int:
    """The one clamp: the command records the request UNCLAMPED, the ceiling being the backend's."""
    return max(1, min(requested, ceiling))


def requested_lookahead(controls: Controls) -> int:
    ask = controls.lookahead
    if ask is None:
        return 1
    return sys.maxsize if ask.auto else ask.cells


def standing_run_limits(cycle_dir: Path) -> RunLimitsRecord:
    return scan_ledger_run_limits(CycleLayout(cycle_dir).ledger)


def armed_run_limits(cycle_dir: Path, declared: RunLimits) -> RunLimits:
    standing = standing_run_limits(cycle_dir)
    return RunLimits(
        max_rounds=declared.max_rounds if standing.rounds is None else standing.rounds.max_rounds,
        ceiling=declare_ceiling(declared.ceiling, standing.ceiling),
        optimizer=declared.optimizer,
    )


# `RUN_FRESH_S` bounds ANY ledger append (a heartbeat is one); the other two, the last NON-heartbeat one.
RUN_FRESH_S = 30.0
RECENT_STEP_S = 9 * HEARTBEAT_INTERVAL_S
WEDGED_AFTER_S = 30 * HEARTBEAT_INTERVAL_S


def _heartbeat_mtime(cycle_dir: Path) -> float | None:
    try:
        return CycleLayout(cycle_dir).ledger.stat().st_mtime
    except OSError:
        return None


def _beat_stale_after(cycle_dir: Path) -> float | None:
    beat = _heartbeat_mtime(cycle_dir)
    return None if beat is None else beat + RUN_FRESH_S


# Spans one slow cell; bounds only how long a KILLED verify pass reads as in flight.
VERIFY_FRESH_S = 300.0


def verify_stale_after(cycle_dir: Path) -> float | None:
    try:
        return CycleLayout(cycle_dir).ledger.stat().st_mtime + VERIFY_FRESH_S
    except OSError:
        return None


def _beating(cycle_dir: Path) -> bool:
    edge = _beat_stale_after(cycle_dir)
    return edge is not None and time.time() < edge


def read_run_state(
    *,
    checkin: bool,
    terminal: bool,
    pause_requested: bool,
    declared: str,
    held: bool,
    beating: bool,
    silent_for_s: float | None,
    open_for_s: float | None,
    claimed: LaunchStage | None = None,
) -> RunState:
    """`held` alone decides attachment; the clocks only grade it, so a quiet producer reads WEDGED, still attached."""
    absent = ProducerReading.of(ProducerState.ABSENT)
    taken = ProducerReading.of(ProducerState.CLAIMED)
    if checkin:
        return RunState(RunPhase.CHECKIN, absent if claimed is None else taken)
    if not held and claimed is not None:
        return RunState(claimed, taken)
    if not held:
        owed = declared == RunPhase.RUNNING and not pause_requested and not terminal
        producer = ProducerReading.of(ProducerState.SILENT) if owed else absent
    elif declared == RunPhase.GATE:
        producer = ProducerReading.of(ProducerState.HELD)
    else:
        if not beating or (
            open_for_s is None and silent_for_s is not None and silent_for_s > WEDGED_AFTER_S
        ):
            state = ProducerState.WEDGED
        elif silent_for_s is not None and silent_for_s <= RECENT_STEP_S:
            state = ProducerState.LIVE
        else:
            state = ProducerState.IDLE
        producer = ProducerReading.of(state, silent_for_s=silent_for_s, open_for_s=open_for_s)
    if terminal:
        return RunState(RunPhase.TERMINAL, producer)
    if pause_requested or declared == RunPhase.PAUSED:
        return RunState(RunPhase.PAUSED, producer)
    if producer.state is ProducerState.HELD:
        return RunState(RunPhase.GATE, producer)
    return RunState(RunPhase.RUNNING if producer.attached else RunPhase.DETACHED, producer)


def derive_run_state(cycle_dir: Path) -> RunState:
    ledger = CycleLayout(cycle_dir).ledger
    checkin = is_checkin(cycle_dir)
    declared = scan_ledger_declared_phase(ledger)
    terminal = declared == RunPhase.TERMINAL
    settled = checkin or terminal
    producer_held = cycle_held(cycle_dir)
    progress = scan_ledger_progress(ledger) if producer_held else Progress()
    asked = None if settled else standing_controls(cycle_dir).pause
    claim = None if producer_held else scan_launch_claim(ledger)
    now = time.time()
    run = read_run_state(
        checkin=checkin,
        terminal=terminal,
        pause_requested=asked is not None,
        declared="" if settled else declared,
        held=producer_held,
        beating=_beating(cycle_dir),
        silent_for_s=None
        if progress.progressed_at is None
        else max(0.0, now - progress.progressed_at),
        open_for_s=None
        if progress.waiting_since is None
        else max(0.0, now - progress.waiting_since),
        claimed=None if claim is None or not held(Path(claim.claimant_lock)) else claim.stage,
    )._replace(inner=in_inner_sandbox(cycle_dir))
    if run.run_phase is not RunPhase.PAUSED:
        return run
    stood = scan_cycle_facts(ledger).paused
    if stood is not None and stood.stop_reason is not None and stood.cause is not None:
        return run._replace(
            pause=PauseReading(
                stop_reason=stood.stop_reason,
                cause=stood.cause,
                detail=stood.detail,
                next_step=STOP_REASON_INFO[stood.stop_reason].next_step,
                at=stood.timestamp,
            )
        )
    if asked is None:
        return run
    return run._replace(
        pause=PauseReading(
            stop_reason=None,
            cause=PauseCause.COMMAND,
            detail=f"pause-cycle by {asked.issued_by}" if asked.issued_by else "pause-cycle",
            next_step="The run stops at its next checkpoint.",
            at=asked.at,
        )
    )


def run_phase_validator_epoch(cycle_dir: Path) -> float | None:
    """Stats only, no parse: this is the 2 s poll's 304 path."""
    layout = CycleLayout(cycle_dir)
    campaign_manifest = CampaignLayout(cycle_dir.parent.parent).manifest
    stamps: list[float] = []
    for path in (
        layout.cycle_dir,
        layout.dashboard,
        layout.runtime,  # the producer lock: a directory's mtime sees a child's create AND unlink
        layout.ledger,
        campaign_manifest,
    ):
        try:
            stamps.append(path.stat().st_mtime)
        except OSError:
            continue
    for edge in (_beat_stale_after(cycle_dir), verify_stale_after(cycle_dir)):
        if edge is not None and time.time() >= edge:
            stamps.append(edge)
    # A claim lasts as long as its process, which no write marks: a claimed cycle is never a 304.
    if scan_launch_claim(layout.ledger) is not None:
        stamps.append(time.time())
    return max(stamps) if stamps else None


__all__ = [
    "RECENT_STEP_S",
    "RUN_FRESH_S",
    "WEDGED_AFTER_S",
    "armed_run_limits",
    "derive_run_state",
    "effective_lookahead",
    "is_checkin",
    "read_run_state",
    "requested_lookahead",
    "run_phase_validator_epoch",
    "standing_controls",
    "standing_run_limits",
    "verify_stale_after",
]
