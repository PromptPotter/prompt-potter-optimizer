from __future__ import annotations

import logging
from enum import StrEnum

from promptpotter.config.settings import settings
from promptpotter.infrastructure.llm.send_pacing import throttle_stall_seconds
from promptpotter.shared.worded import Worded

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


class SlotBound(StrEnum):
    """What holds a launch below the slots it is offered, as `SLOT_BOUND_WORDS` words it."""

    THROTTLE = "throttle"
    MACHINE = "machine"


# `{name}` stands for the served model's field of that name.
SLOT_BOUND_WORDS: Worded[SlotBound, str] = Worded(
    {
        SlotBound.THROTTLE: "admitting {capacity} of {ceiling} while the provider throttle is saturated",
        SlotBound.MACHINE: "This account's limit of {max_concurrent_cycles} is above the machine's "
        "{ceiling}, so the machine is what binds.",
    }
)
assert SLOT_BOUND_WORDS.keys() == set(SlotBound)
