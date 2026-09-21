"""Two budget ceilings, one gate, halting at the next clean round boundary — twins because a free
backend reports $0 while tokens count the work it misses. Caps re-read every tick, never cached;
what enforces them is the admission of each call, ahead of any boundary."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from promptpotter.domain.phases import REFUSAL_STOPS, StopLoop, StopReason
from promptpotter.shared.errors import SendRefusedError, is_repairable_hole

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from promptpotter.domain.results import DegradationHealth
    from promptpotter.infrastructure.llm.spend_book import SpendBook

logger = logging.getLogger(__name__)

OriginGateMode = Literal["strict", "critical_only", "off"]
PanelGateMode = Literal["strict", "off"]

# What ends a run on a named reason wherever it is raised — prep, init or the round loop.
RUN_STOPS = (StopLoop, SendRefusedError)


def run_stop_reason(stop: StopLoop | SendRefusedError) -> StopReason:
    if isinstance(stop, SendRefusedError):
        logger.warning("Run halted: %s", stop)
        return REFUSAL_STOPS[stop.category]
    return stop.reason


@dataclass(frozen=True)
class BudgetGate:
    """The run's ceilings as the round loop asks them: whether one is already reached. Whether a
    call may be SENT is the spend book's to answer (``infrastructure/llm/spend_book.py``)."""

    book: SpendBook

    def tripped(self) -> StopReason | None:
        refused = self.book.exhausted()
        return None if refused is None else REFUSAL_STOPS[refused]


def origin_gate_tripped(
    health: DegradationHealth | None, mode: OriginGateMode
) -> StopReason | None:
    """``ORIGIN_GATE`` when round 0's verdict warrants a halt — candidates would otherwise be
    measured against a broken floor."""
    if mode == "off" or health is None:
        return None
    if health.grade == "critical":
        return StopReason.ORIGIN_GATE
    # Strict also halts on a degraded floor: `degraded` carries exactly one cause
    # (`results_health.py`) — transient backend noise the gate's rescore can re-measure away.
    if mode == "strict" and health.grade == "degraded":
        return StopReason.ORIGIN_GATE
    return None


def panel_gate_tripped(
    holed_rows: Sequence[Mapping[str, Any]], mode: PanelGateMode
) -> StopReason | None:
    """``PAUSED`` when an electable candidate's panel has holes, so candidates were ranked on different
    cell sets. **Not a second under-probing guard** — ``coverage_floor`` excludes; this halts, resumably.

    ONE hole a declared bound CUT makes it ``PANEL_CUT`` instead. Both halt and both are resumable,
    but only one is plugged by resuming: the declaration that cut the cell cuts the re-run at the
    same place, so the standing advice would re-buy the round at full price on every attempt."""
    if mode == "off" or not holed_rows:
        return None
    if any(not is_repairable_hole(row) for row in holed_rows):
        return StopReason.PANEL_CUT
    return StopReason.PAUSED


__all__ = [
    "RUN_STOPS",
    "BudgetGate",
    "OriginGateMode",
    "origin_gate_tripped",
    "panel_gate_tripped",
    "run_stop_reason",
]
