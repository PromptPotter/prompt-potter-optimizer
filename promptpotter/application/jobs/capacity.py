"""How many campaigns this machine admits right now. A resolved value, never a startup constant —
so a box under provider back-pressure stops admitting before it starts thrashing."""

from __future__ import annotations

import logging

from promptpotter.config.settings import settings
from promptpotter.infrastructure.llm.rate_limit import throttle_stall_seconds

logger = logging.getLogger(__name__)

# Seconds of shared-throttle stall, summed across every task over the trailing window, above which
# the box counts as oversubscribed. Sized as "one task spent a whole window waiting": past that,
# admitting another campaign buys queueing inside the provider window rather than throughput.
# A module constant, not a setting: the operator's knob is MACHINE_RUN_CAPACITY, and a second dial
# nobody tunes is a redundant mechanism.
_STALL_BUDGET_S = 60.0


def resolve_run_capacity(running: int) -> int:
    """Campaigns admissible with *running* already live.

    **This function can only ever LOWER the operator's ceiling, never raise it** — both returns are
    ``<= settings.MACHINE_RUN_CAPACITY`` by construction rather than by assertion, so no runtime
    condition can widen concurrency beyond what was written down. That property is what lets
    back-pressure exist here at all while the provider key is still shared: the standing
    prohibition is on *raising* concurrency, and nothing here can.

    Back-pressure, not resource sizing. The signal is LAGGING — stall accrues only once the box is
    already oversubscribed — which is the right shape for an admission gate, whose whole job is to
    decide about the NEXT run rather than to resize the ones in flight.

    Deliberately reads no CPU, memory, load or disk. ``os.getloadavg`` is Unix-only (this is
    developed on Windows and deployed on Fedora), cgroup and ``/proc`` files are Linux-only, and
    ``psutil`` would be a declared dependency with a ``deptry`` gate to satisfy — for the weakest
    input available. A number that silently reads zero on half the machines it runs on is worse
    than no number. Should a real machine signal ever land, it joins as one more term in the same
    ``min``; nothing else here changes.
    """
    ceiling = settings.MACHINE_RUN_CAPACITY
    stalled = throttle_stall_seconds()
    if stalled <= _STALL_BUDGET_S:
        return ceiling
    # Oversubscribed: admit no MORE than are already live. Never below one, or a quiet box that
    # happens to be stalled could refuse its own last slot and admit nothing ever again.
    held = max(1, min(ceiling, running))
    if held < ceiling:
        logger.info(
            "throttle back-pressure: %.0fs stall in the last window holds capacity at %d (ceiling %d)",
            stalled,
            held,
            ceiling,
        )
    return held


__all__ = ["resolve_run_capacity"]
