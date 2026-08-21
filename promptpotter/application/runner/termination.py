"""Two budget ceilings, one gate, halting at the next clean round boundary — twins because a free
backend reports $0 while tokens count the work it misses. Caps re-read every tick, never cached."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from promptpotter.domain.phases import StopReason

if TYPE_CHECKING:
    from promptpotter.domain.results import DegradationHealth

OriginGateMode = Literal["strict", "critical_only", "off"]
PanelGateMode = Literal["strict", "off"]


@dataclass(frozen=True)
class BudgetGate:
    usd_spent: Callable[[], float] | None = None
    usd_cap: Callable[[], float | None] | None = None
    tokens_spent: Callable[[], int] | None = None
    tokens_cap: Callable[[], int | None] | None = None

    def tripped(self) -> StopReason | None:
        if self.usd_spent is not None and self.usd_cap is not None:
            cap = self.usd_cap()
            if cap is not None and self.usd_spent() >= cap:
                return StopReason.SPEND_BUDGET
        if self.tokens_spent is not None and self.tokens_cap is not None:
            cap_tok = self.tokens_cap()
            if cap_tok is not None and self.tokens_spent() >= cap_tok:
                return StopReason.TOKEN_BUDGET
        return None


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


def backend_unreachable_tripped(health: DegradationHealth | None) -> StopReason | None:
    """``BACKEND_UNREACHABLE`` on a closed round whose verdict says the backend is down. Unconditional:
    there is nothing to decide, only a corpse to grind, and halting turns six silent rounds into one."""
    if health is None:
        return None
    if health.grade == "critical" and health.cause == "backend_unreachable":
        return StopReason.BACKEND_UNREACHABLE
    return None


def panel_gate_tripped(holed_candidate_ids: list[str], mode: PanelGateMode) -> StopReason | None:
    """``PAUSED`` when an electable candidate's panel has holes, so candidates were ranked on different
    cell sets. **Not a second under-probing guard** — ``coverage_floor`` excludes; this halts, resumably."""
    if mode == "off" or not holed_candidate_ids:
        return None
    return StopReason.PAUSED


__all__ = [
    "BudgetGate",
    "OriginGateMode",
    "backend_unreachable_tripped",
    "origin_gate_tripped",
    "panel_gate_tripped",
]
