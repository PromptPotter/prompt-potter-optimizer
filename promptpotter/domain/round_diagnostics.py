"""RoundDiagnostics — typed post-scoring deterministics.

Lifted from inline ``compile_critique_context`` + ``_section_l1c_round_report``
+ scattered ``formatting.py`` helpers into one typed structure computed once
per round (in ``application/optimization/round_analysis.py``) and attached
to ``RoundResult``.

Pure data — rendering lives in the dispatch hub's ``diagnostics`` signal,
which is **layer-agnostic**: same renderer for every layer that subscribes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# No "regressing": `round_analysis._trajectory` is the sole producer and never returns it
# (it computes a `regressions` counter, but only as an input to the OSCILLATING test).
TrajectoryClass = Literal["healthy", "oscillating", "plateau", "ceiling"]


@dataclass(frozen=True)
class NearMiss:
    """A query whose ground truth landed in candidates rank 2-10."""

    query: str
    ground_truth: str
    rank: int
    predicted: str


@dataclass(frozen=True)
class EvolutionRow:
    """One row of the cycle's accuracy-over-rounds table."""

    round: int
    accuracy: float
    delta: float
    degraded: int
    n_candidates: int


@dataclass(frozen=True)
class SampleDiag:
    """Per-sample diagnostics surfaced for tactical reasoning (L2)."""

    query: str
    ground_truth: str
    predicted: str
    rank: int | None
    terminated_at: str
    gt_in_source: bool | None
    gt_in_ranked: bool | None
    warnings: list[str]
    hit: bool


@dataclass(frozen=True)
class RoundDiagnostics:
    """Post-scoring deterministics computed once per round.

    Renderers in the dispatch hub read this; they do not recompute. The
    plan field ``trajectory`` widens the existing 4-class model with
    ``ceiling`` (long-running stall at known best) — the field type
    encodes which classifications the renderer must handle. The classifier
    itself is ``optimization/round_analysis.py::_trajectory``.
    """

    # Rank distribution — where does GT land in candidates?
    rank_buckets: dict[str, int] = field(default_factory=dict)
    top_k_accuracy: dict[int, float] = field(default_factory=dict)
    near_misses: list[NearMiss] = field(default_factory=list)
    n_valid: int = 0

    # Pipeline shape this round
    termination_dist: dict[str, int] = field(default_factory=dict)
    error_rate: float = 0.0
    warning_rate: float = 0.0

    # Cycle arc (cumulative across rounds)
    evolution_rows: list[EvolutionRow] = field(default_factory=list)
    trajectory: TrajectoryClass = "healthy"
    trajectory_description: str = ""
    anomalies: list[str] = field(default_factory=list)

    # Population this round
    cross_candidate_diff: list[str] = field(default_factory=list)
    l1_diversity: float = 1.0

    # Per-sample (used by L2 for tactical reasoning over actionable misses)
    samples: list[SampleDiag] = field(default_factory=list)


__all__ = [
    "EvolutionRow",
    "NearMiss",
    "RoundDiagnostics",
    "SampleDiag",
    "TrajectoryClass",
]
