from __future__ import annotations

from collections.abc import Mapping, Sequence

from promptpotter.application.intelligence.earned_blocks import CampaignSurvey
from promptpotter.application.intelligence.rasch import RoundAbilities, theta_lift_over_parent
from promptpotter.domain.crowning_bar import BarSubject, CrowningBar, LiftPrior, lift_prior
from promptpotter.domain.paired_reading import ArmKey, CellSetName, Estimate, ReadingState
from promptpotter.domain.results import RoundResult
from promptpotter.domain.scoring import ROW_GRADES


def crowning_cells(
    rounds: Sequence[RoundResult], parent: str, key_of: Mapping[int, str]
) -> frozenset[str]:
    """The cells *parent*'s crown was won on: those its fitness rose on over the arm the round's
    deciding pair read it against, on the cells that pair read (the measurand the crown is decided
    on), a race drawn onto them replaying the luck that crowned it. A leader the line moved on to
    uncrowned won on no cells, so its rows read as any draw. *key_of* names the origin panel's
    slots."""
    fitness = ROW_GRADES["fitness"]
    crowned: set[str] = set()
    for rr in rounds:
        election = rr.election
        if election is None or election.selected != parent or election.deciding_pair is None:
            continue
        pair = election.deciding_pair
        assert pair.state is ReadingState.READ, "a crown is decided on a read pair"
        own = rr.raced(ArmKey(parent)).by_key()
        against = rr.raced(ArmKey(pair.a.address.individual_id)).by_key()
        on = (
            {key_of[slot] for slot in rr.overlap.positions}
            if pair.cell_set.name is CellSetName.ORIGIN_PANEL
            else own.keys() & against.keys()
        )
        crowned.update(
            key
            for key in on
            if key in own
            and key in against
            and own[key].scored
            and against[key].scored
            and fitness.read(own[key]) > fitness.read(against[key])
        )
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


def ranked_first(
    bar: CrowningBar, subjects: Mapping[ArmKey, BarSubject], cells: Mapping[ArmKey, int]
) -> ArmKey | None:
    """The subject *bar* ranks first (`CrowningBar.rank`), more cells read holding a tie, then the
    first walked: the crown among the arms that clear, the lead among all."""
    return max(subjects, key=lambda k: (bar.rank(subjects[k]), cells[k]), default=None)


def crowned(
    bar: CrowningBar, subjects: Mapping[ArmKey, BarSubject], cells: Mapping[ArmKey, int]
) -> ArmKey | None:
    """The arm *bar* ranks first among those it clears, read over every readable arm at once."""
    clears = bar.clears(subjects)
    return ranked_first(bar, {k: s for k, s in subjects.items() if clears[k]}, cells)


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
        for key in held.close.proposed
        if (fit := election.fit.get(key)) is not None and fit.lift is not None
    )
