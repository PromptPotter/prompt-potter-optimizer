"""Phase lifecycle event for feedback cycle observability."""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class PhaseEvent(BaseModel):
    """Emitted at phase boundaries during the feedback cycle.

    Phases: init, l1_generate, l1_evaluate, refine_context, modify_plan.
    Each phase emits an "enter" and "exit" event with phase-specific data.
    """

    model_config = {"frozen": True}

    phase: str
    event: str
    round: int | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
    )
