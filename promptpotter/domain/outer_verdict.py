"""Blocked, paired outer verdict — the statistically-rigorous L4 read of a round.

An L4 outer round scores each meta-prompt variant across the panel's cells (one inner
cycle per (variant, cell)). The no-op probe is the within-panel control. This computes,
for the round's target variant, the paired ``(variant − noop)`` composite difference per
cell, pooled across cells into an effect + CI, and a three-way decision. Cells are the
blocks; the pooling treats them as exchangeable (a flat paired posterior across cells —
per-cell n is 1, so inverse-variance weighting degenerates to this; a random-effects
refinement is the documented next step).

Pure domain: no I/O. The projection (`round_summary`) builds the inputs from a
`RoundResult` and copies the result onto the summary, exactly like `health`.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from promptpotter.shared.statistics import min_detectable_effect, paired_diff_posterior

# The verbatim marker on the no-op probe's changes_description (l1/generate.py).
NOOP_MARKER = "NO-OP probe"

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
    """One cell's paired (variant − noop) composite difference."""

    model_config = ConfigDict(frozen=True)

    cell: str
    variant_fitness: float
    noop_fitness: float
    diff: float


class OuterVerdict(BaseModel):
    """The pooled blocked-paired verdict for a round's target variant."""

    model_config = ConfigDict(frozen=True)

    variant_id: str
    variant_label: str
    per_cell: list[OuterCellEffect]
    effect: float  # pooled mean paired (variant − noop) across cells
    se: float
    ci_lo: float
    ci_hi: float
    n_cells: int
    decision: str  # adopt | reject | inconclusive
    mde_remaining: float  # min detectable effect at the current cell count


def _cell_fitness(rows: list[dict[str, Any]]) -> dict[str, float]:
    """``{cell_query: composite_fitness}`` from a candidate's per-cell rows."""
    out: dict[str, float] = {}
    for r in rows:
        cell = r.get("query")
        fit = r.get("fitness")
        if isinstance(cell, str) and isinstance(fit, int | float):
            out[cell] = float(fit)
    return out


def _pick_variant(
    candidates: list[CandidateInfo], noop_id: str, winner_label: str
) -> CandidateInfo | None:
    """The variant the verdict scores: the round winner if it isn't the noop, else the
    strongest non-noop candidate by composite (so a round that crowned nothing still
    reports its best arm's verdict)."""
    non_noop = [c for c in candidates if c.candidate_id != noop_id]
    if not non_noop:
        return None
    winners = [c for c in non_noop if c.is_winner]
    if winners:
        return winners[0]
    return max(non_noop, key=lambda c: c.composite_fitness)


def compute_outer_verdict(
    all_candidate_results: dict[str, list[dict[str, Any]]],
    candidates: list[CandidateInfo],
    winner_label: str,
) -> OuterVerdict | None:
    """The round's blocked-paired verdict, or ``None`` when there is no no-op arm to
    pair against (i.e. a non-L4 round, or a round with no probe)."""
    noop = next((c for c in candidates if NOOP_MARKER in c.changes_description), None)
    if noop is None:
        return None
    noop_cells = _cell_fitness(all_candidate_results.get(noop.candidate_id, []))
    if not noop_cells:
        return None
    variant = _pick_variant(candidates, noop.candidate_id, winner_label)
    if variant is None:
        return None
    var_cells = _cell_fitness(all_candidate_results.get(variant.candidate_id, []))

    shared = sorted(c for c in var_cells if c in noop_cells)
    if not shared:
        return None
    per_cell = [
        OuterCellEffect(
            cell=c,
            variant_fitness=var_cells[c],
            noop_fitness=noop_cells[c],
            diff=var_cells[c] - noop_cells[c],
        )
        for c in shared
    ]
    effect, se, n = paired_diff_posterior(
        [var_cells[c] for c in shared], [noop_cells[c] for c in shared]
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
    "compute_outer_verdict",
]
