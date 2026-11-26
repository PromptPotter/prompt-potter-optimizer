from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import NamedTuple, cast

from promptpotter.application.bench.node_context import Bounds, Lineage, NodeContext, Replay, Ruler
from promptpotter.application.bench.resume_and_fork.replayers import ReplayContext, Replayed
from promptpotter.application.intelligence.rasch import (
    RoundAbilities,
    candidate_abilities,
    theta_lift_over_parent,
)
from promptpotter.application.optimizers.nodes import MeasuredRound, Selection
from promptpotter.application.optimizers.potter.knobs import ElectionKnobs
from promptpotter.application.scoring.paired import RoundPairs
from promptpotter.application.scoring.selection import crowned, ranked_first
from promptpotter.domain.arms import ArmState, Concluded, arm_name
from promptpotter.domain.crowning_bar import BarSubject, CrowningBar, NextParent, Vote
from promptpotter.domain.origin_panel import RoundEnding, panel_trigger
from promptpotter.domain.paired_reading import ArmKey, ArmName, CellSetName, PairedReading
from promptpotter.domain.scoring import CellSheet


def _value(value: float | None) -> str:
    """A value never read prints ``n/a``, never ``0.000``, which is a real one."""
    return "n/a" if value is None else f"{value:+.3f}"


def _point(bar: CrowningBar, subject: BarSubject) -> str:
    """The value *bar* ranks *subject* on."""
    if bar.reads_theta:
        return f"θ lift {_value(None if subject.lift is None else subject.lift.value)} logits"
    return (
        f"paired lift {subject.paired.estimate.value:+.3f} "
        f"({subject.wins} won, {subject.losses} lost)"
    )


def _read_on(bar: CrowningBar, key: ArmKey, subject: BarSubject, abilities: RoundAbilities) -> str:
    """The bar's value, then the θ lift beside it where the bar crowns on the paired verdicts."""
    lift = subject.lift
    theta = (
        None
        if lift is None
        else f"θ lift {lift.value:+.3f} logits, se {lift.se:.3f} "
        f"(θ {_value(abilities.theta.get(key))} vs parent {_value(abilities.bar[key][0])})"
    )
    if bar.reads_theta:
        assert theta is not None, "a θ crown's subject carries its θ lift"
        return theta
    return _point(bar, subject) if theta is None else f"{_point(bar, subject)}, {theta}"


def _verdict_reason(
    *,
    bar: CrowningBar,
    decided: RoundDecision,
    subjects: Mapping[ArmKey, BarSubject],
    cells: Mapping[ArmKey, int],
    electable: Sequence[ArmKey],
    abilities: RoundAbilities,
    labels: Mapping[ArmKey, str],
    coverage_floor: int,
    n_scored: int,
    cold: bool,
) -> str:
    scale = (
        " (cold ruler — θ is logit-fitness on the cells each arm shares with the parent)"
        if cold
        else ""
    )
    census = f"{len(electable)} of {n_scored} electable, coverage floor {coverage_floor}"
    leading, ending = decided
    if leading is None:
        read_on = ", ".join(f"{labels[key]} {abilities.cells.get(key, 0)}" for key in electable)
        return (
            f"no arm could be read against the parent; cells each θ was read on: "
            f"{read_on or 'none'}; {census}{scale}"
        )
    said = _read_on(bar, leading, subjects[leading], abilities)
    match ending:
        case RoundEnding.CROWNED:
            others = {k: s for k, s in subjects.items() if k != leading}
            runner = ranked_first(bar, others, cells)
            return (
                f"{labels[leading]} won on {said}"
                + (
                    f"; runner-up {labels[runner]} at {_point(bar, subjects[runner])}"
                    if runner is not None
                    else ""
                )
                + scale
            )
        case RoundEnding.MOVED:
            return (
                f"no arm cleared the parent; the line moves on to {labels[leading]}, uncrowned, "
                f"on {said}; {census}{scale}"
            )
        case RoundEnding.HELD:
            return f"no arm cleared the parent: best {labels[leading]} {said}; {census}{scale}"


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

    def subjects(
        self, pairings: Mapping[ArmKey, PairedReading], abilities: RoundAbilities
    ) -> dict[ArmKey, BarSubject]:
        """Every arm its pairing read on two cells or more and the coverage floor reads: on θ cells
        where the bar crowns on θ, which then reads its θ lift, else on the pairing's scored cells."""
        subjects: dict[ArmKey, BarSubject] = {}
        for key, pair in pairings.items():
            lift = theta_lift_over_parent(abilities, key)
            if pair.headline is None or (self.bar.reads_theta and lift is None):
                continue
            subject = BarSubject(pair.headline, lift)
            if self.covered(key, subject, abilities) >= self.coverage_floor:
                subjects[key] = subject
        return subjects

    def covered(self, key: ArmKey, subject: BarSubject, abilities: RoundAbilities) -> int:
        """The cells the coverage floor counts, which also hold a rank's tie."""
        if self.bar.reads_theta:
            return abilities.cells.get(key, 0)
        assert subject.paired.estimate.n is not None, "a paired lift counts its cells"
        return subject.paired.estimate.n

    def decide(
        self, subjects: Mapping[ArmKey, BarSubject], abilities: RoundAbilities
    ) -> RoundDecision:
        """The crown ends the round on itself; a round crowning none moves on to its leader where
        ``next_parent`` lets the line move to it uncrowned, else holds on its parent."""
        cells = {key: self.covered(key, s, abilities) for key, s in subjects.items()}
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
        abilities = candidate_abilities(
            {key: raced[key] for key in self.arms}, raced[self.parent], ctx.ruler
        )
        pairings = self.pairings(raced, _names(rr.arms, rr.round))
        decided = self.decide(self.subjects(pairings, abilities), abilities)
        return [decided.ending, decided.leading]

    def stands_on(self, outcome: object) -> str | None:
        """``[ending, leading]``'s arm, ``None`` where the parent stands."""
        ending, leading = cast("tuple[str, str | None]", outcome)
        return leading if RoundEnding(ending).moves_line else None


def _names(arms: Mapping[ArmKey, ArmState], round_num: int) -> dict[ArmKey, ArmName]:
    return {key: arm_name(key, arm, round_num) for key, arm in arms.items()}


def elect_on_theta(ctx: NodeContext, measured: MeasuredRound) -> Selection:
    ruler = ctx.read(Ruler).difficulty.ruler
    knobs = ctx.knobs(ElectionKnobs)
    bounds = ctx.read(Bounds)
    electable = seated(
        knobs.vote, measured.electable, measured.arms, measured.rows, bounds.elimination_n_min
    )
    bar = CrowningBar(
        statistic=knobs.crown,
        admission=knobs.admission,
        prior=bounds.lift_prior() if knobs.crown == "empirical_bayes" else None,
    )
    inputs = RoundWinner(
        arms=electable,
        coverage_floor=measured.coverage_floor,
        bar=bar,
        next_parent=knobs.next_parent,
        parent=measured.parent.key,
        cells=None if knobs.vote == "draw" else sorted(measured.origin_panel.values()),
        pairs=measured.pairs,
    )
    abilities = measured.abilities
    names = _names({key: read.state for key, read in measured.filed.items()}, ctx.round_num)
    pairings = inputs.pairings(measured.raced, names)
    subjects = inputs.subjects(pairings, abilities)
    decided = inputs.decide(subjects, abilities)
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
            cells={key: inputs.covered(key, s, abilities) for key, s in subjects.items()},
            electable=electable,
            abilities=abilities,
            labels=measured.labels,
            coverage_floor=measured.coverage_floor,
            n_scored=len(measured.arms),
            cold=ruler is None,
        ),
        deciding_pair=None if leading is None else pairings[leading],
    )
