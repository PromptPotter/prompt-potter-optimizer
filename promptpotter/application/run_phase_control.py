"""Declare a run-phase control transition onto the ledger. The runner is the SOLE declarer of ``running`` / ``paused``, so a
paused run declares once and every surface reads the truth even after ``dashboard.json`` goes stale."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from promptpotter.domain.phases import RunPhase
from promptpotter.domain.run_records import PhaseRecord

if TYPE_CHECKING:
    from promptpotter.application.initialization.session import Session

__all__ = ["declare_run_phase", "pause_requested"]

# The PhaseRecord.phase discriminator that LiveDashboardView routes to run_phase.
_CONTROL_PHASE = "control"


def declare_run_phase(
    session: Session,
    phase: Literal[RunPhase.RUNNING, RunPhase.PAUSED, RunPhase.GATE, RunPhase.TERMINAL],
    *,
    stop_reason: str = "",
) -> None:
    """Append a control ``PhaseRecord`` so the projection flips ``run_phase``; no-op before the ledger is bound. Idempotent
    at the projection, so emitting ``paused`` from several checkpoints is cheap.

    ``TERMINAL`` carries the reason, and it belongs here for the same purpose the rest do: it was
    pushed straight into the projection by ``LiveDashboardView.mark_stopped``, a side door past the
    ledger, so a cycle could STOP and the record of it existed only in that projection's own output
    and in ``index.json``. A reader folding the ledger saw a run still going."""
    ledger = session.state.ledger
    if ledger is None:
        return
    payload = {"stop_reason": stop_reason} if stop_reason else {}
    ledger.append(PhaseRecord(phase=_CONTROL_PHASE, event=str(phase), payload=payload))


def pause_requested(session: Session) -> bool:
    """The single in-loop pause predicate; a pause is a CLEAN EXIT, never an in-process hold. Place the checkpoint only where
    work already done is on disk, so pausing cannot lose an accumulated datapoint."""
    return session.pause_check is not None and session.pause_check()
