from __future__ import annotations

import enum
from collections.abc import Callable
from typing import Any, NamedTuple

from pydantic import ConfigDict, Field

from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.clock import utcnow_iso

__all__ = [
    "CampaignPhase",
    "DashboardState",
    "PhaseEvent",
    "RunPhase",
    "StopLoop",
    "StopOutcome",
    "StopReason",
    "emit_phase",
    "stop_reason_outcome",
]


class CampaignPhase(enum.StrEnum):
    INIT = "init"
    ORIGIN = "origin"
    L1_GENERATE = "l1_generate"
    L1_SCORE = "l1_score"
    REFINE_STRATEGY = "refine_strategy"
    MODIFY_PLAN = "modify_plan"
    ESCALATION = "escalation"


class StopReason(enum.StrEnum):
    """``BACKEND_UNREACHABLE`` halts on a round that was ≥ ``BACKEND_UNREACHABLE_RATE``
    backend-down samples, rather than grinding zero-accuracy rounds against a dead backend."""

    PERFECT = "perfect_score"
    MAX_ROUNDS = "max_rounds"
    LIVES_EXHAUSTED = "lives_exhausted"
    PAUSED = "paused"
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
    BACKEND_UNREACHABLE = "backend_unreachable"
    RENDER_ERROR = "render_error"
    OPTIMIZER_TIMEOUT = "optimizer_timeout"
    REBASED = "rebased_to_fork"
    PRODUCER_VANISHED = "producer_vanished"


class RunPhase(enum.StrEnum):
    """The single run-state vocabulary every surface reads.

    Composes two orthogonal facts — *lifecycle* (is the cycle active or
    finished) and *control + liveness* (is a process driving it, and what is
    the operator's intent) — into one value derived in exactly one place
    (``derive_run_phase``).

    - ``CHECKIN`` — the campaign is still authoring its origin (pre-loop); a
      real disk-backed campaign in the ``checkin`` lifecycle, minted on the
      first ingest action, resumable. Holds no machine slot. Derived from
      ``.runtime/checkin.flag`` (dropped at skeleton creation, cleared at
      Start when ``checkin`` flips to ``active``); precedes every other phase.
    - ``RUNNING`` — a process is attached and driving the active cycle.
    - ``PAUSED`` — the worker exits cleanly but the cycle stays **active and
      resumable** (no ``finished_at``). Three ways in: the pause button (writes
      ``.runtime/pause.flag``), Ctrl+C, and an ``asyncio.CancelledError`` — our
      own machinery stopping a run, typically the L4 outer sample deadline
      cancelling its inner campaign. Only the first writes a flag, so the other
      two are derived off the runner's DECLARATION, made once for all three at
      the finalize seam (``runner/entry.py::_finalize_run``); leaving that to
      each raise site is what let a deliberately-cancelled inner cycle read
      ``detached`` and be stamped ``PRODUCER_VANISHED`` 15 minutes later. The
      play action relaunches via ``start-run``/``resume``. The single
      operator-interrupt phase — there is no separate "stopping".
    - ``GATE`` — alive, holding at the round-0 origin gate awaiting an
      operator decision (rescore / proceed / abort) because the origin
      verdict was not ``healthy``. Reversible (``origin-gate-decision``).
    - ``DETACHED`` — active lifecycle but no live producer (CLI exited or
      ``kill -9`` left no terminal record). The *only* phase that uses the
      freshness heuristic; never written into ``dashboard.json`` (a dead
      producer can't write) — only emitted by ``derive_run_phase``. This is
      a *dead* producer, not a quiet-but-alive one (a live cycle heartbeats
      its ledger → dashboard within ``RUN_FRESH_S``), so the liveness reaper
      (``application/jobs/reaper.py``) stamps a persistently-detached cycle
      ``TERMINAL`` (``PRODUCER_VANISHED``); it is not an in-flight unit and
      drops out of the dock.
    - ``TERMINAL`` — the cycle finished; the reason is the cycle's
      :class:`StopReason` (labelled via the :data:`STOP_REASON_INFO` table).
    """

    CHECKIN = "checkin"
    RUNNING = "running"
    PAUSED = "paused"
    GATE = "gate"
    DETACHED = "detached"
    TERMINAL = "terminal"


class DashboardState(enum.StrEnum):
    """The fine-grained ACTIVITY vocabulary (``dashboard.json::state``), orthogonal to
    :class:`RunPhase`. Declared here because it is a vocabulary the webapp must agree on."""

    INIT = "init"
    ORIGIN = "origin"
    SCORING = "scoring"
    BETWEEN_SAMPLES = "between_samples"
    BETWEEN_CANDIDATES = "between_candidates"
    L1_GENERATE = "l1_generate"
    L2_REFINING = "l2_refining"
    L3_REPLANNING = "l3_replanning"
    ESCALATION = "escalation"
    STOPPED = "stopped"


class StopOutcome(enum.StrEnum):
    """``PAUSED`` is the **only non-terminal** outcome — the worker exited but the cycle keeps no
    ``finished_at``. Every terminal surface derives its label from this one table."""

    SUCCESS = "success"
    HALTED = "halted"
    FAILED = "failed"
    PAUSED = "paused"


class StopReasonInfo(NamedTuple):
    """``halts_mid_round`` means the round left on disk is PARTIAL — read as complete, its fitness
    is a handful of samples passing for the whole bank. No defaults: a new reason states both."""

    label: str
    outcome: StopOutcome
    halts_mid_round: bool
    has_traceback: bool


# The single source of truth mapping each terminal reason to its operator-facing
# label + outcome class + partial-round/traceback facts. Exhaustiveness is checked at
# import time below — adding a StopReason without a row here raises before the module loads.
#
# Mid-round is decided by WHERE the stop is raised, not by how bad it sounds:
#   - `scoring/query_loop.py` raises inside the per-sample loop -> SPEND_BUDGET, TOKEN_BUDGET.
#   - a pause returns from that same loop between samples -> PAUSED.
#   - CRASHED / RENDER_ERROR / OPTIMIZER_TIMEOUT are exceptions from anywhere, round included.
#   - everything else fires at a round BOUNDARY: `runner/round.py` raises only after
#     `close_round`, escalation's ABORT/REBASED ride the post-round transition seam,
#     ORIGIN_GATE runs once round 0 is scored, BACKEND_UNREACHABLE reads a CLOSED round's
#     verdict, and DIVERGED is decided at resume before any round starts.
STOP_REASON_INFO: dict[StopReason, StopReasonInfo] = {
    StopReason.PERFECT: StopReasonInfo("Perfect score", StopOutcome.SUCCESS, False, False),
    StopReason.TARGET_HIT: StopReasonInfo("Target reached", StopOutcome.SUCCESS, False, False),
    StopReason.MAX_ROUNDS: StopReasonInfo("Max rounds", StopOutcome.SUCCESS, False, False),
    StopReason.LIVES_EXHAUSTED: StopReasonInfo("Out of lives", StopOutcome.SUCCESS, False, False),
    StopReason.HARD_CAP: StopReasonInfo("Round cap", StopOutcome.SUCCESS, False, False),
    StopReason.SWEEP_COMPLETE: StopReasonInfo("Sweep complete", StopOutcome.SUCCESS, False, False),
    StopReason.DIAG_COMPLETE: StopReasonInfo(
        "Diagnostic complete", StopOutcome.SUCCESS, False, False
    ),
    StopReason.L3_PATIENCE: StopReasonInfo(
        "Converged (L3 patience)", StopOutcome.SUCCESS, False, False
    ),
    StopReason.REBASED: StopReasonInfo("Rebased to fork", StopOutcome.SUCCESS, False, False),
    StopReason.PAUSED: StopReasonInfo("Paused", StopOutcome.PAUSED, True, False),
    StopReason.ABORT: StopReasonInfo("Escalation abort", StopOutcome.HALTED, False, False),
    # The two the private or-chain missed: the budget gate stops INSIDE the sample loop.
    StopReason.SPEND_BUDGET: StopReasonInfo(
        "Spend budget reached", StopOutcome.HALTED, True, False
    ),
    StopReason.TOKEN_BUDGET: StopReasonInfo(
        "Token budget reached", StopOutcome.HALTED, True, False
    ),
    StopReason.ORIGIN_GATE: StopReasonInfo(
        "Origin gate (unhealthy origin)", StopOutcome.HALTED, False, False
    ),
    StopReason.BACKEND_UNREACHABLE: StopReasonInfo(
        "Backend unreachable", StopOutcome.HALTED, False, False
    ),
    StopReason.CRASHED: StopReasonInfo("Crashed", StopOutcome.FAILED, True, True),
    # Written by the REAPER straight onto index.json — the producer is already gone, so
    # `_finalize_run` never runs and never reads this row. True is the honest value: a
    # vanished process died at an arbitrary point, so whatever round was open is partial.
    StopReason.PRODUCER_VANISHED: StopReasonInfo(
        "Producer vanished", StopOutcome.FAILED, True, False
    ),
    StopReason.RENDER_ERROR: StopReasonInfo("Render error", StopOutcome.FAILED, True, True),
    StopReason.DIVERGED: StopReasonInfo("Diverged", StopOutcome.FAILED, False, False),
    StopReason.OPTIMIZER_TIMEOUT: StopReasonInfo(
        "Optimizer timeout", StopOutcome.FAILED, True, False
    ),
}

_missing_stop_info = set(StopReason) - set(STOP_REASON_INFO)
if _missing_stop_info:
    raise RuntimeError(
        f"STOP_REASON_INFO is missing rows for {sorted(r.value for r in _missing_stop_info)} — "
        "every StopReason needs a label + outcome (domain/phases.py)."
    )


def stop_reason_outcome(reason: StopReason | str) -> StopOutcome:
    return STOP_REASON_INFO[StopReason(reason)].outcome


class StopLoop(Exception):  # noqa: N818 — control-flow signal, not an error
    """Control-flow signal caught once at the top of the round loop."""

    def __init__(self, reason: StopReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


class PhaseEvent(StrictModel):
    model_config = ConfigDict(frozen=True)

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
    if on_phase is None:
        return
    on_phase(PhaseEvent(phase=phase, event=event, round=round, data=data))
