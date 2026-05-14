"""``build_bundle`` — wires live Cycle state into a frozen ``InjectionBundle``.

Renderers never see ``Cycle`` directly; they read the snapshot this
builder produces. The split keeps the bundle dataclasses free of any
Cycle dependency (which would create an import cycle).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from promptpotter.application.optimization.dispatch.hub.bundle import (
    CycleSlice,
    InjectionBundle,
    RoundDigest,
)

if TYPE_CHECKING:
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.domain.results import RoundResult


logger = logging.getLogger(__name__)


def build_bundle(
    cycle: Cycle,
    *,
    latest_round: RoundResult | None = None,
) -> InjectionBundle:
    """Snapshot live cycle state into a InjectionBundle for one optimizer LLM call.

    Reads the most recent round (if any) for diagnostics + critique, and
    the escalation/tracking counters for the ``diagnostics`` STATUS prefix.
    Renderers don't see ``cycle`` directly — they see the snapshot.

    Pass *latest_round* explicitly for L1_CRITIQUE: the just-completed round
    has not yet been folded into ``cycle.rounds`` (that happens in
    ``Cycle.absorb_round`` after critique fires). L2/L3 callers can omit
    it — we fall back to ``cycle.rounds[-1]`` (post-fold).
    """
    if latest_round is None and cycle.rounds:
        latest_round = cycle.rounds[-1]
    latest_diag = latest_round.diagnostics if latest_round else None
    latest_crit = latest_round.critique if latest_round else None
    round_num = latest_round.round + 1 if latest_round else 1

    cs = CycleSlice(
        round_num=round_num,
        current_accuracy=cycle.tracking.current_accuracy,
        best_accuracy=cycle.tracking.best_accuracy,
        best_round=cycle.tracking.best_round,
        l1_stall_count=cycle.escalation.l1_stall_count,
        l2_round=cycle.escalation.l2_round,
        l2_stall_count=cycle.escalation.l2_stall_count,
        l3_round=cycle.escalation.l3_round,
        l3_stall_count=cycle.escalation.l3_stall_count,
    )

    return InjectionBundle(
        opt_sp=cycle.opt_sp,
        pipeline_schema=cycle.session.pipeline_schema,
        cycle_slice=cs,
        digest=RoundDigest(
            diagnostics=latest_diag,
            critique=latest_crit,
        ),
        axes=cycle.axes,
    )
