"""Analysis and escalation data models.

Pure data containers for failure patterns, query difficulty, and escalation
signals. No I/O, no service dependencies.
"""

from __future__ import annotations

import enum
from dataclasses import asdict, dataclass, field
from typing import Any, Literal


@dataclass
class FailurePattern:
    """A cluster of query failures sharing the same diagnostic signature."""

    name: str
    query_count: int
    fraction: float
    diagnostic_key: tuple[str, ...]
    example_queries: list[str] = field(default_factory=list)
    signals: dict[str, object] = field(default_factory=dict)


@dataclass
class FailureAnalysis:
    """Structured failure analysis over a set of evaluation results."""

    patterns: list[FailurePattern] = field(default_factory=list)
    total_failures: int = 0
    total_results: int = 0


DifficultyClass = Literal["easy", "discriminating", "hard", "dead"]


@dataclass
class QueryProfile:
    """Per-query difficulty profile across evaluations."""

    query: str
    hit_rate: float
    n_measurements: int
    classification: DifficultyClass


@dataclass
class QueryDifficulty:
    """Aggregate query difficulty classification."""

    profiles: list[QueryProfile] = field(default_factory=list)

    @property
    def easy(self) -> list[QueryProfile]:
        return [p for p in self.profiles if p.classification == "easy"]

    @property
    def discriminating(self) -> list[QueryProfile]:
        return [p for p in self.profiles if p.classification == "discriminating"]

    @property
    def hard(self) -> list[QueryProfile]:
        return [p for p in self.profiles if p.classification == "hard"]

    @property
    def dead(self) -> list[QueryProfile]:
        return [p for p in self.profiles if p.classification == "dead"]


# ---------------------------------------------------------------------------
# Escalation types
# ---------------------------------------------------------------------------


class EscalationTarget(enum.StrEnum):
    """Where an escalation check directs the feedback cycle."""

    RETRY = "retry"
    L2 = "l2"
    L3 = "l3"
    ELIMINATE_CANDIDATE = "eliminate_candidate"
    ABORT_CAMPAIGN = "abort_campaign"


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
class ValidationFailure:
    """A parse-time invariant violation on an L1-generated candidate.

    Recorded as a property of the OptSearchPoint (in
    ``OptimizationMemory.validation_failures``). Drives the synthetic-0
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
    """A runtime-observed health failure on a candidate's evaluation.

    Sibling of ``ValidationFailure`` on the self-healing rail, but
    populated AFTER the candidate ran — from per-query degradation
    evidence (e.g. a candidate with ``max_tokens=150`` that produces
    ``empty_content_reasoning_fallback`` on 7/7 queries). Attributes
    the failure to the specific candidate that caused it, not the
    round, so winners are never penalised for losers' runtime issues.

    Stored on ``OptimizationMemory.runtime_failures``, surfaced in the
    candidate's score report, and ingested by L2 next round as a
    self-healing directive signal — mirroring the ValidationFailure
    pipeline. Does **not** drive synthetic-0: the candidate's real
    score stands (usually low anyway), the failure is a forensic
    attachment explaining *why*.
    """

    source: str  # e.g. "degradation_check" | "empty_output_check"
    dominant_warning: str  # e.g. "llm_only:empty_content_reasoning_fallback"
    warning_types: dict[str, int]  # full histogram of warning types seen
    degraded_rate: float  # fraction of scored queries that degraded
    degraded_count: int
    total_evaluated: int
    observed_config: dict[str, Any]  # snapshot of the offending node's config
    # Round in which this failure was first observed. Used by the L2 inbox
    # renderer (``inbox_registry._r_runtime_failures_l2``) to partition NEW
    # (current round) vs ACCUMULATED (surviving earlier rounds) from a
    # single list.
    first_seen_round: int = 0
    # changes_description of the candidate that first produced this failure.
    # Propagated forward through the outer-memory mirror so ACCUMULATED rows
    # still identify which prompt variant introduced the pattern.
    candidate_label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
