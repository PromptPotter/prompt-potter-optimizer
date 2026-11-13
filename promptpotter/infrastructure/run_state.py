"""A cycle's run state, read off what is declared (its ledger's last `RunPhaseRecord`, its
launch's claim, the pause asked of it) and its two locks' liveness."""

from __future__ import annotations

import time
from pathlib import Path

from promptpotter.domain.phases import (
    LaunchStage,
    PauseCause,
    PauseReading,
    ProducerReading,
    ProducerState,
    RunPhase,
    RunState,
)
from promptpotter.domain.run_records import RunPhaseRecord
from promptpotter.infrastructure.holders import holder_of
from promptpotter.infrastructure.llm.heartbeat import HEARTBEAT_INTERVAL_S
from promptpotter.infrastructure.producer_lock import cycle_held, pass_held
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    scan_last,
    scan_ledger_progress,
)
from promptpotter.infrastructure.store.command_log import Controls, PauseAsk
from promptpotter.infrastructure.store.io import newest_mtime_ns
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.infrastructure.store.read_model import LedgerSpan

# `RUN_FRESH_S` bounds ANY ledger append (a heartbeat is one); the other two, the last NON-heartbeat one.
RUN_FRESH_S = 30.0
RECENT_STEP_S = 9 * HEARTBEAT_INTERVAL_S
WEDGED_AFTER_S = 30 * HEARTBEAT_INTERVAL_S


def _beat_stale_after(cycle_dir: Path) -> float | None:
    try:
        beat = CycleLayout(cycle_dir).ledger.stat().st_mtime
    except OSError:
        return None
    return beat + RUN_FRESH_S


def _beating(cycle_dir: Path) -> bool:
    edge = _beat_stale_after(cycle_dir)
    return edge is not None and time.time() < edge


def read_run_state(
    *,
    last: RunPhaseRecord | None,
    claimed: LaunchStage | None,
    asked: PauseAsk | None,
    cycle_held: bool,
    pass_held: bool,
    graded: ProducerReading | None,
) -> RunState:
    """THE precedence ladder, over what is declared (the ledger's last `RunPhaseRecord`, the
    launch's claim, the pause asked) and the two locks' liveness alone. *graded* is a held
    producer's clock grade (`_graded`): the clocks only grade attachment, so a quiet producer
    reads WEDGED, still attached; ``None`` where no producer holds the cycle."""
    declared = None if last is None else last.run_phase
    terminal = declared is RunPhase.TERMINAL
    scoring = cycle_held and pass_held
    # A pause is asked of a producer: with none holding the cycle, a RUNNING one is owed (SILENT).
    asked = asked if cycle_held and (scoring or not terminal) else None
    if not cycle_held and claimed is not None:
        return RunState(claimed, ProducerReading.of(ProducerState.CLAIMED))
    if not cycle_held or graded is None:
        owed = declared is RunPhase.RUNNING
        producer = ProducerReading.of(ProducerState.SILENT if owed else ProducerState.ABSENT)
    elif declared is RunPhase.GATE:
        producer = ProducerReading.of(ProducerState.HELD)
    else:
        producer = graded
    # Before the lifecycle: a pass scores ended cycles too, and a pause asked of it is standing.
    if scoring:
        run = RunState(RunPhase.PAUSED if asked is not None else RunPhase.SCORING, producer)
    elif terminal:
        return RunState(RunPhase.TERMINAL, producer)
    elif asked is not None or declared is RunPhase.PAUSED:
        run = RunState(RunPhase.PAUSED, producer)
    elif producer.state is ProducerState.HELD:
        assert last is not None, "a gate is declared"
        return RunState(RunPhase.GATE, producer, gate=last.gate, drafts=len(last.drafts))
    else:
        return RunState(RunPhase.RUNNING if producer.attached else RunPhase.DETACHED, producer)
    if run.run_phase is not RunPhase.PAUSED:
        return run
    # A pass declares no stop: the pause it reads as is the one asked of it.
    stood = last if not scoring and last is not None and declared is RunPhase.PAUSED else None
    if stood is not None and stood.stop_reason is not None and stood.cause is not None:
        return run._replace(
            pause=PauseReading(
                stop_reason=stood.stop_reason,
                cause=stood.cause,
                detail=stood.detail,
                at=stood.timestamp,
            )
        )
    if asked is None:
        return run
    return run._replace(
        pause=PauseReading(
            stop_reason=None,
            cause=PauseCause.COMMAND,
            detail=f"pause by {asked.issued_by.display}",
            at=asked.at,
        )
    )


def _graded(cycle_dir: Path) -> ProducerReading:
    """A held producer's clock grade: WEDGED past its beat or long silent with no call open."""
    progress = scan_ledger_progress(CycleLayout(cycle_dir).ledger)
    now = time.time()
    silent = None if progress.progressed_at is None else max(0.0, now - progress.progressed_at)
    open_for = None if progress.waiting_since is None else max(0.0, now - progress.waiting_since)
    if not _beating(cycle_dir) or (
        open_for is None and silent is not None and silent > WEDGED_AFTER_S
    ):
        state = ProducerState.WEDGED
    elif silent is not None and silent <= RECENT_STEP_S:
        state = ProducerState.LIVE
    else:
        state = ProducerState.IDLE
    return ProducerReading.of(state, silent_for_s=silent, open_for_s=open_for)


def _claimed(cycle_dir: Path, jobs_dir: Path) -> LaunchStage | None:
    holder = holder_of(jobs_dir, cycle_dir)
    return None if holder is None else holder.stage


def derive_run_state(cycle_dir: Path, inbox: Controls, jobs_dir: Path) -> RunState:
    held = cycle_held(cycle_dir)
    return read_run_state(
        last=scan_last([LedgerSpan(CycleLayout(cycle_dir).ledger)], RunPhaseRecord),
        claimed=None if held else _claimed(cycle_dir, jobs_dir),
        asked=inbox.pause,
        cycle_held=held,
        pass_held=held and pass_held(cycle_dir),
        graded=_graded(cycle_dir) if held else None,
    )


def run_owed(cycle_dir: Path, jobs_dir: Path) -> bool:
    """`SILENT`'s fact without its lock read: the run declared RUNNING, no live launch claiming it.
    Read under the producer lock, where `derive_run_state` would see its reader's own hold."""
    last = scan_last([LedgerSpan(CycleLayout(cycle_dir).ledger)], RunPhaseRecord)
    owed = last is not None and last.run_phase is RunPhase.RUNNING
    return owed and _claimed(cycle_dir, jobs_dir) is None


def producer_attached(cycle_dir: Path, jobs_dir: Path) -> bool:
    """Attachment reads no command: a pause never moves it (`read_run_state`)."""
    return derive_run_state(cycle_dir, Controls(), jobs_dir).producer.attached


def run_state_stamps(cycle_dir: Path, jobs_dir: Path) -> tuple[int | bool | None, ...]:
    """What moves `derive_run_state`: the files it reads, both locks' liveness (a producer's death
    unlinks nothing) and the beat's edge, which no write marks."""
    layout = CycleLayout(cycle_dir)
    edge = _beat_stale_after(cycle_dir)
    return (
        newest_mtime_ns(layout.cycle_dir),
        newest_mtime_ns(layout.ledger),
        cycle_held(cycle_dir),
        pass_held(cycle_dir),
        # A pass that dies writes nothing: past this edge its served mark reads its process gone.
        None if edge is None or time.time() < edge else int(edge * 1e9),
        # A live claim ends with its job's producer, which no write marks: it never 304s.
        time.time_ns() if _claimed(cycle_dir, jobs_dir) is not None else None,
    )
