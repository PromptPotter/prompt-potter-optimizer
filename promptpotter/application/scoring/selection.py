from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, NamedTuple

from promptpotter.application.intelligence.rasch import (
    candidate_abilities,
    graded_response,
    theta_cells,
    theta_lift_over_parent,
)
from promptpotter.domain.origin_panel import election_rows
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.scoring import NO_CELLS, ROW_GRADES, CellSheet, GradedCell
from promptpotter.shared.statistics import (
    discordant_counts,
    mean_ci,
    sign_posterior,
)

if TYPE_CHECKING:
    from promptpotter.application.intelligence.rasch import RoundAbilities
    from promptpotter.domain.arm_reading import DisplayMetric
    from promptpotter.domain.results import RoundResult
    from promptpotter.domain.ruler import DeltaRuler, ThetaLift
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
        if election is None or election.selected != parent or (reference := rr.reference) is None:
            continue
        bar = reference.arm
        skip = crowning_cells(rounds[:at], bar)
        own, against = (
            paired_grades(election_rows(rr.rows(key), rr.overlap_results.get(key, NO_CELLS), skip))
            for key in (ArmKey(parent), bar)
        )
        crowned.update(c for c, g in own.items() if c in against and g.verdict > against[c].verdict)
    return frozenset(crowned)


def readable_lifts(
    keys: Sequence[ArmKey], coverage_floor: int, abilities: RoundAbilities
) -> dict[ArmKey, ThetaLift]:
    reads: dict[ArmKey, ThetaLift] = {}
    for key in keys:
        # PoBB's `n_min` IS this floor: both count `theta_cells`.
        if abilities.cells.get(key, 0) < coverage_floor:
            continue
        lift = theta_lift_over_parent(abilities, key)
        if lift is not None:
            reads[key] = lift
    return reads


def closest_to_bar(
    reads: Mapping[ArmKey, ThetaLift], *, beside: ArmKey | None = None
) -> ArmKey | None:
    """On the θ lift ADMISSION takes; the first walked holds a tie."""
    return max((key for key in reads if key != beside), key=lambda k: reads[k].value, default=None)


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
        # A θ lift the paired cells contradict is the prior's or the δ weighting's, never the arm's.
        own = paired_grades(rows[key])
        shared = [k for k in own if k in parent]
        wins, losses = discordance([own[k] for k in shared], [parent[k] for k in shared])
        if wins <= losses:
            continue
        # The bar is read off no cell that crowned the parent: `election_rows` leaves out `crowning_cells`.
        if not lift.clears(admission, support=sign_posterior(wins, losses)):
            continue
        rank = (lift.p_better, abilities.cells[key])
        if rank > best_rank:
            best_rank = rank
            winner = key
    return winner, abilities


class PriorRead(NamedTuple):
    """The election's posterior of an arm over one prior, and the cells it stands on."""

    p_better: float
    paired: int
    discordant: int


def prior_read(
    rows: CellSheet, prior: CellSheet, ruler: DeltaRuler | None, *, min_shared: int
) -> PriorRead | None:
    """A stopping rule, not a verdict: the election's fit and posterior with *prior* as the bar
    (``candidate_abilities``), capped by the discordant cells both scored. A prior is read only on
    *min_shared* such cells, so a parent still catching up is a prior."""
    own, bar = paired_grades(rows), paired_grades(prior)
    shared = [c for c in own if c in bar]
    if len(shared) < max(min_shared, 1):
        return None
    lift = theta_lift_over_parent(candidate_abilities({"read": rows}, prior, ruler), "read")
    if lift is None:
        return None
    wins, losses = discordance([own[c] for c in shared], [bar[c] for c in shared])
    return PriorRead(lift.reach(sign_posterior(wins, losses)), len(shared), wins + losses)


def capped_reach(wins: int, losses: int) -> tuple[float, float]:
    """The least and most any θ posterior can read once capped by these discordant counts."""
    bound = sign_posterior(wins, losses)
    return min(0.5, bound), max(0.5, bound)
