"""Typed post-scoring deterministics, computed once per round and attached to ``RoundResult``. Pure data — rendering lives in the
dispatch hub's ``diagnostics`` signal, which is layer-agnostic.

**Every field below defaults, and that is the contract, not laziness.** These are REPORTING
payloads read back off disk — ``RoundResult.model_dump()`` IS the round document — and nothing
gates, scores or escalates on them. So a field that loses its name must DEGRADE, never raise:
otherwise one nested rename makes a paid measurement unreadable, which is the exact failure the
document's ``extra="ignore"`` exists to prevent, and which that setting cannot reach on its own
(it forgives an extra key, not a missing one). The SCORING payloads beside them on the same
document — ``ScoredCandidate``, ``EscalationSignal`` — stay REQUIRED for the opposite reason: a
missing field there means the record cannot be vouched for, and loading it blind would trade a
loud failure for a silent wrong number. Producers pass every field explicitly; these defaults
serve the read path alone."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# No "regressing": `round_analysis._trajectory` is the sole producer and never returns it
# (it computes a `regressions` counter, but only as an input to the OSCILLATING test).
TrajectoryClass = Literal["healthy", "oscillating", "plateau", "ceiling"]


@dataclass(frozen=True)
class NearMiss:
    """A query whose ground truth landed in candidates rank 2-10."""

    query: str = ""
    ground_truth: str = ""
    rank: int = 0
    predicted: str = ""


@dataclass(frozen=True)
class EvolutionRow:
    round: int = 0
    accuracy: float = 0.0
    delta: float = 0.0
    degraded: int = 0
    n_candidates: int = 0


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
    """Post-scoring deterministics computed once per round; renderers READ this and never recompute. The ``trajectory``
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
