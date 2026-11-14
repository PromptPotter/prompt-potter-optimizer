"""Reporting rows: every field required, ``None`` where the round holds no answer to it."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NearMiss:
    """A query whose ground truth landed in candidates rank 2-10."""

    query: str
    ground_truth: str
    rank: int
    predicted: str


@dataclass(frozen=True)
class SampleDiag:
    query: str
    ground_truth: str
    predicted: str
    rank: int | None
    terminal_node: str
    gt_in_source: bool | None
    gt_in_ranked: bool | None
    warnings: list[str]
    fitness: float


@dataclass(frozen=True)
class RoundDiagnostics:
    """One round's post-scoring diagnostics, computed once and read by every renderer."""

    rank_buckets: dict[str, int]
    top_k_accuracy: dict[int, float]
    near_misses: list[NearMiss]
    n_valid: int

    # No `terminal_node` tally: the deepest node REACHED reads as that node failing en masse.
    error_rate: float
    warning_rate: float

    samples: list[SampleDiag]
