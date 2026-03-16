"""Phase lifecycle event for feedback cycle observability."""

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class PhaseEvent(BaseModel):
    """Emitted at phase boundaries during the feedback cycle.

    Phases: init, l1_generate, l1_evaluate, refine_context, modify_plan.
    Each phase emits an "enter" and "exit" event with phase-specific data.
    """

    phase: str
    event: str
    round: int | None = None
    data: dict = Field(default_factory=dict)
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )
