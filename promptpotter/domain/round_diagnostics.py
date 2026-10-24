"""Reporting rows: every field defaults (`CLAUDE.md` § Tolerance is scoped by what a payload is FOR)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class NearMiss:
    """A query whose ground truth landed in candidates rank 2-10."""

    query: str = ""
    ground_truth: str = ""
    rank: int = 0
    predicted: str = ""


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

    samples: list[SampleDiag] = field(default_factory=list)
