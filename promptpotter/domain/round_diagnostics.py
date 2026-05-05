"""RoundDiagnostics — typed post-scoring deterministics.

Lifted from inline ``compile_critique_context`` + ``_section_l1c_round_report``
+ scattered ``formatting.py`` helpers into one typed structure computed once
per round (in ``application/optimization/round_diagnostics.py``) and attached
to ``RoundResult``.

Pure data — rendering lives in the dispatch hub's ``diagnostics`` signal,
which is **layer-agnostic**: same renderer for every layer that subscribes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

TrajectoryClass = Literal["healthy", "oscillating", "plateau", "regressing", "ceiling"]


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
    changed_axes: list[str] = field(default_factory=list)


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
class ProbeOutcome:
    """Result of a probe round (warned-query subset re-run)."""

    axis_tested: str
    target_subset_size: int
    hit_rate: float
    delta_vs_full: float


@dataclass(frozen=True)
class RoundDiagnostics:
    """Post-scoring deterministics computed once per round.

    Renderers in the dispatch hub read this; they do not recompute. The
    plan field ``trajectory`` widens the existing 4-class model with
    ``ceiling`` (long-running stall at known best) for parity with
    :func:`build_trajectory_report`'s classification — the field type
    encodes which classifications the renderer must handle.
    """

    # Rank distribution — where does GT land in candidates?
    rank_buckets: dict[str, int] = field(default_factory=dict)
    top_k_accuracy: dict[int, float] = field(default_factory=dict)
    near_misses: list[NearMiss] = field(default_factory=list)
    near_miss_queries: frozenset[str] = field(default_factory=frozenset)
    n_valid: int = 0

    # Pipeline shape this round
    termination_dist: dict[str, int] = field(default_factory=dict)
    error_rate: float = 0.0
    warning_rate: float = 0.0

    # Failure shape this round
    failures_by_step: dict[str, int] = field(default_factory=dict)
    failures_by_warning: dict[str, list[str]] = field(default_factory=dict)

    # Cycle arc (cumulative across rounds)
    evolution_rows: list[EvolutionRow] = field(default_factory=list)
    trajectory: TrajectoryClass = "healthy"
    trajectory_description: str = ""
    plateau_count: int = 0
    anomalies: list[str] = field(default_factory=list)

    # Population this round
    cross_candidate_diff: list[str] = field(default_factory=list)
    l1_diversity: float = 1.0
    cache_share: float = 0.0
    prompt_chars: int = 0

    # Per-sample (used by L2 for tactical reasoning over actionable misses)
    samples: list[SampleDiag] = field(default_factory=list)

    # Probe-round footprint (when last round was a probe; else None)
    probe_outcome: ProbeOutcome | None = None
