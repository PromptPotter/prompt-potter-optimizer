from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import NamedTuple, cast

from promptpotter.application.bench.node_context import Bounds, Lineage, NodeContext, Replay
from promptpotter.application.bench.resume_and_fork.replayers import ReplayContext, Replayed
from promptpotter.application.optimizer_contract import MeasuredRound, Selection
from promptpotter.application.optimizers.potter.knobs import ElectionKnobs
from promptpotter.application.scoring.paired import RoundPairs
from promptpotter.application.scoring.selection import crowned, ranked_first
from promptpotter.domain.arms import ArmState, Concluded, arm_name
from promptpotter.domain.crowning_bar import BarSubject, CrowningBar, NextParent, Vote
from promptpotter.domain.origin_panel import RoundEnding, panel_trigger
from promptpotter.domain.paired_reading import (
    ArmKey,
    ArmName,
    CellSetName,
    PairedReading,
    ReadingState,
    ReadPair,
)
from promptpotter.domain.scoring import CellSheet


def _point(subject: BarSubject) -> str:
    """The value the bar ranks *subject* on: its paired lift over the parent."""
    return (
        f"paired lift {subject.paired.estimate.value:+.3f} "
        f"({subject.wins} won, {subject.losses} lost)"
    )


def _verdict_reason(
    *,
    bar: CrowningBar,
    decided: RoundDecision,
    subjects: Mapping[ArmKey, BarSubject],
    cells: Mapping[ArmKey, int],
    electable: Sequence[ArmKey],
    labels: Mapping[ArmKey, str],
    coverage_floor: int,
    n_scored: int,
) -> str:
    census = f"{len(electable)} of {n_scored} electable, coverage floor {coverage_floor}"
    leading, ending = decided
    if leading is None:
        return f"no arm could be read against the parent; {census}"
    said = _point(subjects[leading])
    match ending:
        case RoundEnding.CROWNED:
            others = {k: s for k, s in subjects.items() if k != leading}
            runner = ranked_first(bar, others, cells)
            return f"{labels[leading]} won on {said}" + (
                f"; runner-up {labels[runner]} at {_point(subjects[runner])}"
                if runner is not None
                else ""
            )
        case RoundEnding.MOVED:
            return (
                f"no arm cleared the parent; the line moves on to {labels[leading]}, uncrowned, "
                f"on {said}; {census}"
            )
    return f"no arm cleared the parent: best {labels[leading]} {said}; {census}"


def seated(
    vote: Vote,
    electable: Sequence[ArmKey],
    arms: Mapping[ArmKey, Concluded],
    rows: Mapping[ArmKey, CellSheet],
    n_min: int,
) -> list[ArmKey]:
    """Under ``overlap`` only an arm the panel trigger seats on the origin panel is electable."""
    if vote == "draw":
        return list(electable)
    return [k for k in electable if panel_trigger(arms[k], rows[k], n_min) is not None]


class RoundDecision(NamedTuple):
    """The arm a round's election reads off and how the round ends on it."""

    leading: ArmKey | None
    ending: RoundEnding


class RoundWinner(Replayed, kind="round_winner"):
    # The arms the vote seats, of `MeasuredRound.electable`.
    arms: list[ArmKey]
    coverage_floor: int
    bar: CrowningBar
    next_parent: NextParent
    # Names which of the round's banked references the bar was; its rows are the close's.
    parent: ArmKey
    # The origin panel's cells the vote pairs on, sorted; `None` pairs on every cell both raced.
    cells: list[str] | None
    pairs: RoundPairs

    def pairings(
        self, raced: Mapping[ArmKey, CellSheet], names: Mapping[ArmKey, ArmName]
    ) -> dict[ArmKey, PairedReading]:
        """Each arm (`b`) over the parent (`a`) on its fitness verdicts alone, on the vote's cells."""
        against = self.pairs.member(
            self.parent, raced[self.parent], name=names.get(self.parent), role=None, cut=False
        )
        return {
            key: self.pairs.read(
                against,
                self.pairs.member(key, raced[key], name=names.get(key), role=None, cut=False),
                cell_set=CellSetName.MEASURED_BY_BOTH
                if self.cells is None
                else CellSetName.ORIGIN_PANEL,
                cells=self.cells,
                measurands=self.pairs.measurands[:1],
            )
            for key in self.arms
        }

    def subjects(self, pairings: Mapping[ArmKey, PairedReading]) -> dict[ArmKey, BarSubject]:
        """Every arm its pairing read on two cells or more and the coverage floor reads, on the
        pairing's scored cells."""
        subjects: dict[ArmKey, BarSubject] = {}
        for key, pair in pairings.items():
            if pair.state is not ReadingState.READ:
                continue
            subject = BarSubject(pair.headline)
            if covered(subject) >= self.coverage_floor:
                subjects[key] = subject
        return subjects

    def decide(self, subjects: Mapping[ArmKey, BarSubject]) -> RoundDecision:
        """The crown ends the round on itself; a round crowning none moves on to its leader where
        ``next_parent`` lets the line move to it uncrowned, else holds on its parent."""
        cells = {key: covered(s) for key, s in subjects.items()}
        crown = crowned(self.bar, subjects, cells)
        if crown is not None:
            return RoundDecision(crown, RoundEnding.CROWNED)
        leader = ranked_first(self.bar, subjects, cells)
        match self.next_parent:
            case "crowned":
                moves = False
            case "not_lost":
                moves = leader is not None and not self.bar.lost(subjects[leader])
            case "leading":
                moves = leader is not None
        return RoundDecision(leader, RoundEnding.MOVED if moves else RoundEnding.HELD)

    def replay(self, ctx: ReplayContext) -> list[str | None]:
        """The parent is the reference the round BANKED, never a panel rebuilt here: that one re-elects differently."""
        rr = ctx.round_data
        raced = {key: rr.raced(key) for key in (*self.arms, self.parent)}
        pairings = self.pairings(raced, _names(rr.arms, rr.round))
        decided = self.decide(self.subjects(pairings))
        return [decided.ending, decided.leading]

    def stands_on(self, outcome: object) -> str | None:
        """``[ending, leading]``'s arm, ``None`` where the parent stands."""
        ending, leading = cast("tuple[str, str | None]", outcome)
        return leading if RoundEnding(ending).moves_line else None


def covered(subject: BarSubject) -> int:
    """The cells the coverage floor counts, which also hold a rank's tie."""
    assert subject.paired.estimate.n is not None, "a paired lift counts its cells"
    return subject.paired.estimate.n


def _names(arms: Mapping[ArmKey, ArmState], round_num: int) -> dict[ArmKey, ArmName]:
    return {key: arm_name(key, arm, round_num) for key, arm in arms.items()}


def elect_on_pairings(ctx: NodeContext, measured: MeasuredRound) -> Selection:
    knobs = ctx.knobs(ElectionKnobs)
    electable = seated(
        knobs.vote,
        measured.electable,
        measured.arms,
        measured.rows,
        ctx.read(Bounds).elimination_n_min,
    )
    bar = CrowningBar(statistic=knobs.crown, admission=knobs.admission)
    inputs = RoundWinner(
        arms=electable,
        coverage_floor=measured.coverage_floor,
        bar=bar,
        next_parent=knobs.next_parent,
        parent=measured.parent.key,
        cells=None if knobs.vote == "draw" else sorted(measured.origin_panel.values()),
        pairs=measured.pairs,
    )
    names = _names({key: read.state for key, read in measured.filed.items()}, ctx.round_num)
    pairings = inputs.pairings(measured.raced, names)
    subjects = inputs.subjects(pairings)
    decided = inputs.decide(subjects)
    ctx.read(Replay).decide(inputs, [decided.ending, decided.leading])
    leading = decided.leading
    # Evidence is bought in the round's race alone: a leader short of the bar there is never
    # crowned, whatever `next_parent` moves the line to.
    return Selection(
        leading=leading,
        ending=decided.ending,
        electable=len(electable),
        ranked=[
            measured.arms[leading].individual
            if leading is not None and decided.ending.moves_line
            else ctx.read(Lineage).parent
        ],
        verdict_reason=_verdict_reason(
            bar=bar,
            decided=decided,
            subjects=subjects,
            cells={key: covered(s) for key, s in subjects.items()},
            electable=electable,
            labels=measured.labels,
            coverage_floor=measured.coverage_floor,
            n_scored=len(measured.arms),
        ),
        # A crown is decided on a read pair: a leader its pairing never read was decided on none.
        deciding_pair=pair
        if leading is not None and isinstance(pair := pairings[leading], ReadPair)
        else None,
    )
