"""Blocked, paired outer verdict — the statistically-rigorous L4 read of a round.

An L4 outer round scores each meta-prompt variant across the panel's cells (one inner
cycle per (variant, cell)). The **cached round-0 origin** is the within-panel control —
no config is ever re-measured mid-run. This computes, for the round's target variant,
the paired ``(variant − origin)`` composite difference per cell, pooled across cells into
an effect + CI, and a three-way decision. Cells are the blocks; the pooling treats them as
exchangeable (a flat paired posterior across cells — per-cell n is 1, so inverse-variance
weighting degenerates to this; a random-effects refinement is the documented next step).

Pure domain: no I/O. The projection (`round_summary`) reads the cached round-0 origin
cells off disk and passes them in; this module never touches the filesystem.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from promptpotter.shared.statistics import min_detectable_effect, paired_diff_posterior

DECISION_ADOPT = "adopt"
DECISION_REJECT = "reject"
DECISION_INCONCLUSIVE = "inconclusive"


class CandidateInfo(BaseModel):
    """The minimal per-candidate facts the verdict needs (no RoundResult import)."""

    model_config = ConfigDict(frozen=True)

    candidate_id: str
    label: str
    changes_description: str
    composite_fitness: float
    is_winner: bool


class OuterCellEffect(BaseModel):
    """One cell's paired (variant − origin) composite difference."""

    model_config = ConfigDict(frozen=True)

    cell: str
    variant_fitness: float
    origin_fitness: float
    diff: float


class OuterVerdict(BaseModel):
    """The pooled blocked-paired verdict for a round's target variant."""

    model_config = ConfigDict(frozen=True)

    variant_id: str
    variant_label: str
    per_cell: list[OuterCellEffect]
    effect: float  # pooled mean paired (variant − origin) across cells
    se: float
    ci_lo: float
    ci_hi: float
    n_cells: int
    decision: str  # adopt | reject | inconclusive
    mde_remaining: float  # min detectable effect at the current cell count


def cell_fitness(rows: list[dict[str, Any]]) -> dict[str, float]:
    """``{cell_query: mean composite_fitness}`` from a candidate's per-cell rows, averaging
    REPLICATE rows per cell (``replicate_survivors``) so the blocked-paired diff carries one
    point per cell at any replication depth. Identity with last-wins at the n=1 default.

    The one shared pure extraction — callers reading a fresh round (``compute_outer_verdict``
    below) and callers reading an archived round doc off disk
    (``application/meta_champion/reducer.py``) both walk the same row shape.
    """
    acc: dict[str, list[float]] = {}
    for r in rows:
        cell = r.get("query")
        fit = r.get("fitness")
        if isinstance(cell, str) and isinstance(fit, int | float):
            acc.setdefault(cell, []).append(float(fit))
    return {cell: sum(v) / len(v) for cell, v in acc.items()}


def _pick_variant(candidates: list[CandidateInfo], winner_label: str) -> CandidateInfo | None:
    """The variant the verdict scores: the round winner, else the strongest candidate by
    composite (so a round that crowned nothing still reports its best arm's verdict)."""
    if not candidates:
        return None
    winners = [c for c in candidates if c.is_winner]
    if winners:
        return winners[0]
    return max(candidates, key=lambda c: c.composite_fitness)


def compute_outer_verdict(
    all_candidate_results: dict[str, list[dict[str, Any]]],
    candidates: list[CandidateInfo],
    winner_label: str,
    origin_cells: dict[str, float],
) -> OuterVerdict | None:
    """The round's blocked-paired verdict against the **cached round-0 origin**
    (*origin_cells*, supplied by the caller — round 0 is never re-measured), or ``None``
    when there are no origin cells to pair against (a non-L4 round, or round 0 itself —
    the origin is the control, not a verdict subject)."""
    if not origin_cells:
        return None
    variant = _pick_variant(candidates, winner_label)
    if variant is None:
        return None
    var_cells = cell_fitness(all_candidate_results.get(variant.candidate_id, []))

    shared = sorted(c for c in var_cells if c in origin_cells)
    if not shared:
        return None
    per_cell = [
        OuterCellEffect(
            cell=c,
            variant_fitness=var_cells[c],
            origin_fitness=origin_cells[c],
            diff=var_cells[c] - origin_cells[c],
        )
        for c in shared
    ]
    effect, se, n = paired_diff_posterior(
        [var_cells[c] for c in shared], [origin_cells[c] for c in shared]
    )
    ci_lo, ci_hi = effect - 1.96 * se, effect + 1.96 * se
    if ci_lo > 0:
        decision = DECISION_ADOPT
    elif ci_hi < 0:
        decision = DECISION_REJECT
    else:
        decision = DECISION_INCONCLUSIVE
    return OuterVerdict(
        variant_id=variant.candidate_id,
        variant_label=variant.label,
        per_cell=per_cell,
        effect=effect,
        se=se,
        ci_lo=ci_lo,
        ci_hi=ci_hi,
        n_cells=n,
        decision=decision,
        mde_remaining=min_detectable_effect(n),
    )


__all__ = [
    "CandidateInfo",
    "OuterCellEffect",
    "OuterVerdict",
    "cell_fitness",
    "compute_outer_verdict",
]
