"""The round's blocked-paired verdict on an optimizer prompt variant — the paired
``(variant − origin)`` composite diff per cell, pooled across cells into an effect + CI."""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, Field

from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.errors import is_error_result
from promptpotter.shared.statistics import (
    min_detectable_effect,
    paired_diff_posterior,
    t_critical,
)

DECISION_ADOPT = "adopt"
DECISION_REJECT = "reject"
DECISION_INCONCLUSIVE = "inconclusive"


class CandidateInfo(StrictModel):
    """The minimal per-candidate facts the verdict needs (no ``RoundResult`` import).
    Carries only arms the round could READ; admission is the caller's, via ``is_electable``."""

    model_config = ConfigDict(frozen=True)

    candidate_id: str
    label: str
    changes_description: str
    composite_fitness: float
    is_winner: bool


class OuterCellEffect(StrictModel):
    """One cell's paired (variant − origin) composite difference.
    ``variant_fitness`` / ``origin_fitness`` have no reader yet — but ``diff`` is lossy, so they stay."""

    model_config = ConfigDict(frozen=True)

    cell: str
    variant_fitness: float
    origin_fitness: float
    diff: float


class OuterVariance(StrictModel):
    """Where the panel's spread comes from — estimation noise, or real between-cell difference.
    A diagnostic in θ logits, never the fitness scale; it does not enter the decision."""

    model_config = ConfigDict(frozen=True)

    # sqrt(mean over cells of (se_variant² + se_origin²)) — the spread the panel would show if
    # every cell were measuring the SAME true value and differed only by measurement error.
    estimation_sd: float
    # The sample SD of the per-cell paired diffs. Contains estimation_sd plus any real spread.
    observed_sd: float
    # estimation_sd² / observed_sd², clamped to [0, 1]. Near 1 ⇒ the panel is reading its own
    # noise and more cells will not help; near 0 ⇒ the cells genuinely differ and the spread is
    # signal about where this optimizer prompt works.
    estimation_share: float
    n_cells: int


class OuterVerdict(StrictModel):
    """The pooled blocked-paired verdict for a round's target variant.
    ``variant_id`` / ``variant_label`` NAME the subject — strip them and it measures nothing."""

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
    mde_remaining: float  # smallest effect this panel could still resolve, from `se`
    # False when the round crowned nobody and this verdict reports the BEST-SCORING arm instead.
    # The distinction is load-bearing: `variant_id` names the subject, and a reader must be able
    # to tell "the round adopted this and here is the evidence" from "the round adopted nothing,
    # and here is what the closest arm actually measured".
    variant_is_winner: bool
    # ``None`` until both arms carry per-cell precision, or below two shared cells (one cell has
    # no spread to decompose). Absent is honest; a fabricated 0.0 would read as "all signal".
    variance: OuterVariance | None = None
    # Cells either arm attempted that produced no measurement, so no pair could form. The panel
    # is a census (`_verify_outer_panel_contract`), so `n_cells` alone cannot say whether the
    # panel was small or the round lost cells. Ids rather than a count: the id is what an
    # operator re-measures, and `dashboard.json` carries no rows to recover it from.
    cells_dropped: list[str] = Field(default_factory=list)


def cell_fitness(rows: list[dict[str, Any]]) -> dict[str, float]:
    """``{cell_query: mean composite_fitness}`` — one point per cell however many rows it holds, so
    the paired diff carries one point either way. Errored rows drop — a timeout measured nothing."""
    acc: dict[str, list[float]] = {}
    for r in rows:
        if is_error_result(r):
            continue
        cell = r.get("query")
        fit = r.get("fitness")
        if isinstance(cell, str) and isinstance(fit, int | float):
            acc.setdefault(cell, []).append(float(fit))
    return {cell: sum(v) / len(v) for cell, v in acc.items()}


def cell_measurand(rows: list[dict[str, Any]], key: str) -> dict[str, tuple[float, float]]:
    """``{cell: (measurand, its within-cell SE)}`` on the measurand's OWN scale. ``key`` is passed
    in, not imported: ``domain.results`` imports this module, so reading it here closes a cycle."""
    acc: dict[str, list[tuple[float, float]]] = {}
    for r in rows:
        cell, pd = r.get("query"), r.get("pipeline_data")
        if not isinstance(cell, str) or not isinstance(pd, dict):
            continue
        val, se = pd.get(key), pd.get(f"{key}_se")
        if isinstance(val, int | float) and isinstance(se, int | float):
            acc.setdefault(cell, []).append((float(val), float(se)))
    return {
        cell: (sum(v for v, _ in vs) / len(vs), sum(s for _, s in vs) / len(vs))
        for cell, vs in acc.items()
    }


def _variance_split(
    variant: dict[str, tuple[float, float]],
    origin: dict[str, tuple[float, float]],
    shared: list[str],
) -> OuterVariance | None:
    cells = [c for c in shared if c in variant and c in origin]
    if len(cells) < 2:
        return None
    diffs = [variant[c][0] - origin[c][0] for c in cells]
    mean = sum(diffs) / len(diffs)
    # Sample SD (n-1): the mean is estimated from these same points.
    observed = (sum((d - mean) ** 2 for d in diffs) / (len(diffs) - 1)) ** 0.5
    # The two arms are separate inner campaigns on the same cell, so their errors add in
    # quadrature. This is the diff's error, not one arm's — halving it would understate.
    estimation = (sum(variant[c][1] ** 2 + origin[c][1] ** 2 for c in cells) / len(cells)) ** 0.5
    share = 1.0 if observed <= 0.0 else min(1.0, (estimation / observed) ** 2)
    return OuterVariance(
        estimation_sd=float(estimation),
        observed_sd=float(observed),
        estimation_share=float(share),
        n_cells=len(cells),
    )


def _pick_variant(candidates: list[CandidateInfo]) -> tuple[CandidateInfo, bool] | None:
    """The variant the verdict scores: the round's elected winner, else its best arm — a round that
    crowns nothing is "inconclusive", which reporting nothing cannot distinguish from unmeasured."""
    if (winner := next((c for c in candidates if c.is_winner), None)) is not None:
        return (winner, True)
    if not candidates:
        return None
    return (max(candidates, key=lambda c: c.composite_fitness), False)


def compute_outer_verdict(
    all_candidate_results: dict[str, list[dict[str, Any]]],
    candidates: list[CandidateInfo],
    origin_rows: list[dict[str, Any]],
    *,
    measurand_key: str = "",
) -> OuterVerdict | None:
    """Pairs against the CACHED round-0 origin (round 0 is never re-measured); ``None`` when there
    are no origin cells or no readable arm. Takes the origin ROWS: the law needs two readings."""
    origin_cells = cell_fitness(origin_rows)
    if not origin_cells:
        return None
    picked = _pick_variant(candidates)
    if picked is None:
        return None
    variant, variant_is_winner = picked
    variant_rows = all_candidate_results.get(variant.candidate_id, [])
    var_cells = cell_fitness(variant_rows)

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
    # Student-t, not z: the SE is estimated from the same ~7 paired cells it widens. At a
    # single shared cell there is no df — treat as df=1, which is already indecisive.
    crit = t_critical(max(n - 1, 1))
    ci_lo, ci_hi = effect - crit * se, effect + crit * se
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
        mde_remaining=min_detectable_effect(se),
        variant_is_winner=variant_is_winner,
        cells_dropped=sorted(
            (
                {r["query"] for r in variant_rows if isinstance(r.get("query"), str)}
                | {r["query"] for r in origin_rows if isinstance(r.get("query"), str)}
            )
            - set(shared)
        ),
        variance=(
            _variance_split(
                cell_measurand(variant_rows, measurand_key),
                cell_measurand(origin_rows, measurand_key),
                shared,
            )
            if measurand_key
            else None
        ),
    )


__all__ = [
    "CandidateInfo",
    "OuterCellEffect",
    "OuterVariance",
    "OuterVerdict",
    "cell_fitness",
    "compute_outer_verdict",
]
