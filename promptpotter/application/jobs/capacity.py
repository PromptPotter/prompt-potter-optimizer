from __future__ import annotations

import logging

from promptpotter.config.settings import settings
from promptpotter.infrastructure.llm.send_pacing import throttle_stall_seconds

logger = logging.getLogger(__name__)

# "One task waited a whole window". Not a setting: the operator's knob is MACHINE_RUN_CAPACITY.
_STALL_BUDGET_S = 60.0


def resolve_run_capacity(running: int) -> int:
    ceiling = settings.MACHINE_RUN_CAPACITY
    stalled = throttle_stall_seconds()
    if stalled <= _STALL_BUDGET_S:
        return ceiling
    # Never below one, or a stalled quiet box refuses its own last slot and admits nothing again.
    held = max(1, min(ceiling, running))
    if held < ceiling:
        logger.info(
            "throttle back-pressure: %.0fs stall in the last window holds capacity at %d (ceiling %d)",
            stalled,
            held,
            ceiling,
        )
    return held


def throttle_hold(capacity: int, ceiling: int) -> str | None:
    if capacity >= ceiling:
        return None
    return f"admitting {capacity} of {ceiling} while the provider throttle is saturated"
