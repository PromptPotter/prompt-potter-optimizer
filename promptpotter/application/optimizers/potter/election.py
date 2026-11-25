from __future__ import annotations

from collections.abc import Mapping, Sequence

from promptpotter.application.bench.node_context import Bounds, Lineage, NodeContext, Replay, Ruler
from promptpotter.application.bench.resume_and_fork.replayers import ReplayContext, Replayed
from promptpotter.application.intelligence.rasch import RoundAbilities, candidate_abilities
from promptpotter.application.optimizers.nodes import MeasuredRound, Selection
from promptpotter.application.optimizers.potter.knobs import ElectionKnobs
from promptpotter.application.scoring.selection import (
    closest_to_bar,
    elect_round_winner,
    readable_lifts,
)
from promptpotter.domain.crowning_bar import CrowningBar
from promptpotter.domain.paired_reading import ArmKey, Estimate
from promptpotter.domain.scoring import CellSheet


def _theta(value: float | None) -> str:
    """A θ never fit prints ``n/a``, never ``0.000``, which is a real ability."""
    return "n/a" if value is None else f"{value:+.3f}"


def _lift(key: ArmKey, lift: Estimate, abilities: RoundAbilities) -> str:
    return (
        f"θ lift {lift.value:+.3f} logits, se {lift.se:.3f} "
        f"(θ {_theta(abilities.theta.get(key))} vs parent {_theta(abilities.bar[key][0])})"
    )


def _verdict_reason(
    *,
    winner: ArmKey | None,
    leading: ArmKey | None,
    reads: Mapping[ArmKey, Estimate],
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
    if leading is None:
        read_on = ", ".join(f"{labels[key]} {abilities.cells.get(key, 0)}" for key in electable)
        return (
            f"no arm could be read against the parent; cells each θ was read on: "
            f"{read_on or 'none'}; {census}{scale}"
        )
    lift = _lift(leading, reads[leading], abilities)
    if winner is not None:
        runner = closest_to_bar(reads, beside=winner)
        return (
            f"{labels[winner]} won on {lift}"
            + (
                f"; runner-up {labels[runner]} at θ lift {reads[runner].value:+.3f} logits"
                if runner is not None
                else ""
            )
            + scale
        )
    return f"no arm cleared the parent: best {labels[leading]} {lift}; {census}{scale}"


class RoundWinner(Replayed, kind="round_winner"):
    arms: list[ArmKey]
    coverage_floor: int
    bar: CrowningBar
    # Names which of the round's banked references the bar was; its rows are the close's.
    parent: ArmKey
    # `MeasuredRound.paired_on`, sorted.
    paired_on: list[str] | None

    def elect(
        self, raced: Mapping[ArmKey, CellSheet], parent: CellSheet, abilities: RoundAbilities
    ) -> ArmKey | None:
        """On the rows the round's race read and their fit against the parent."""
        return elect_round_winner(
            self.arms,
            raced,
            parent,
            self.coverage_floor,
            abilities,
            bar=self.bar,
            paired_on=self.paired_on,
        )

    def replay(self, ctx: ReplayContext) -> ArmKey | None:
        """The parent is the reference the round BANKED, never a panel rebuilt here: that one re-elects differently."""
        raced = {key: ctx.round_data.raced(key) for key in self.arms}
        parent = ctx.round_data.raced(self.parent)
        return self.elect(raced, parent, candidate_abilities(raced, parent, ctx.ruler))


def elect_on_theta(ctx: NodeContext, measured: MeasuredRound) -> Selection:
    ruler = ctx.read(Ruler).difficulty.ruler
    electable = measured.electable
    inputs = RoundWinner(
        arms=electable,
        coverage_floor=measured.coverage_floor,
        bar=ctx.read(Bounds).crowning_bar(ctx.knobs(ElectionKnobs).admission),
        parent=measured.parent.key,
        paired_on=None if measured.paired_on is None else sorted(measured.paired_on),
    )
    abilities = measured.abilities
    selected = inputs.elect(measured.raced, measured.raced[inputs.parent], abilities)

    reads = readable_lifts(electable, measured.coverage_floor, abilities)
    leading = selected or closest_to_bar(reads)
    ctx.read(Replay).decide(inputs, selected)
    # Evidence is bought in the round's race alone: a leader short of the bar there is dropped.
    return Selection(
        selected=selected,
        leading=leading,
        ranked=[
            measured.arms[selected].individual if selected is not None else ctx.read(Lineage).parent
        ],
        verdict_reason=_verdict_reason(
            winner=selected,
            leading=leading,
            reads=reads,
            electable=electable,
            abilities=abilities,
            labels=measured.labels,
            coverage_floor=measured.coverage_floor,
            n_scored=len(measured.arms),
            cold=ruler is None,
        ),
    )
