"""Escalation data types shared across campaign and search packages.

Pure data types with no service-level dependencies.  Execution logic
(L2/L3 transitions, degradation checks) stays in
``services.campaign.escalation``.
"""

from __future__ import annotations

import enum
from dataclasses import asdict, dataclass
from typing import Any

__all__ = [
    "DEFAULT_STRATEGIES",
    "EscalationSignal",
    "EscalationStrategy",
    "EscalationTarget",
]


class EscalationTarget(enum.StrEnum):
    """Where an escalation check directs the feedback cycle."""

    RETRY = "retry"
    L2 = "l2"
    L3 = "l3"
    ABORT = "abort"


@dataclass
class EscalationSignal:
    """Signal emitted when an EscalationCheck triggers mid-evaluation."""

    check_name: str
    target: EscalationTarget
    check_result: dict[str, Any]
    candidate_idx: int
    candidates_scored: int
    candidates_skipped: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EscalationStrategy:
    """Configurable response to a specific warning type."""

    target: EscalationTarget = EscalationTarget.L2


DEFAULT_STRATEGIES: dict[str, EscalationStrategy] = {
    "web_search:low_document_count": EscalationStrategy(target=EscalationTarget.L2),
}
