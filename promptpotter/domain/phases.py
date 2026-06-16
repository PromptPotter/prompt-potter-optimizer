"""PhaseRecord enums, stop reasons, phase events — pure control-flow types."""

from __future__ import annotations

import enum
from collections.abc import Callable
from typing import Any, NamedTuple

from pydantic import BaseModel, Field

from promptpotter.shared.clock import utcnow_iso

__all__ = [
    "CampaignPhase",
    "PhaseEvent",
    "RunPhase",
    "StopLoop",
    "StopOutcome",
    "StopReason",
    "emit_phase",
    "stop_reason_label",
    "stop_reason_outcome",
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
    SPEND_BUDGET = "spend_budget"
    TOKEN_BUDGET = "token_budget"
    ORIGIN_GATE = "origin_gate"
    RENDER_ERROR = "render_error"
    OPTIMIZER_TIMEOUT = "optimizer_timeout"
    REBASED = "rebased_to_fork"


class RunPhase(enum.StrEnum):
    """The single run-state vocabulary every surface reads.

    Composes two orthogonal facts — *lifecycle* (is the cycle active or
    finished) and *control + liveness* (is a process driving it, and what is
    the operator's intent) — into one value derived in exactly one place
    (``derive_run_phase``). Replaces the five independent "is it running?"
    derivations that disagreed (a paused run read as running by one and not
    another).

    - ``RUNNING`` — a process is attached and driving the active cycle.
    - ``PAUSED`` — operator paused; alive, holding at a round boundary.
      Reversible (``pause.flag`` / ``resume-cycle``).
    - ``STOPPING`` — operator requested stop; alive, exits at the next
      boundary (``stop.flag`` / ``stop-cycle``). Terminal-intent.
    - ``DETACHED`` — active lifecycle but no live producer (CLI exited or
      ``kill -9`` left no terminal record). The *only* phase that still uses
      the freshness heuristic; never written into ``dashboard.json`` (a dead
      producer can't write) — only emitted by ``derive_run_phase``.
    - ``TERMINAL`` — the cycle finished; the reason is the cycle's
      :class:`StopReason` (rendered via :func:`stop_reason_label`).
    """

    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    DETACHED = "detached"
    TERMINAL = "terminal"


class StopOutcome(enum.StrEnum):
    """How a terminal cycle ended — the one classification of :class:`StopReason`.

    Every terminal surface (index status display, ``JobStatus``, the webapp
    label) derives from this single table instead of re-encoding the reason
    into its own vocabulary.

    - ``SUCCESS`` — ran to a natural conclusion (perfect score, target hit,
      round cap, sweep/diag complete, converged).
    - ``HALTED`` — operator-initiated stop (Ctrl+C, spend cap, escalation abort).
    - ``FAILED`` — abnormal termination needing operator action (crash, render
      error, resume divergence, optimizer timeout).
    """

    SUCCESS = "success"
    HALTED = "halted"
    FAILED = "failed"


class StopReasonInfo(NamedTuple):
    """One row of the canonical :class:`StopReason` classification table."""

    label: str
    outcome: StopOutcome


# The single source of truth mapping each terminal reason to its operator-facing
# label + outcome class. Exhaustiveness is checked at import time below — adding a
# StopReason without a row here raises before the module loads.
STOP_REASON_INFO: dict[StopReason, StopReasonInfo] = {
    StopReason.PERFECT: StopReasonInfo("Perfect score", StopOutcome.SUCCESS),
    StopReason.TARGET_HIT: StopReasonInfo("Target reached", StopOutcome.SUCCESS),
    StopReason.MAX_ROUNDS: StopReasonInfo("Max rounds", StopOutcome.SUCCESS),
    StopReason.HARD_CAP: StopReasonInfo("Round cap", StopOutcome.SUCCESS),
    StopReason.SWEEP_COMPLETE: StopReasonInfo("Sweep complete", StopOutcome.SUCCESS),
    StopReason.DIAG_COMPLETE: StopReasonInfo("Diagnostic complete", StopOutcome.SUCCESS),
    StopReason.L3_PATIENCE: StopReasonInfo("Converged (L3 patience)", StopOutcome.SUCCESS),
    StopReason.REBASED: StopReasonInfo("Rebased to fork", StopOutcome.SUCCESS),
    StopReason.INTERRUPTED: StopReasonInfo("Interrupted", StopOutcome.HALTED),
    StopReason.ABORT: StopReasonInfo("Escalation abort", StopOutcome.HALTED),
    StopReason.SPEND_BUDGET: StopReasonInfo("Spend budget reached", StopOutcome.HALTED),
    StopReason.TOKEN_BUDGET: StopReasonInfo("Token budget reached", StopOutcome.HALTED),
    StopReason.ORIGIN_GATE: StopReasonInfo("Origin gate (unhealthy origin)", StopOutcome.HALTED),
    StopReason.CRASHED: StopReasonInfo("Crashed", StopOutcome.FAILED),
    StopReason.RENDER_ERROR: StopReasonInfo("Render error", StopOutcome.FAILED),
    StopReason.DIVERGED: StopReasonInfo("Diverged", StopOutcome.FAILED),
    StopReason.OPTIMIZER_TIMEOUT: StopReasonInfo("Optimizer timeout", StopOutcome.FAILED),
}

_missing_stop_info = set(StopReason) - set(STOP_REASON_INFO)
if _missing_stop_info:
    raise RuntimeError(
        f"STOP_REASON_INFO is missing rows for {sorted(r.value for r in _missing_stop_info)} — "
        "every StopReason needs a label + outcome (domain/phases.py)."
    )


def stop_reason_label(reason: StopReason | str) -> str:
    """Operator-facing label for a terminal reason — the one display mapping."""
    return STOP_REASON_INFO[StopReason(reason)].label


def stop_reason_outcome(reason: StopReason | str) -> StopOutcome:
    """Outcome class for a terminal reason — the one classification."""
    return STOP_REASON_INFO[StopReason(reason)].outcome


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
    timestamp: str = Field(default_factory=utcnow_iso)


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
