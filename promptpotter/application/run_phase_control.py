"""Declare a run-phase control transition onto the canonical ledger.

The runner is the *sole declarer* of control state (``running`` / ``paused``);
:class:`~promptpotter.infrastructure.projections.live_dashboard.view.LiveDashboardView`
projects the declaration into ``dashboard.json::run_phase`` (the ``terminal``
phase is set by ``mark_stopped`` at finalize, never declared here). A paused run
*declares* ``paused`` once, so every surface reads the truth even after
``dashboard.json`` goes stale.

Vocabulary: :class:`~promptpotter.domain.phases.RunPhase`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from promptpotter.domain.phases import RunPhase
from promptpotter.domain.run_records import PhaseRecord

if TYPE_CHECKING:
    from promptpotter.application.bootstrap.session import Session

__all__ = ["declare_run_phase", "pause_requested"]

# The PhaseRecord.phase discriminator that LiveDashboardView routes to run_phase.
_CONTROL_PHASE = "control"


def declare_run_phase(
    session: Session,
    phase: Literal[RunPhase.RUNNING, RunPhase.PAUSED, RunPhase.GATE],
) -> None:
    """Append a control ``PhaseRecord`` so the dashboard projection flips
    ``run_phase``. No-op before the ledger is bound. Idempotent at the
    projection: re-declaring the current phase neither overwrites ``terminal``
    nor re-flushes, so emitting ``paused`` from several checkpoints is cheap.
    """
    ledger = session.state.ledger
    if ledger is None:
        return
    ledger.append(PhaseRecord(phase=_CONTROL_PHASE, event=str(phase)))


def pause_requested(session: Session) -> bool:
    """True iff the operator set the pause flag (``.runtime/pause.flag``).

    The single in-loop pause predicate. When it fires the caller declares
    ``RunPhase.PAUSED`` and exits its loop cleanly: the worker process ends and
    the cycle stays resumable (``_finalize_run`` skips terminal marking on
    ``StopReason.PAUSED``). There is no in-process hold — a pause is a clean
    exit, and the play action relaunches via ``start-run``/``resume``.

    Place the checkpoint only at a seam where any work already done is on disk
    (e.g. after ``persist_fresh`` in the per-sample loop), so a pause never loses
    an accumulated datapoint.
    """
    return session.pause_check is not None and session.pause_check()
