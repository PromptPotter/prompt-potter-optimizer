"""The cycle's budget guardrail, in one legible place.

`run_round_loop` ends a cycle for several reasons; the *budget* ones used to be
an inline `if spend_probe() >= cap` buried in the loop body. They now live here
behind names that say what they are, so a developer reading the loop sees
`budget_gate.tripped()` and a developer reading *this* file sees exactly which
ceilings exist and how each is read.

Two ceilings, one gate:

- **USD** — cumulative optimizer + backend dollars. Brittle alone: a free
  backend reports $0, so this sees only optimizer cost.
- **Tokens** — cumulative optimizer + backend tokens. The model-portable twin;
  it counts backend work the USD ceiling misses.

Whichever trips first halts the cycle at the next clean round boundary. Each
ceiling is a *pair* of zero-arg callables — a `spent` probe and a `cap` probe —
so the gate re-reads the cap every tick (the `change-spend-budget` command
rewrites `.runtime/spend_cap.json` mid-flight) and never caches a stale ceiling.
A ceiling whose probes are `None` is disarmed; a gate with both disarmed never
trips.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from promptpotter.domain.phases import StopReason

if TYPE_CHECKING:
    from promptpotter.domain.results import DegradationHealth

OriginGateMode = Literal["strict", "critical_only", "off"]


@dataclass(frozen=True)
class BudgetGate:
    usd_spent: Callable[[], float] | None = None
    usd_cap: Callable[[], float | None] | None = None
    tokens_spent: Callable[[], int] | None = None
    tokens_cap: Callable[[], int | None] | None = None

    def tripped(self) -> StopReason | None:
        """The stop reason if a ceiling is met or exceeded, else `None`."""
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
    """``ORIGIN_GATE`` when the round-0 origin verdict warrants a halt, else ``None``.

    The round-0 sibling of :meth:`BudgetGate.tripped`. A non-healthy origin means
    candidates would be measured against a broken floor (the common case while a
    developer brings up a new connector whose ``pipeline.json`` or backend code is
    still buggy). ``strict`` halts on ``critical`` and on a ``degraded`` floor whose
    degradation the rescore remedy can re-measure away (transient backend noise) —
    but NOT on a purely ``untested`` degraded grade (a wide-CI low-sample round 0),
    which is statistical uncertainty, not a broken measurement. ``critical_only``
    halts only on a structurally-broken origin; ``off`` disarms the gate. A missing
    verdict never trips.
    """
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
    """``BACKEND_UNREACHABLE`` when a closed round's verdict says the backend is down,
    else ``None``. The mid-run sibling of :func:`origin_gate_tripped`.

    Unconditional (no operator mode): a backend unreachable for most of a round is
    never an optimization signal — there is nothing to decide, only a corpse to grind.
    Halting at the round boundary turns six silent zero-accuracy rounds into one clear
    "restart the backend and ``resume``". A missing verdict never trips.
    """
    if health is None:
        return None
    if health.grade == "critical" and "backend_unreachable" in health.reasons:
        return StopReason.BACKEND_UNREACHABLE
    return None


__all__ = [
    "BudgetGate",
    "OriginGateMode",
    "backend_unreachable_tripped",
    "origin_gate_tripped",
]
