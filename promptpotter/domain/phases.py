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

    Operator-recoverable halts (no traceback, plain ``resume`` fixes):
    - ``INTERRUPTED`` — Ctrl+C / asyncio.CancelledError. Split from CRASHED
      so the operator can tell "I hit Ctrl+C" from "swallowed traceback".
    - ``DIVERGED`` — resume detected recorded-vs-current decision mismatch
      under changed policy; one-flag rerun.
    - ``RENDER_ERROR`` — an injection renderer raised (usually code drift on
      a renamed field). Failing injection + traceback in ``index.json::final``.
      Fix the renderer and ``resume``.
    - ``OPTIMIZER_TIMEOUT`` — optimizer LLM blew its wall-clock twice (provider
      stalled mid-stream, see ``llm_call._chat_under_deadline``); plain ``resume``.

    ``CRASHED`` is the catch-all for unhandled exceptions in the round loop.
    """

    PERFECT = "perfect_score"
    MAX_ROUNDS = "max_rounds"
    INTERRUPTED = "interrupted"
    CRASHED = "crashed"
    DIVERGED = "diverged"
    ABORT = "escalation_abort"
    L3_PATIENCE = "l3_patience_exhausted"
    HARD_CAP = "hard_cap_reached"
    SWEEP_COMPLETE = "sweep_complete"
    DIAG_COMPLETE = "diag_complete"
    TARGET_HIT = "target_hit"
    MAX_SPEND = "max_spend"
    RENDER_ERROR = "render_error"
    OPTIMIZER_TIMEOUT = "optimizer_timeout"


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
