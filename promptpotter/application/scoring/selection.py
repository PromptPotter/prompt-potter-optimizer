from __future__ import annotations

from collections.abc import Mapping, Sequence

from promptpotter.domain.crowning_bar import BarSubject, CrowningBar
from promptpotter.domain.paired_reading import ArmKey, CellSetName, ReadingState
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
            {key_of[slot] for slot in rr.origin_panel.reading.positions}
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
