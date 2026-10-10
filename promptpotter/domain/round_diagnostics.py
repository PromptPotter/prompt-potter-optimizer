"""Reporting rows: every field defaults (`CLAUDE.md` § Tolerance is scoped by what a payload is FOR)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# The ELECTION series classified (did the round clear the parent), not a trajectory of readings.
TrendClass = Literal["healthy", "plateau", "ceiling"]


@dataclass(frozen=True)
class NearMiss:
    """A query whose ground truth landed in candidates rank 2-10."""

    query: str = ""
    ground_truth: str = ""
    rank: int = 0
    predicted: str = ""


@dataclass(frozen=True)
class EvolutionRow:
    """Only ``elected`` compares across rows; ``accuracy`` is relative to its round's subset."""

    round: int = 0
    # ``None``, with ``delta``, where the round measured nothing readable: a gap, not a flat stretch.
    accuracy: float | None = None
    delta: float | None = None
    degraded: int = 0
    n_candidates: int = 0
    elected: bool = False


@dataclass(frozen=True)
class SampleDiag:
    query: str = ""
    ground_truth: str = ""
    predicted: str = ""
    rank: int | None = None
    terminal_node: str = ""
    gt_in_source: bool | None = None
    gt_in_ranked: bool | None = None
    warnings: list[str] = field(default_factory=list)
    fitness: float = 0.0


@dataclass(frozen=True)
class RoundDiagnostics:
    """One round's post-scoring diagnostics, computed once and read by every renderer."""

    rank_buckets: dict[str, int] = field(default_factory=dict)
    top_k_accuracy: dict[int, float] = field(default_factory=dict)
    near_misses: list[NearMiss] = field(default_factory=list)
    n_valid: int = 0

    # No `terminal_node` tally: the deepest node REACHED reads as that node failing en masse.
    error_rate: float = 0.0
    warning_rate: float = 0.0

    evolution_rows: list[EvolutionRow] = field(default_factory=list)
    trend: TrendClass = "healthy"
    trend_description: str = ""
    anomalies: list[str] = field(default_factory=list)

    cross_candidate_diff: list[str] = field(default_factory=list)

    samples: list[SampleDiag] = field(default_factory=list)


__all__ = [
    "EvolutionRow",
    "NearMiss",
    "RoundDiagnostics",
    "SampleDiag",
    "TrendClass",
]
