from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from typing import NamedTuple

from promptpotter.application.intelligence.earned_blocks import CampaignSurvey
from promptpotter.application.intelligence.rasch import (
    RoundAbilities,
    candidate_abilities,
    graded_response,
    theta_cells,
    theta_lift_over_parent,
)
from promptpotter.domain.arm_reading import DisplayMetric
from promptpotter.domain.crowning_bar import BarSubject, CrowningBar, LiftPrior, lift_prior
from promptpotter.domain.paired_reading import ArmKey, Estimate, MeasurandUnit
from promptpotter.domain.results import RoundResult
from promptpotter.domain.ruler import DeltaRuler, clears, p_better
from promptpotter.domain.scoring import ROW_GRADES, CellSheet, GradeColumn, GradedCell
from promptpotter.shared.statistics import (
    bounded_mean_interval,
    discordant_counts,
    sign_posterior,
)


def level_band(sheet: CellSheet, column: GradeColumn, unit: MeasurandUnit) -> Estimate | None:
    values = list(sheet.column(column).values())
    if not values:
        return None
    level = sum(values) / len(values)
    lo, hi = bounded_mean_interval(values, binary=column.binary) or (None, None)
    return Estimate(
        value=level, ci_lo=lo, ci_hi=hi, n=len(values), se=None, unit=unit, form="level"
    )


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
    for rr in rounds:
        election = rr.election
        if election is None or election.selected != parent or (reference := rr.reference) is None:
            continue
        own, against = (paired_grades(rr.raced(key)) for key in (ArmKey(parent), reference.arm))

        crowned.update(c for c, g in own.items() if c in against and g.verdict > against[c].verdict)
    return frozenset(crowned)


def readable_lifts(
    keys: Sequence[ArmKey], coverage_floor: int, abilities: RoundAbilities
) -> dict[ArmKey, Estimate]:
    reads: dict[ArmKey, Estimate] = {}
    for key in keys:
        # PoBB's `n_min` IS this floor: both count `theta_cells`.
        if abilities.cells.get(key, 0) < coverage_floor:
            continue
        lift = theta_lift_over_parent(abilities, key)
        if lift is not None:
            reads[key] = lift
    return reads


def closest_to_bar(
    reads: Mapping[ArmKey, Estimate], *, beside: ArmKey | None = None
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
    bar: CrowningBar,
) -> tuple[ArmKey | None, RoundAbilities]:
    """ADMISSION is the crowning *bar* over every readable arm at once; under ``posterior`` that is
    raw θ above the parent's by more than a fit resolves AND ``P(θ_cand > θ_parent)``, bounded by
    the discordant cells, past the admission. The RANK is the unbounded posterior."""

    abilities = candidate_abilities(
        {key: rows[key] for key in keys if key in rows},
        parent_results,
        ruler,
    )
    reads = readable_lifts(keys, coverage_floor, abilities)
    verdicts = bar.read(bar_subjects(reads, rows, parent_results, bar.admission))
    best_rank: tuple[float, int] = (0.0, 0)
    winner: ArmKey | None = None
    for key, lift in reads.items():
        if not verdicts[key].clears:
            continue
        rank = (p_better(lift), abilities.cells[key])
        if rank > best_rank:
            best_rank = rank
            winner = key
    return winner, abilities


def banked_lift_prior(survey: CampaignSurvey, dataset: str) -> LiftPrior | None:
    """The generator's lift prior off the tenant's banked arm-vs-parent θ lifts on *dataset*:
    the archive is filed by the dataset a row measured, never the campaign that paid."""
    return lift_prior(
        fit.lift
        for campaign in survey.campaigns
        if campaign.dataset_name == dataset
        for standing in survey.cycles(campaign)
        for held in standing.rounds.values()
        if held.close.round > 0 and (election := held.close.election) is not None
        for key in held.close.panel
        if (fit := election.fit.get(key)) is not None and fit.lift is not None
    )


def discordant(rows: CellSheet, bar: CellSheet) -> tuple[int, int]:
    """The verdicts *rows* won and lost against *bar* on the cells both scored."""
    own, against = paired_grades(rows), paired_grades(bar)
    shared = [k for k in own if k in against]
    return discordance([own[k] for k in shared], [against[k] for k in shared])


def bar_subjects(
    lifts: Mapping[ArmKey, Estimate],
    rows: Mapping[ArmKey, CellSheet],
    parent_results: CellSheet,
    admission: float,
) -> dict[ArmKey, BarSubject]:
    """Each arm's paired evidence over the parent. A θ lift the paired cells contradict is the
    prior's or the δ weighting's, never the arm's, so no reading clears it; the bar is read off no
    cell that crowned the parent (`election_rows` leaves out `crowning_cells`)."""
    subjects: dict[ArmKey, BarSubject] = {}
    for key, lift in lifts.items():
        wins, losses = discordant(rows[key], parent_results)
        posterior = wins > losses and clears(lift, admission, support=sign_posterior(wins, losses))
        subjects[key] = BarSubject(wins, losses, lift, posterior)
    return subjects
