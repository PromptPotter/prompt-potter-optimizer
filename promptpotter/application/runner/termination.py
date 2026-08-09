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
    """``ORIGIN_GATE`` when round 0's verdict warrants a halt — candidates would otherwise be measured
    against a broken floor. ``strict`` spares a purely ``untested`` grade: that is uncertainty, not damage."""
    if mode == "off" or health is None:
        return None
    if health.grade == "critical":
        return StopReason.ORIGIN_GATE
    # Strict also halts on a degraded floor — but only when the degradation is a
    # corrupted measurement the gate's rescore remedy can re-measure away (transient
    # backend noise, reason "degraded"). A degraded grade whose only reason is
    # "untested" is the wide confidence interval of a small round-0 sample, not a
    # broken floor: re-scoring the SAME samples can't narrow a CI and ``untested``
    # stays true at round 0, so the gate would offer a non-remedy and deadlock.
    # Statistical uncertainty isn't gate-worthy — the operator proceeds (or scores
    # more samples); only a corrupted floor halts.
    if (
        mode == "strict"
        and health.grade == "degraded"
        and any(reason != "untested" for reason in health.reasons)
    ):
        return StopReason.ORIGIN_GATE
    return None


def backend_unreachable_tripped(health: DegradationHealth | None) -> StopReason | None:
    """``BACKEND_UNREACHABLE`` on a closed round whose verdict says the backend is down. Unconditional:
    there is nothing to decide, only a corpse to grind, and halting turns six silent rounds into one."""
    if health is None:
        return None
    if health.grade == "critical" and "backend_unreachable" in health.reasons:
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
