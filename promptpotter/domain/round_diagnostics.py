"""Typed post-scoring deterministics, computed once per round and attached to ``RoundResult``. Pure data — rendering lives in the
dispatch hub's ``diagnostics`` signal, which is layer-agnostic.

**Tolerance is scoped by what a payload is FOR** — owned by
[`CLAUDE.md`](CLAUDE.md) § Tolerance is scoped by what a payload is FOR. Everything here is
reporting, so every field defaults and producers pass them all explicitly anyway."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# No "regressing" and no "oscillating": `round_analysis._trend` is the sole producer and returns
# neither. It classifies the ELECTION series — did this round clear the parent — and on that
# series "oscillating" is "elected before, not lately", which `plateau` already names.
#
# **Not "trajectory".** This is the election series CLASSIFIED — while a trajectory in this repo
# is a walk of points that each carry their own reading (`evidence.py::TrajectoryPoint`,
# `p_best_trajectory`, the Sample-trajectory grid). Two meanings under one word, and the reader
# could tell them apart from neither name.
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
    """``elected`` is the only field here comparable ACROSS rows. ``accuracy`` and its ``delta``
    are relative to the subset that round bought, and the acquisition re-picks that subset at the
    leader's own θ, so an unchanged prompt climbs on its own. Render the pair, never accuracy
    alone."""

    round: int = 0
    accuracy: float = 0.0
    delta: float = 0.0
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
    """Post-scoring deterministics computed once per round; renderers READ this and never recompute. The ``trend``
    field type encodes which classifications the renderer must handle."""

    # Rank distribution — where does GT land in candidates?
    rank_buckets: dict[str, int] = field(default_factory=dict)
    top_k_accuracy: dict[int, float] = field(default_factory=dict)
    near_misses: list[NearMiss] = field(default_factory=list)
    n_valid: int = 0

    # Pipeline shape this round. Add no `terminal_node` tally beside these: that field names the
    # deepest node a sample REACHED, so a healthy round tallies wholly under the last node and
    # reads as a mass failure. A run that stopped short is already carried by these two rates and
    # by `evidence_health`, and is derivable from `terminal_node` against the schema.
    error_rate: float = 0.0
    warning_rate: float = 0.0

    # Cycle arc (cumulative across rounds)
    evolution_rows: list[EvolutionRow] = field(default_factory=list)
    trend: TrendClass = "healthy"
    trend_description: str = ""
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
    "TrendClass",
]
