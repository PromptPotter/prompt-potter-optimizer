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

    current_sp = cycle.tracking.current_sp
    current_pp = current_sp.pipeline_params if current_sp is not None else None
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
        pipeline_params=dict(current_pp) if current_pp else {},
    )

    # Trajectory-memory panels: origin's per-sample hits (frozen at start;
    # tells L1 which samples the parent scaffolding already converts) +
    # cumulative trajectory misses (live cycle-wide miss set from
    # ``current_results``; tells L1 which samples nothing has solved yet
    # this cycle). Both consumed by the two new L1 injections.
    origin_per_sample = list(cycle.tracking.origin_per_sample_results)
    trajectory_misses = [r for r in cycle.tracking.current_results if not r.get("hit")]

    return InjectionBundle(
        opt_sp=cycle.opt_sp,
        pipeline_schema=cycle.session.pipeline_schema,
        cycle_slice=cs,
        digest=RoundDigest(
            diagnostics=latest_diag,
            critique=latest_crit,
        ),
        axes=cycle.axes,
        origin_per_sample=origin_per_sample,
        trajectory_misses=trajectory_misses,
        forbidden_axes_strict=cycle.config.optimization.forbidden_axes_strict,
    )


__all__ = ["build_bundle"]
