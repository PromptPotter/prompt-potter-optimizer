from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any, Literal, Self

from pydantic import ConfigDict, Field, model_validator

from promptpotter.domain.arm_reading import (
    ArmElection,
    ArmReading,
    DisplayMetric,
    ScoreboardRankKey,
    ScoreboardRow,
    panel_counts,
    panel_cuts,
    scoreboard_rank_key,
)
from promptpotter.domain.arms import ArmState, Concluded, arm_name, is_leader_eligible
from promptpotter.domain.cell_fold import ScoreSummary
from promptpotter.domain.optimizer_state import OptimizerState
from promptpotter.domain.origin_panel import (
    OriginPanel,
    OverlapReading,
    RoundEnding,
    line_by_individual,
)
from promptpotter.domain.paired_reading import ArmKey, PairedReading, ReadPair, RoundArm
from promptpotter.domain.pass_kinds import SCOPE_ROLES, IndividualWalk, individual_cells
from promptpotter.domain.round_health import DegradationHealth
from promptpotter.domain.scoring import (
    NO_CELLS,
    CellSheet,
    GradedCell,
    WalkedCell,
)
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.shared.hashing import stable_hash
from promptpotter.shared.identity import Principal
from promptpotter.shared.measurement_context import MeasurementRole, RoleScope


def round_document_digest(rr: RoundResult) -> str:
    """The WHOLE outcome, never a chosen subset: under-firing goes stale in silence."""
    outcome = rr.model_dump(mode="json", include=set(RoundOutcome.model_fields))
    panel = {key: rows.addresses() for key, rows in rr.panel_rows.items()}
    return stable_hash([outcome, rr.addresses(), panel])


class OptimizerFact(FrozenModel):
    """One labelled reading an optimizer reports about a round, worded by its own runtime."""

    key: str
    label: str
    text: str
    value: float | None
    # A `stat` reads on one line; a `note` is prose the optimizer carries into its next round.
    kind: Literal["stat", "note"]


def round_walks[C: tuple[Any, ...]](
    arms: Mapping[ArmKey, Sequence[C]], panel: Mapping[str, Sequence[C]]
) -> list[IndividualWalk[C]]:
    """The WALK's role, never the answer's `role` stamp: that names who FILED it, and a replay reads
    it. An arm's cells are its individual's: a refused arm walked none, so its slot key holds none."""
    return [
        *(
            IndividualWalk(key, SCOPE_ROLES[RoleScope.DECISION], cells)
            for key, cells in arms.items()
        ),
        *(IndividualWalk(key, _PANEL_ROLES, cells) for key, cells in panel.items()),
    ]


_PANEL_ROLES = frozenset({MeasurementRole.OVERLAP})


class Reelection(FrozenModel):
    """A layer restating a round as electing another arm than its selector's, and why."""

    by: Principal
    reason: str


class Election(FrozenModel):
    """What a round's selector decided over its arms, by key: its `leading` arm and how the round
    ended on it."""

    leading: ArmKey | None = Field(
        description="The ONE arm this round is read off, as its selector named it and never "
        "re-ranked: the crown, the leader the line moved to, on a held round the challenger that "
        "came closest on the selector's own objective, at round 0 the origin. Rank 1 of "
        "`RoundView.candidates`. `null` where the selector could read no arm."
    )
    ending: RoundEnding = Field(
        description="`adopted`: round 0 adopted the origin `leading`; `crowned`: the round "
        "crowned `leading`; `moved`: "
        "the line moved to `leading` uncrowned (potter's `next_parent`); `held`: it stands on "
        "`parent`, re-read on this round's cells."
    )
    parent: RoundArm | None = Field(
        description="The candidate the line stood on entering the round, re-read on its cells: the "
        "arm every arm was read against, which a held round ends on; null at round 0, which reads "
        "none."
    )
    # Arms that entered the election. Zero says the proposer made no testable variant, not a stall.
    electable: int
    # Each arm (`b`) over the individual it was judged against (`a`): the round carries its pairings.
    vs_reference: dict[ArmKey, PairedReading] = Field(default_factory=dict)
    # `Selector.elects_on`: the column of `RoundView.candidates` the round was WON on.
    elects_on: DisplayMetric
    deciding_pair: ReadPair | None = Field(
        default=None,
        description="`leading` (`b`) over `parent` (`a`) on the cells its selector paired them "
        "on: the reading the round was decided on. Null where the selector banks none.",
    )
    # The selector's own reason; ``None`` on the origin round, which chose between nobody.
    verdict_reason: str | None = None
    # ``None`` where the selector's pick stands; ``verdict_reason`` stays the selector's.
    reelection: Reelection | None = None

    @model_validator(mode="after")
    def _ends_on_an_arm(self) -> Self:
        held = self.ending is RoundEnding.HELD
        if not held and self.leading is None:
            raise ValueError(f"a round {self.ending.value} on its leader, and it names none")
        if held and self.parent is None:
            raise ValueError("a held round ends on its parent, and it names none")
        return self

    @property
    def selected(self) -> ArmKey | None:
        """THE pick: round 0 selects the origin it adopted."""
        return self.leading if self.ending in (RoundEnding.ADOPTED, RoundEnding.CROWNED) else None

    @property
    def ended_on(self) -> ArmKey:
        """WHOSE numbers the round's headline carries, and the reference the next round is read against."""
        if self.ending is not RoundEnding.HELD:
            assert self.leading is not None
            return self.leading
        assert self.parent is not None
        return self.parent.arm


class RoundOutcome(FrozenModel):
    """A ``RoundStatement`` extends it and the round file is it plus its rows: a field declared here reaches both."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    round: int
    # Its proposed arms in slot order, then each reference the round re-read: one key, one state.
    arms: dict[ArmKey, ArmState] = Field(default_factory=dict)
    # Each arm's fold of the cells the round files for it; none for an arm refused before a cell.
    folds: dict[ArmKey, ScoreSummary]
    election: Election | None
    # Not `improved`, the selector's point estimate. Its reading is `pending` until the line's pass lands.
    origin_panel: OriginPanel
    # Graded against the rounds before it, so it is stamped, never re-derived from this one.
    health: DegradationHealth
    # Built once, at the round's close (`Cycle.close_state`); `None` from its election until then.
    optimizer_state: OptimizerState | None = None
    optimizer_facts: list[OptimizerFact] = Field(default_factory=list)

    @property
    def closed_state(self) -> OptimizerState:
        if self.optimizer_state is None:
            raise ValueError(
                f"round {self.round} holds no optimizer state: it has not closed, or it is a "
                "member's view, whose parts open through `NodeContext.recall`"
            )
        return self.optimizer_state

    @property
    def head(self) -> Concluded | None:
        """The arm the round ENDED on: the headline is its numbers."""
        if self.election is None:
            return None
        arm = self.arms[self.election.ended_on]
        assert isinstance(arm, Concluded), "a round ends on an arm it concluded"
        return arm

    @property
    def fold(self) -> ScoreSummary | None:
        """The headline: the fold of the arm the round ENDED on, never a field of its own."""
        return None if self.election is None else self.folds.get(self.election.ended_on)

    @property
    def improved(self) -> bool:
        """A promotion: the round's selector crowned an arm; an adopted origin promotes nothing."""
        return self.election is not None and self.election.ending is RoundEnding.CROWNED

    @property
    def proposed(self) -> dict[ArmKey, ArmState]:
        """The arms the round PROPOSED, in slot order: no reference it re-read."""
        return {key: arm for key, arm in self.arms.items() if arm.role.is_proposal}

    @property
    def candidates_scored(self) -> int:
        return sum(1 for arm in self.proposed.values() if is_leader_eligible(arm))

    @property
    def origin(self) -> Concluded:
        head = self.head
        assert self.round == 0 and head is not None, "round 0 ends on the origin"
        return head

    def vs_reference(self, key: ArmKey) -> PairedReading | None:
        return None if self.election is None else self.election.vs_reference.get(key)

    def arm_readings(self) -> list[ArmReading]:
        e = self.election
        selected, leading, electable = (
            (None, None, None) if e is None else (e.selected, e.leading, e.electable)
        )
        counts = {key: panel_counts(arm, self.folds.get(key)) for key, arm in self.arms.items()}
        proposed = list(self.proposed)
        cuts = dict(zip(proposed, panel_cuts([counts[key] for key in proposed]), strict=True))
        # A reference reads the cells its readers asked, so no proposed arm's fuller walk cuts it.
        cuts |= {key: panel_cuts([counts[key]])[0] for key in self.arms if key not in cuts}
        line = line_by_individual(self.origin_panel.reading)
        return [
            ArmReading.of(
                arm_name(key, arm, self.round),
                arm,
                fold=self.folds.get(key),
                cut=cuts[key],
                election=ArmElection.of(
                    key, held=e is not None, selected=selected, leading=leading, electable=electable
                ),
                vs_reference=self.vs_reference(key),
            ).on_line(line)
            for key, arm in self.arms.items()
        ]

    def ranked(self) -> list[ScoreboardRow]:
        """THE rank: the leading arm first, then composite, then fitness."""
        leading = None if self.election is None else self.election.leading

        def rank(reading: ArmReading) -> ScoreboardRankKey:
            own = reading.own
            return scoreboard_rank_key(
                None if own is None or own.composite is None else own.composite.value,
                None if own is None or own.fitness is None else own.fitness.value,
                is_leading=reading.arm.key == leading,
                is_partial=reading.end == "skipped",
            )

        ranked = sorted(self.arm_readings(), key=rank, reverse=True)
        return [ScoreboardRow(rank=i, reading=r) for i, r in enumerate(ranked, start=1)]


class RoundResult(RoundOutcome):
    """A round with its rows, which is the round file itself: facts alone, so no served word is
    stored (a surface reads its rank, `ranked`, and its headline, `headline`)."""

    # Offset of the statement it stands on. ``None`` where no ledger was bound: 0 is a real offset.
    at_offset: int | None = None
    # By arm: a reference re-read on the round's cells is an arm, so a held round's head is here.
    cells: dict[ArmKey, CellSheet] = Field(default_factory=dict)
    # Every origin-panel row, by the MEASURED individual, outside the arms (`OriginPanel.raced`).
    panel_rows: dict[str, CellSheet] = Field(default_factory=dict)

    @property
    def results(self) -> CellSheet:
        """The rows of the arm the round ENDED on: the headline's own."""
        return NO_CELLS if self.election is None else self.rows(self.election.ended_on)

    def rows(self, key: ArmKey) -> CellSheet:
        return self.cells.get(key, NO_CELLS)

    def raced(self, key: ArmKey) -> CellSheet:
        """The ONE row set the race and the election read: the draw, then the origin-panel cells
        it lacks that the election pooled."""
        pooled = key in self.origin_panel.raced
        return self.rows(key).merged(self.panel_rows.get(key, NO_CELLS) if pooled else NO_CELLS)

    def read_on_panel(self, reading: OverlapReading, bought: Mapping[str, CellSheet]) -> Self:
        """This round holding the line's origin-panel *reading* and the cells it *bought*."""
        panel = self.origin_panel.with_reading(reading)
        return self.model_copy(
            update={"origin_panel": panel, "panel_rows": {**self.panel_rows, **bought}}
        )

    def closed_rows(self, key: ArmKey) -> CellSheet:
        """Every row the round closed holding of *key*: its draw, then its origin-panel cells."""
        return self.rows(key).merged(self.panel_rows.get(key, NO_CELLS))

    def reference_rows(self, key: ArmKey) -> CellSheet:
        """The rows of the arm *key* was judged against, re-read on this round's cells."""
        pair = self.vs_reference(key)
        against = None if pair is None else pair.a
        return NO_CELLS if against is None else self.rows(ArmKey(against.address.individual_id))

    def addresses(self) -> dict[ArmKey, list[WalkedCell]]:
        """Its arms' rows as archive addresses, in walk order."""
        return {k: v.addresses() for k, v in self.cells.items()}

    @property
    def headline(self) -> ArmReading | None:
        """The arm the round ENDED on (`election.ended_on`), read."""
        ended_on = None if self.election is None else self.election.ended_on
        return next((r for r in self.arm_readings() if r.arm.key == ended_on), None)

    def sheet_of(self, individual_id: str) -> CellSheet:
        return InRunCells([self]).sheet(individual_id, RoleScope.DECISION)


class InRunCells:
    """Each round's walks, folded once: *held*'s rounds are reused, so a restated round re-folds alone."""

    def __init__(self, rounds: Sequence[RoundResult], held: InRunCells | None = None) -> None:
        kept = {} if held is None else held._rounds
        self._rounds: dict[int, tuple[RoundResult, list[IndividualWalk[_SampleCell]]]] = {
            rr.round: kept[rr.round]
            if rr.round in kept and kept[rr.round][0] is rr
            else (
                rr,
                round_walks(
                    {k: _by_sample(v) for k, v in rr.cells.items()},
                    {k: _by_sample(v) for k, v in rr.panel_rows.items()},
                ),
            )
            for rr in rounds
        }

    def sheet(self, individual_id: str, scope: RoleScope) -> CellSheet:
        walks = [walk for _, round_walks in self._rounds.values() for walk in round_walks]
        folded = individual_cells(walks, individual_id, scope)
        if not folded:
            return NO_CELLS
        return replace(folded[0][2], cells=tuple(cell for _, cell, _ in folded)).standing()


# The sheet it was read from: its scorer and that scorer's declaration ride the cell.
type _SampleCell = tuple[str, GradedCell, CellSheet]


def _by_sample(sheet: CellSheet) -> list[_SampleCell]:
    return [(cell.key, cell, sheet) for cell in sheet]
