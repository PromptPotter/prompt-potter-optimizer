"""Run-state model invariants — the single run-phase derivation + the single
StopReason→outcome classification that every surface (index status, JobStatus,
the webapp label) now reads. Guards the reported bug (a paused run must not read
as running) and the cross-surface consistency fix (optimizer_timeout no longer
splits "failed" on JobRegistry vs a distinct status on the index).
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from promptpotter.domain.phases import (
    STOP_REASON_INFO,
    RunPhase,
    StopOutcome,
    StopReason,
    stop_reason_outcome,
)
from promptpotter.infrastructure.runtime_flags import derive_run_phase

# Mirror of the sole outcome→JobStatus bridge (application/jobs/launcher.py); if
# that map drifts from this, the cross-surface guarantee is broken.
_JOB_STATUS_BY_OUTCOME = {
    StopOutcome.SUCCESS: "completed",
    StopOutcome.HALTED: "stopped",
    StopOutcome.FAILED: "failed",
}


def _fresh_dashboard(cycle_dir: Path, *, stale: bool) -> None:
    dash = cycle_dir / "dashboard.json"
    dash.write_text("{}", encoding="utf-8")
    if stale:
        old = time.time() - 10_000
        os.utime(dash, (old, old))


def test_derive_run_phase_priority(tmp_path: Path) -> None:
    """terminal > stopping > paused > running > detached, and freshness is
    consulted ONLY to split running from detached — never to decide paused."""
    runtime = tmp_path / ".runtime"
    runtime.mkdir()

    # Terminal wins over every live signal, even with a fresh file + flags set.
    _fresh_dashboard(tmp_path, stale=False)
    (runtime / "pause.flag").write_text("x", encoding="utf-8")
    (runtime / "stop.flag").write_text("x", encoding="utf-8")
    assert derive_run_phase(tmp_path, is_terminal=True) is RunPhase.TERMINAL

    # Stop-intent beats pause (terminal-intent wins during a pause).
    assert derive_run_phase(tmp_path, is_terminal=False) is RunPhase.STOPPING

    # Pause with no stop — the reported bug: a paused run (fresh file) must read
    # paused, not running.
    (runtime / "stop.flag").unlink()
    assert derive_run_phase(tmp_path, is_terminal=False) is RunPhase.PAUSED

    # No flags, fresh producer → running.
    (runtime / "pause.flag").unlink()
    assert derive_run_phase(tmp_path, is_terminal=False) is RunPhase.RUNNING

    # No flags, stale producer (died without a terminal record) → detached.
    _fresh_dashboard(tmp_path, stale=True)
    assert derive_run_phase(tmp_path, is_terminal=False) is RunPhase.DETACHED


def test_stop_reason_outcome_is_total_and_consistent() -> None:
    """Every StopReason classifies, and the one outcome table drives JobStatus —
    so optimizer_timeout can't be "failed" on one surface and "completed" on
    another (the pre-unification split)."""
    assert set(STOP_REASON_INFO) == set(StopReason)
    for reason in StopReason:
        outcome = stop_reason_outcome(reason)
        assert outcome in _JOB_STATUS_BY_OUTCOME  # total mapping, no fallthrough

    assert stop_reason_outcome(StopReason.OPTIMIZER_TIMEOUT) is StopOutcome.FAILED
    assert stop_reason_outcome(StopReason.INTERRUPTED) is StopOutcome.HALTED
    assert stop_reason_outcome(StopReason.PERFECT) is StopOutcome.SUCCESS
