"""``RunControl``, what steers a run from outside it, and the phase transitions the runner — SOLE
declarer of ``running`` / ``paused`` — puts on the ledger, so every surface reads one truth."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from promptpotter.domain.phases import CONTROL_PHASE, REFUSAL_STOPS, RunPhase, StopReason
from promptpotter.domain.run_records import PhaseRecord
from promptpotter.infrastructure.llm.spend_book import SpendBook, unbounded_spend_book
from promptpotter.infrastructure.runtime_flags import read_sample_lookahead, spend_sample_lookahead
from promptpotter.infrastructure.store.layout import CycleLayout

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from promptpotter.application.initialization.session import Session
    from promptpotter.application.views.view_models import RunSpendView

__all__ = ["RunControl", "declare_run_phase"]


@dataclass(frozen=True)
class RunControl:
    """The operator's flags on one cycle, an enclosing run's stop, and the spend ceiling, as the
    run polls them. Unbound it steers nothing; ``runner/entry.py`` binds one per cycle."""

    # ``None`` until a cycle exists: every flag is pressed on a cycle's directory.
    cycle_dir: Path | None = None
    # An L4 spawner's stop, which reaches the cycle instrumenting it. Its look-ahead depth does
    # not: inherited, one arming would multiply concurrency at every nested level.
    enclosing_pause: Callable[[], bool] | None = None
    # The run's own book — the one home of what it counts against its ceiling. Unbound it holds
    # no ceiling, so it trips nothing.
    book: SpendBook = field(default_factory=unbounded_spend_book)
    # A look-ahead depth held for the whole run and never spent, where no round exists to spend
    # one (a screen); ``None`` reads the cycle's flag.
    held_lookahead: int | None = None

    def pause_requested(self) -> bool:
        """A pause is a CLEAN EXIT, never an in-process hold. Poll it only where work already done
        is on disk, so pausing cannot lose an accumulated datapoint."""
        if self.cycle_dir is not None and CycleLayout(self.cycle_dir).pause_flag.is_file():
            return True
        return self.enclosing_pause is not None and self.enclosing_pause()

    def skip_requested(self) -> bool:
        return self.cycle_dir is not None and CycleLayout(self.cycle_dir).skip_flag.is_file()

    def spend_skip(self) -> None:
        """One-shot: the walk that honours the skip removes it, so exactly one searchpoint is cut."""
        if self.cycle_dir is not None:
            CycleLayout(self.cycle_dir).skip_flag.unlink(missing_ok=True)

    def sample_lookahead(self) -> int:
        if self.held_lookahead is not None:
            return self.held_lookahead
        return 1 if self.cycle_dir is None else read_sample_lookahead(self.cycle_dir)

    def spend_sample_lookahead(self) -> None:
        """Spent by the ROUND that scored under the depth, the one loop every armable walk sits in."""
        if self.held_lookahead is None and self.cycle_dir is not None:
            spend_sample_lookahead(self.cycle_dir)

    def budget_tripped(self) -> StopReason | None:
        """The stop a phase reads between samples and the loop at a round boundary. Whether a call
        may be SENT is the book's own answer, at the send."""
        refused = self.book.exhausted()
        return None if refused is None else REFUSAL_STOPS[refused]

    def spend_used_usd(self) -> float:
        """A FLOOR while unpriced tokens are outstanding."""
        return self.book.usd_spent


def declare_run_phase(
    session: Session,
    phase: Literal[RunPhase.RUNNING, RunPhase.PAUSED, RunPhase.GATE, RunPhase.TERMINAL],
    *,
    stop_reason: StopReason | None = None,
    spend: RunSpendView | None = None,
) -> None:
    """Append a control ``PhaseRecord`` so the projection flips ``run_phase``; no-op before the ledger is bound.
    ``PAUSED`` is declared where a paused exit ENDS (``runner/entry.py``), never at the checkpoint
    that raised it: every checkpoint converges there, so a second site is a second record.

    ``TERMINAL`` carries the reason, and it belongs here for the same purpose the rest do: it was
    pushed straight into the projection by ``LiveDashboardProjection.mark_stopped``, a side door past the
    ledger, so a cycle could STOP and the record of it existed only in that projection's own output
    and in ``index.json``. A reader folding the ledger saw a run still going."""
    ledger = session.state.ledger
    if ledger is None:
        return
    payload: dict[str, Any] = {} if stop_reason is None else {"stop_reason": stop_reason.value}
    # Where a run ends, what it spent rides as the record's view, which the readout prints.
    if spend is not None:
        payload["view"] = spend
    ledger.append(PhaseRecord(phase=CONTROL_PHASE, event=str(phase), payload=payload))
