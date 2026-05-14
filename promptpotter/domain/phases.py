"""PhaseRecord enums, stop reasons, phase events — pure control-flow types."""

from __future__ import annotations

import enum
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

__all__ = [
    "CampaignPhase",
    "PhaseEvent",
    "StopLoop",
    "StopReason",
    "emit_phase",
]


class CampaignPhase(enum.StrEnum):
    """Feedback cycle phase names."""

    INIT = "init"
    ORIGIN = "origin"
    L1_GENERATE = "l1_generate"
    L1_SCORE = "l1_score"
    REFINE_STRATEGY = "refine_strategy"
    MODIFY_PLAN = "modify_plan"
    ESCALATION = "escalation"


class StopReason(enum.StrEnum):
    """Feedback cycle termination reasons.

    ``INTERRUPTED`` is reserved for KeyboardInterrupt + asyncio.CancelledError
    (user-initiated or programmatic cancellation). ``CRASHED`` is the
    catch-all for any other unhandled exception inside the round loop —
    distinguished so the operator can tell "I hit Ctrl+C" from "the run
    died and the traceback was swallowed."
    """

    PERFECT = "perfect_score"
    MAX_ROUNDS = "max_rounds"
    INTERRUPTED = "interrupted"
    CRASHED = "crashed"
    ABORT = "escalation_abort"
    L3_PATIENCE = "l3_patience_exhausted"
    HARD_CAP = "hard_cap_reached"
    SWEEP_COMPLETE = "sweep_complete"
    DIAG_COMPLETE = "diag_complete"
    TARGET_HIT = "target_hit"
    MAX_SPEND = "max_spend"


class StopLoop(Exception):  # noqa: N818 — control-flow signal, not an error
    """Control-flow signal caught once at the top of the round loop."""

    def __init__(self, reason: StopReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


class PhaseEvent(BaseModel):
    """Emitted at phase boundaries during the feedback cycle."""

    model_config = {"frozen": True}

    phase: str
    event: str
    round: int | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


def emit_phase(
    on_phase: Callable[[PhaseEvent], None] | None,
    phase: str,
    event: str,
    *,
    round: int | None = None,
    **data: Any,
) -> None:
    """Build a PhaseEvent and dispatch it; no-op if ``on_phase`` is None."""
    if on_phase is None:
        return
    on_phase(PhaseEvent(phase=phase, event=event, round=round, data=data))
