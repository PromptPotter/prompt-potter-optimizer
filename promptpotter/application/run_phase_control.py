"""Declare a run-phase control transition onto the canonical ledger.

The runner is the *sole declarer* of control state (``running`` / ``paused`` /
``stopping``); :class:`~promptpotter.infrastructure.projections.live_dashboard.view.LiveDashboardView`
projects the declaration into ``dashboard.json::run_phase`` (the ``terminal``
phase is set by ``mark_stopped`` at finalize, never declared here). This is the
fix for the run-state bug: a paused run used to stop writing ``dashboard.json``
and read as "running" off file freshness — now it *declares* ``paused`` once, so
every surface reads the truth even after the file goes stale.

Vocabulary: :class:`~promptpotter.domain.phases.RunPhase`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from promptpotter.domain.phases import RunPhase
from promptpotter.domain.run_records import PhaseRecord

if TYPE_CHECKING:
    from promptpotter.application.bootstrap.session import Session

__all__ = ["declare_run_phase"]

# The PhaseRecord.phase discriminator that LiveDashboardView routes to run_phase.
_CONTROL_PHASE = "control"


def declare_run_phase(
    session: Session, phase: Literal[RunPhase.RUNNING, RunPhase.PAUSED, RunPhase.STOPPING]
) -> None:
    """Append a control ``PhaseRecord`` so the dashboard projection flips
    ``run_phase``. No-op before the ledger is bound. Idempotent at the
    projection: re-declaring the current phase neither overwrites ``terminal``
    nor re-flushes, so emitting ``stopping`` from several checkpoints is cheap.
    """
    ledger = session.state.ledger
    if ledger is None:
        return
    ledger.append(PhaseRecord(phase=_CONTROL_PHASE, event=str(phase)))
