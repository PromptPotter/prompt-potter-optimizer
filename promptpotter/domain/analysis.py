"""Analysis and escalation data models.

Pure data containers for escalation signals + validation/runtime failures.
No I/O, no service dependencies.
"""

from __future__ import annotations

import enum
from dataclasses import asdict, dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Escalation types
# ---------------------------------------------------------------------------


class EscalationTarget(enum.StrEnum):
    """Where an escalation check directs the feedback cycle."""

    RETRY = "retry"
    L2 = "l2"
    L3 = "l3"
    ELIMINATE_CANDIDATE = "eliminate_candidate"
    LEADER_LOCKED = "leader_locked"
    ABORT_CAMPAIGN = "abort_campaign"


@dataclass
class EscalationSignal:
    """Signal emitted when an escalation check triggers mid-round."""

    check_name: str
    target: EscalationTarget
    check_result: dict[str, Any]
    candidate_idx: int
    candidates_scored: int
    candidates_skipped: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def is_elimination(self) -> bool:
        return self.target is EscalationTarget.ELIMINATE_CANDIDATE

    @property
    def is_leader_lock(self) -> bool:
        return self.target is EscalationTarget.LEADER_LOCKED

    @property
    def terminates_candidate(self) -> bool:
        """True iff this signal ends the candidate's run cleanly (cut or lock)."""
        return self.target in (
            EscalationTarget.ELIMINATE_CANDIDATE,
            EscalationTarget.LEADER_LOCKED,
        )

    @property
    def routes_to_optimizer(self) -> bool:
        """True iff the cycle layer should escalate to L2/L3 self-healing."""
        return self.target in (EscalationTarget.L2, EscalationTarget.L3)

    @property
    def is_abort(self) -> bool:
        return self.target is EscalationTarget.ABORT_CAMPAIGN


@dataclass
class ValidationFailure:
    """A parse-time invariant violation on an L1-generated candidate.

    Recorded as a property of the OptSearchPoint (in
    ``OptSearchPoint.validation_failures``). Drives the synthetic-0
    early exit in ``score_search_point()`` — see
    ``docs/developer/self-healing-internals.md`` for the rationale.
    """

    axis: str  # e.g. "llm_only.model"
    value: str  # the offending value the optimizer proposed
    allowed: list[str]  # the user-declared allowed set (may be empty)
    reason: str  # short machine-readable reason code

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RuntimeFailure:
    """A runtime-observed health failure on a candidate's scoring run.

    Sibling of ``ValidationFailure`` in the self-healing canon, but
    populated AFTER the candidate ran — from per-sample degradation
    evidence (e.g. a candidate with ``max_tokens=150`` that classifies
    as ``reasoning_budget_exhausted`` on 7/7 samples). Attributes
    the failure to the specific candidate that caused it, not the
    round, so winners are never penalised for losers' runtime issues.

    Stored on ``OptSearchPoint.runtime_failures``, surfaced in the
    candidate's score report, and ingested by L2 next round as a
    self-healing brief signal — mirroring the ValidationFailure
    pipeline. Does **not** drive synthetic-0: the candidate's real
    score stands (usually low anyway), the failure is a forensic
    attachment explaining *why*.
    """

    source: str  # e.g. "degradation_check" | "empty_output_check"
    dominant_warning: str  # e.g. "llm_only:reasoning_budget_exhausted"
    warning_types: dict[str, int]  # full histogram of warning types seen
    degraded_rate: float  # fraction of scored samples that degraded
    degraded_count: int
    total_scored: int
    observed_config: dict[str, Any]  # snapshot of the offending node's config
    # Round in which this failure was first observed. Used by
    # ``_r_runtime_failures`` (`application/optimization/dispatch_hub.py`)
    # to partition NEW (current round) vs ACCUMULATED (surviving earlier
    # rounds) from a single list.
    first_seen_round: int = 0
    # changes_description of the candidate that first produced this failure.
    # Propagated forward through the outer-memory mirror so ACCUMULATED rows
    # still identify which prompt variant introduced the pattern.
    candidate_label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
