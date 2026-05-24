"""Escalation signals + self-healing failure types — pure data, no I/O."""

from __future__ import annotations

import enum
from dataclasses import asdict, dataclass
from typing import Any


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
    def routes_to_optimizer(self) -> bool:
        """True iff the cycle layer should escalate to L2/L3 self-healing."""
        return self.target in (EscalationTarget.L2, EscalationTarget.L3)

    @property
    def is_abort(self) -> bool:
        return self.target is EscalationTarget.ABORT_CAMPAIGN


@dataclass
class ValidationFailure:
    """L1-output parse-time invariant violation. Drives synthetic-0 in ``score_search_point``."""

    axis: str
    value: str
    allowed: list[str]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RuntimeFailure:
    """Post-eval degradation evidence, attached per-candidate.

    Stored on ``OptSearchPoint.wounds.runtime_failures``; surfaced in the
    candidate's score report; ingested by L2 next round. Does NOT drive
    synthetic-0 — the candidate's real score stands.
    """

    source: str
    dominant_warning: str
    warning_types: dict[str, int]
    degraded_rate: float
    degraded_count: int
    total_scored: int
    observed_config: dict[str, Any]
    first_seen_round: int = 0
    candidate_label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


__all__ = ["EscalationSignal", "EscalationTarget", "RuntimeFailure", "ValidationFailure"]
