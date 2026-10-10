from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, cast

from promptpotter.domain.phases import (
    REFUSAL_STOPS,
    GateDecision,
    PauseCause,
    RunPhase,
    StopOutcome,
    StopReason,
    stop_reason_outcome,
)
from promptpotter.domain.run_records import RunPhaseRecord
from promptpotter.infrastructure.llm.spend_book import SpendBook, unbounded_spend_book
from promptpotter.infrastructure.llm.telemetry import emit_command_ack
from promptpotter.infrastructure.runtime_flags import requested_lookahead, standing_controls
from promptpotter.infrastructure.store.campaign_store.ledger_scan import Controls

if TYPE_CHECKING:
    from pathlib import Path

    from promptpotter.application.initialization.session import Session
    from promptpotter.domain.phase_views import RunSpendView

__all__ = ["RunControl", "declare_run_phase", "declare_run_stop"]


@dataclass(frozen=True)
class RunControl:
    """Every ``take_*`` / ``spend_*`` acks on the run's own ledger: call inside the launch that bound it."""

    cycle_dir: Path | None = None
    # Outermost first. Their pause stops this run; their look-ahead never reaches it — inherited,
    # one arming would multiply concurrency at every nested level.
    enclosing: tuple[Path, ...] = ()
    book: SpendBook = field(default_factory=unbounded_spend_book)
    # Held for the whole run and never spent (a screen has no round); ``None`` reads the inbox.
    held_lookahead: int | None = None

    def _inbox(self) -> Controls:
        return Controls() if self.cycle_dir is None else standing_controls(self.cycle_dir)

    def pause_requested(self) -> bool:
        """A pause is a CLEAN EXIT: poll it only where work already done is on disk."""
        if self._inbox().pause is not None:
            return True
        return any(standing_controls(outer).pause is not None for outer in self.enclosing)

    def take_pause(self) -> tuple[PauseCause, str] | None:
        """``None`` where nothing on a ledger asked: a signal or a cancellation, which the caller names."""
        asked = self._inbox().pause
        if asked is not None:
            emit_command_ack(
                command_id=asked.command_id,
                status="applied",
                effect={"run_phase": RunPhase.PAUSED.value},
            )
            who = f" by {asked.issued_by}" if asked.issued_by else ""
            return PauseCause.COMMAND, f"pause-cycle{who}"
        for outer in reversed(self.enclosing):
            if standing_controls(outer).pause is not None:
                return PauseCause.ENCLOSING, f"enclosing run {outer.name} was paused"
        return None

    def skip_requested(self) -> bool:
        return bool(self._inbox().skips)

    def spend_skip(self) -> bool:
        skips = self._inbox().skips
        if not skips:
            return False
        emit_command_ack(command_id=skips[0], status="applied", effect={"skipped": 1})
        return True

    def take_gate_decision(self) -> GateDecision | None:
        standing = self._inbox().gate_decisions
        if not standing:
            return None
        command_id, decision = standing[0]
        emit_command_ack(command_id=command_id, status="applied", effect={"decision": decision})
        return cast("GateDecision", decision)

    def sample_lookahead(self) -> int:
        if self.held_lookahead is not None:
            return self.held_lookahead
        return requested_lookahead(self._inbox())

    def spend_sample_lookahead(self) -> None:
        """One ack per round removes a plain press and leaves an ``auto`` one standing."""
        if self.held_lookahead is not None:
            return
        asked = self._inbox().lookahead
        if asked is not None and not asked.taken:
            emit_command_ack(
                command_id=asked.command_id,
                status="applied",
                effect={"cells": asked.cells, "auto": asked.auto},
            )

    def budget_tripped(self) -> StopReason | None:
        """Read between samples and at a round boundary; whether a call may be SENT is the book's."""
        refused = self.book.exhausted()
        return None if refused is None else REFUSAL_STOPS[refused]

    def spend_used_usd(self) -> float:
        """In the meter's units; a FLOOR while unpriced tokens are outstanding."""
        return self.book.usd_metered


def declare_run_phase(session: Session, phase: Literal[RunPhase.RUNNING, RunPhase.GATE]) -> None:
    ledger = session.state.ledger
    if ledger is not None:
        ledger.append(RunPhaseRecord(run_phase=phase))


def declare_run_stop(
    session: Session,
    stop_reason: StopReason,
    *,
    interrupted_by: PauseCause,
    spend: RunSpendView | None = None,
) -> None:
    """Declared where the run ENDS, never at the checkpoint that raised it: a second site, a second record."""
    ledger = session.state.ledger
    if ledger is None:
        return
    if stop_reason_outcome(stop_reason) is not StopOutcome.PAUSED:
        ledger.append(RunPhaseRecord.stop(stop_reason, spend=spend))
        return
    cause, detail = session.control.take_pause() or (interrupted_by, "")
    ledger.append(RunPhaseRecord.stop(stop_reason, cause=cause, detail=detail, spend=spend))
