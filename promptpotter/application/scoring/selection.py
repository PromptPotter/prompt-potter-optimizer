"""The live election and both replayers read the parent it RECORDED (``parent_cells``); none reconstructs one."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import TYPE_CHECKING, Any, NamedTuple

from promptpotter.application.intelligence.rasch import (
    candidate_abilities,
    fit_theta,
    graded_response,
    theta_bounds_given_delta,
    theta_lift_over_parent,
)
from promptpotter.domain.scoring import CellSheet, Grade, GradedCell, MeasuredCell
from promptpotter.shared.statistics import (
    discordant_counts,
    mean_ci,
    p_exceeds,
    sign_posterior,
)

if TYPE_CHECKING:
    from promptpotter.application.intelligence.rasch import RoundAbilities
    from promptpotter.domain.results import RoundResult
    from promptpotter.domain.ruler import DeltaRuler
    from promptpotter.domain.scoring import GradeColumn

__all__ = [
    "PairedPosterior",
    "closest_to_bar",
    "distinct_valid_cells",
    "elect_round_winner",
    "elimination_p_best",
    "elimination_p_best_bounds",
    "level_band",
    "lift_over_bar",
    "paired_p_best",
    "parent_cells",
    "parent_selection_bias",
    "priors_covering",
    "readable_lifts",
    "recorded_parent",
]


def level_band(
    sheet: CellSheet, column: GradeColumn
) -> tuple[float | None, float | None, float | None]:
    values = list(sheet.column(column).values())
    if not values:
        return (None, None, None)
    level = sum(values) / len(values)
    band = mean_ci(values)
    if band is None:
        return (level, None, None)
    _, ci_lo, ci_hi = band
    # Clipped to the metric's support: the SE floor gives an all-0.0 arm a band reaching below zero.
    return (level, min(max(ci_lo, 0.0), 1.0), min(max(ci_hi, 0.0), 1.0))


def distinct_valid_cells(sheet: CellSheet) -> int:
    return len(sheet.scoreable)


def parent_cells(parent_results: CellSheet) -> list[dict[str, Any]]:
    """θ reads ``objective``, the paired lift ``fitness``; an absent ``objective`` stays absent, never a miss."""
    return [
        {
            "sample_id": cell.sample_id,
            "fitness": cell.grade.fitness,
            "error_category": cell.facts.error_category,
            **({"objective": cell.grade.objective} if cell.grade.objective is not None else {}),
        }
        for cell in parent_results
    ]


def recorded_parent(cells: Sequence[dict[str, Any]]) -> CellSheet:
    """Each grade stands AS RECORDED: the sheet names no scorer and is never re-graded."""
    return CellSheet("", tuple(_recorded_cell(row) for row in cells))


def _recorded_cell(row: Mapping[str, Any]) -> GradedCell:
    facts = MeasuredCell.from_wire(row)
    grade = Grade(row.get("fitness"), row.get("objective"), None)
    return GradedCell(facts, grade, not facts.errored)


# E[max of k standard normals], k = 1..6; the last entry stands for any wider round.
_EXPECTED_MAX_Z: tuple[float, ...] = (0.0, 0.0, 0.5642, 0.8463, 1.0294, 1.1630, 1.2672)


def parent_selection_bias(rounds: Sequence[RoundResult]) -> float:
    """A winner is the MAX over its round's arms, so its θ carries the largest noise draw; corrects the BAR only."""
    for rr in reversed(rounds):
        if not rr.selected_labels:
            continue
        winner = next(iter(rr.selected_scores), None)
        se = winner.theta_se if winner else None
        if not se:
            return 0.0
        k = min(max(rr.electable_count, 1), len(_EXPECTED_MAX_Z) - 1)
        # ONE arm's SE, not the paired √2 one: θ̂_parent is common to all k comparisons.
        return _EXPECTED_MAX_Z[k] * se
    return 0.0


def lift_over_bar(
    abilities: RoundAbilities, candidate_id: str, parent_bias: float
) -> tuple[float, float] | None:
    """``(θ lift, bias credit)``; the credit scales by SE_parent/SE_arm, a flat one favouring the noisiest arm."""
    lift = theta_lift_over_parent(abilities, candidate_id)
    if lift is None:
        return None
    se_parent = abilities.parent[1] if abilities.parent is not None else 0.0
    se_cand = abilities.theta_se.get(candidate_id) or 0.0
    return lift, parent_bias * (min(1.0, se_parent / se_cand) if se_parent and se_cand else 1.0)


def readable_lifts(
    candidate_ids: Sequence[str],
    results_by_id: Mapping[str, CellSheet],
    parent_results: CellSheet,
    coverage_floor: int,
    abilities: RoundAbilities,
    parent_bias: float,
) -> dict[str, tuple[float, float]]:
    reads: dict[str, tuple[float, float]] = {}
    for cid in candidate_ids:
        cand_results = results_by_id.get(cid)
        # An arm thin for a reason OTHER than elimination (an operator skip); PoBB's `n_min` IS this floor.
        if cand_results is None or distinct_valid_cells(cand_results) < coverage_floor:
            continue
        # About the WALK, not a statistic: an errored row still shares the cell, where the θ fit drops it.
        if not cand_results.on_ruler.keys() & parent_results.on_ruler.keys():
            continue
        read = lift_over_bar(abilities, cid, parent_bias)
        if read is not None:
            reads[cid] = read
    return reads


def closest_to_bar(reads: Mapping[str, tuple[float, float]], *, beside: str = "") -> str:
    """On the raw θ lift ADMISSION takes, never lift plus credit; the first walked holds a tie."""
    return max((cid for cid in reads if cid != beside), key=lambda cid: reads[cid][0], default="")


def elect_round_winner(
    candidate_ids: list[str],
    results_by_id: Mapping[str, CellSheet],
    parent_results: CellSheet,
    coverage_floor: int,
    ruler: DeltaRuler | None,
    *,
    parent_bias: float,
) -> tuple[str, RoundAbilities]:
    """ADMISSION is raw θ strictly above the parent's; the RANK is ``P(θ_cand > θ_parent)``, which PoBB cuts on."""

    abilities = candidate_abilities(
        {cid: results_by_id[cid] for cid in candidate_ids if cid in results_by_id},
        parent_results,
        ruler,
    )

    theta_parent, se_parent = abilities.parent or (None, 0.0)
    # The bar is what the parent can DO, not the draw that crowned it (`parent_selection_bias`).
    if theta_parent is not None:
        theta_parent -= parent_bias

    best_rank: tuple[float, int] = (0.0, 0)
    winner_id = ""
    reads = readable_lifts(
        candidate_ids, results_by_id, parent_results, coverage_floor, abilities, parent_bias
    )
    for cid, read in reads.items():
        # No SE margin: subtracting one turns a wide-posterior gain negative; uncertainty is the RANK's.
        if read[0] <= 0.0:
            continue
        theta_c = abilities.theta.get(cid)
        if theta_c is None or theta_parent is None:
            continue
        p_better = p_exceeds(theta_c, abilities.theta_se.get(cid) or 0.0, theta_parent, se_parent)
        rank = (p_better, distinct_valid_cells(results_by_id[cid]))
        if rank > best_rank:
            best_rank = rank
            winner_id = cid
    return winner_id, abilities


def elimination_p_best(
    candidate_grades: Sequence[float],
    paired_prior_grades: Mapping[str, Sequence[float]],
    candidate_sample_ids: Sequence[int],
    ruler: DeltaRuler | None,
) -> tuple[float, dict[str, float]]:
    """A stopping rule, not a verdict: ``P(θ_cand > θ_prior)`` CAPPED by what the discordant pairs support."""
    if not paired_prior_grades:
        return 1.0, {}

    sids = [int(s) for s in candidate_sample_ids]
    # The one sanctioned provisional δ: cells the ruler has not absorbed stand at its centre for BOTH arms.
    entries = ruler.entries_covering(sids) if ruler is not None else None

    def read(grades: Sequence[float]) -> tuple[float, float]:
        on_cells = {sid: float(g) for sid, g in zip(sids, grades, strict=True)}
        fitted = fit_theta(on_cells, entries)
        if fitted is None:
            raise ValueError("elimination_p_best: a reading needs at least one graded cell")
        return fitted

    theta_c, se_c = read(candidate_grades)

    per_prior: dict[str, float] = {}
    for pid, grades in paired_prior_grades.items():
        theta_p, se_p = read(grades)
        p = p_exceeds(theta_c, se_c, theta_p, se_p)
        per_prior[pid] = _capped(p, sign_posterior(*discordant_counts(candidate_grades, grades)))
    return min(per_prior.values()), per_prior


def priors_covering[P: Mapping[int, float]](
    priors: Mapping[str, P], cells: Collection[int]
) -> dict[str, P]:
    return {pid: grades for pid, grades in priors.items() if all(c in grades for c in cells)}


class PairedPosterior(NamedTuple):
    cells: list[int]
    grades: list[float]
    priors: dict[str, list[float]]
    p_best: float
    p_better: dict[str, float]


def paired_p_best(
    rows: Sequence[GradedCell],
    priors: Mapping[str, Mapping[int, float]],
    ruler: DeltaRuler | None,
) -> PairedPosterior | None:
    """Over rows carrying a verdict only: a backend error is no evidence of inability."""
    graded = [cell for cell in rows if cell.scored]
    cells = [cell.ruler_key for cell in graded]
    paired = {
        pid: [grades[c] for c in cells] for pid, grades in priors_covering(priors, cells).items()
    }
    if not graded or not paired:
        return None
    own = [graded_response(cell) for cell in graded]
    p_best, p_better = elimination_p_best(own, paired, cells, ruler)
    return PairedPosterior(cells, own, paired, p_best, p_better)


def _capped(p: float, bound: float) -> float:
    # The bound's mass on the SIDE θ read, not `|bound - 0.5|`, which reads 3 losses as 3 wins.
    support = bound if p > 0.5 else 1.0 - bound
    reach = min(abs(p - 0.5), max(0.0, support - 0.5))
    return 0.5 + reach if p > 0.5 else 0.5 - reach


def elimination_p_best_bounds(
    cells: Sequence[int],
    candidate: Mapping[int, float],
    priors: Mapping[str, Mapping[int, float]],
    ruler: DeltaRuler | None,
    *,
    settled: Collection[str],
) -> tuple[float, float]:
    """``high`` is over ``settled`` priors alone: any other may drop out, which can only RAISE the minimum."""
    sids = [int(s) for s in cells]
    entries = ruler.entries_covering(sids) if ruler is not None else None
    known = {s: candidate[s] for s in sids if s in candidate}
    c_low, c_high, c_floor = theta_bounds_given_delta(
        known, [s for s in sids if s not in known], entries
    )
    low: dict[str, float] = {}
    high: dict[str, float] = {}
    for pid, grades in priors.items():
        both = {s: grades[s] for s in known if s in grades}
        rest = [s for s in sids if s not in both]
        p_low, p_high, p_floor = theta_bounds_given_delta(both, rest, entries)
        wins, losses = discordant_counts([known[s] for s in both], list(both.values()))
        # An absent grade can go either way, so it is read as the worst case for each bound.
        can_lose = sum(1 for s in rest if candidate.get(s, 0.0) < grades.get(s, 1.0))
        can_win = sum(1 for s in rest if candidate.get(s, 1.0) > grades.get(s, 0.0))
        worst = p_exceeds(c_low, c_floor, p_high, p_floor) if c_low < p_high else 0.5
        best = p_exceeds(c_high, c_floor, p_low, p_floor) if c_high > p_low else 0.5
        low[pid] = _capped(worst, sign_posterior(wins, losses + can_lose))
        high[pid] = _capped(best, sign_posterior(wins + can_win, losses))
    if not low:
        return 1.0, 1.0
    return min(low.values()), min(high[p] for p in settled) if settled else max(high.values())
