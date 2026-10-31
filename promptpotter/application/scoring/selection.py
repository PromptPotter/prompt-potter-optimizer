from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, NamedTuple

from promptpotter.application.intelligence.rasch import (
    FIT_SLACK,
    candidate_abilities,
    fit_theta,
    graded_response,
    theta_bounds_given_delta,
    theta_cells,
    theta_lift_over_parent,
)
from promptpotter.domain.origin_panel import election_rows
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.scoring import NO_CELLS, ROW_GRADES, CellSheet, GradedCell
from promptpotter.shared.statistics import (
    discordant_counts,
    mean_ci,
    p_exceeds,
    sign_posterior,
)

if TYPE_CHECKING:
    from promptpotter.application.intelligence.rasch import RoundAbilities
    from promptpotter.domain.arm_reading import DisplayMetric
    from promptpotter.domain.results import RoundResult
    from promptpotter.domain.ruler import DeltaRuler
    from promptpotter.domain.scoring import GradeColumn


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


class PairedGrade(NamedTuple):
    """What a pairing reads off one cell: θ is fit on ``response``, a sign test counts ``verdict``."""

    response: float
    verdict: float


def paired_grades(cells: Iterable[GradedCell]) -> dict[str, PairedGrade]:
    """The cost term reaches a pairing through θ alone; a sign test counts disagreeing VERDICTS."""
    verdict = ROW_GRADES["fitness"]
    return {
        cell.key: PairedGrade(graded_response(cell), verdict.read(cell))
        for cell in cells
        if cell.scored
    }


def discordance(candidate: Sequence[PairedGrade], prior: Sequence[PairedGrade]) -> tuple[int, int]:
    return discordant_counts([g.verdict for g in candidate], [g.verdict for g in prior])


def cells_read(
    elects_on: DisplayMetric,
    cells: Iterable[GradedCell],
    parent: Collection[str],
    ruler: DeltaRuler | None,
) -> int:
    """The coverage floor's count: on ability the scored cells θ is read on, else every scored one."""
    scored = dict.fromkeys(cell.key for cell in cells if cell.scored)
    return len(theta_cells(scored, parent, ruler) if elects_on == "ability" else scored)


def crowning_cells(rounds: Sequence[RoundResult], parent: str) -> frozenset[str]:
    """The cells *parent*'s admission was won on — its discordant wins over the bar, on the rows that
    election read: a race drawn onto them replays the luck that crowned it. A tie reads as any draw."""
    crowned: set[str] = set()
    for at, rr in enumerate(rounds):
        election = rr.election
        if election is None or election.selected != parent or (bar := rr.reference) is None:
            continue
        skip = crowning_cells(rounds[:at], bar)
        own, against = (
            paired_grades(election_rows(rr.rows(key), rr.overlap_results.get(key, NO_CELLS), skip))
            for key in (ArmKey(parent), bar)
        )
        crowned.update(c for c, g in own.items() if c in against and g.verdict > against[c].verdict)
    return frozenset(crowned)


def readable_lifts(
    keys: Sequence[ArmKey], coverage_floor: int, abilities: RoundAbilities
) -> dict[ArmKey, float]:
    reads: dict[ArmKey, float] = {}
    for key in keys:
        # PoBB's `n_min` IS this floor: both count `theta_cells`.
        if abilities.cells.get(key, 0) < coverage_floor:
            continue
        lift = theta_lift_over_parent(abilities, key)
        if lift is not None:
            reads[key] = lift
    return reads


def closest_to_bar(reads: Mapping[ArmKey, float], *, beside: ArmKey | None = None) -> ArmKey | None:
    """On the θ lift ADMISSION takes; the first walked holds a tie."""
    return max((key for key in reads if key != beside), key=lambda k: reads[k], default=None)


def elect_round_winner(
    keys: Sequence[ArmKey],
    rows: Mapping[ArmKey, CellSheet],
    parent_results: CellSheet,
    coverage_floor: int,
    ruler: DeltaRuler | None,
    *,
    admission: float,
) -> tuple[ArmKey | None, RoundAbilities]:
    """ADMISSION is raw θ above the parent's by more than a fit resolves AND ``P(θ_cand >
    θ_parent)``, bounded by the discordant cells, past *admission*. The RANK is the unbounded
    posterior."""

    abilities = candidate_abilities(
        {key: rows[key] for key in keys if key in rows},
        parent_results,
        ruler,
    )

    parent = paired_grades(parent_results)
    best_rank: tuple[float, int] = (0.0, 0)
    winner: ArmKey | None = None
    reads = readable_lifts(keys, coverage_floor, abilities)
    for key, lift in reads.items():
        # No SE margin: subtracting one turns a wide-posterior gain negative; uncertainty is the RANK's.
        if lift <= FIT_SLACK:
            continue
        # A θ lift the paired cells contradict is the prior's or the δ weighting's, never the arm's.
        own = paired_grades(rows[key])
        shared = [k for k in own if k in parent]
        wins, losses = discordance([own[k] for k in shared], [parent[k] for k in shared])
        if wins <= losses:
            continue
        # The bar is read off no cell that crowned the parent: `election_rows` leaves out `crowning_cells`.
        p_better = p_exceeds(abilities.theta[key], abilities.theta_se[key], *abilities.bar[key])
        if _capped(p_better, sign_posterior(wins, losses)) <= admission:
            continue
        rank = (p_better, abilities.cells[key])
        if rank > best_rank:
            best_rank = rank
            winner = key
    return winner, abilities


def elimination_p_best(
    candidate_grades: Sequence[PairedGrade],
    paired_prior_grades: Mapping[ArmKey, Sequence[PairedGrade]],
    candidate_cells: Sequence[str],
    ruler: DeltaRuler | None,
) -> tuple[float, dict[ArmKey, float]]:
    """A stopping rule, not a verdict: ``P(θ_cand > θ_prior)`` CAPPED by what the discordant pairs support."""
    if not paired_prior_grades:
        return 1.0, {}

    # The one sanctioned provisional δ: cells the ruler has not absorbed stand at its centre for BOTH arms.
    entries = ruler.entries_covering(candidate_cells) if ruler is not None else None

    def read(grades: Sequence[PairedGrade]) -> tuple[float, float]:
        on_cells = {key: g.response for key, g in zip(candidate_cells, grades, strict=True)}
        fitted = fit_theta(on_cells, entries)
        if fitted is None:
            raise ValueError("elimination_p_best: a reading needs at least one graded cell")
        return fitted

    theta_c, se_c = read(candidate_grades)

    per_prior: dict[ArmKey, float] = {}
    for pid, grades in paired_prior_grades.items():
        theta_p, se_p = read(grades)
        p = p_exceeds(theta_c, se_c, theta_p, se_p)
        per_prior[pid] = _capped(p, sign_posterior(*discordance(candidate_grades, grades)))
    return min(per_prior.values()), per_prior


class PairedPosterior(NamedTuple):
    cells: list[str]
    grades: list[PairedGrade]
    # Each prior's grades on the cells it shares with the candidate: the pairs its θ was read on.
    priors: dict[ArmKey, dict[str, PairedGrade]]
    p_best: float
    p_better: dict[ArmKey, float]


def paired_p_best(
    rows: Sequence[GradedCell],
    priors: Mapping[ArmKey, Mapping[str, PairedGrade]],
    ruler: DeltaRuler | None,
    *,
    min_shared: int,
) -> PairedPosterior | None:
    """Over rows carrying a verdict only: a backend error is no evidence of inability. A prior is
    read on the cells it shares with the candidate, so a parent still catching up is a prior."""
    own = paired_grades(rows)
    paired = {
        pid: shared
        for pid, grades in priors.items()
        if len(shared := {c: grades[c] for c in own if c in grades}) >= max(min_shared, 1)
    }
    if not paired:
        return None
    p_better = {
        pid: elimination_p_best(
            [own[c] for c in shared], {pid: list(shared.values())}, list(shared), ruler
        )[0]
        for pid, shared in paired.items()
    }
    return PairedPosterior(list(own), list(own.values()), paired, min(p_better.values()), p_better)


def _capped(p: float, bound: float) -> float:
    # The bound's mass on the SIDE θ read, not `|bound - 0.5|`, which reads 3 losses as 3 wins.
    support = bound if p > 0.5 else 1.0 - bound
    reach = min(abs(p - 0.5), max(0.0, support - 0.5))
    return 0.5 + reach if p > 0.5 else 0.5 - reach


_UNSOLVED = PairedGrade(0.0, 0.0)
_SOLVED = PairedGrade(1.0, 1.0)


def elimination_p_best_bounds(
    cells: Sequence[str],
    candidate: Mapping[str, PairedGrade],
    priors: Mapping[ArmKey, Mapping[str, PairedGrade]],
    ruler: DeltaRuler | None,
    *,
    settled: Collection[ArmKey],
) -> tuple[float, float]:
    """``high`` is over ``settled`` priors alone: any other may drop out, which can only RAISE the minimum."""
    sids = list(cells)
    entries = ruler.entries_covering(sids) if ruler is not None else None
    known = {s: candidate[s] for s in sids if s in candidate}
    c_low, c_high, c_floor = theta_bounds_given_delta(
        {s: g.response for s, g in known.items()}, [s for s in sids if s not in known], entries
    )
    low: dict[ArmKey, float] = {}
    high: dict[ArmKey, float] = {}
    for pid, grades in priors.items():
        both = {s: grades[s] for s in known if s in grades}
        rest = [s for s in sids if s not in both]
        p_low, p_high, p_floor = theta_bounds_given_delta(
            {s: g.response for s, g in both.items()}, rest, entries
        )
        wins, losses = discordance([known[s] for s in both], list(both.values()))
        # An absent grade can go either way, so it is read as the worst case for each bound.
        _, can_lose = discordance(
            [candidate.get(s, _UNSOLVED) for s in rest], [grades.get(s, _SOLVED) for s in rest]
        )
        can_win, _ = discordance(
            [candidate.get(s, _SOLVED) for s in rest], [grades.get(s, _UNSOLVED) for s in rest]
        )
        worst = p_exceeds(c_low, c_floor, p_high, p_floor) if c_low < p_high else 0.5
        best = p_exceeds(c_high, c_floor, p_low, p_floor) if c_high > p_low else 0.5
        low[pid] = _capped(worst, sign_posterior(wins, losses + can_lose))
        high[pid] = _capped(best, sign_posterior(wins + can_win, losses))
    if not low:
        return 1.0, 1.0
    return min(low.values()), min(high[p] for p in settled) if settled else max(high.values())
