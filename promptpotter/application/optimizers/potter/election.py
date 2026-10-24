from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.application.bench.node_context import Lineage, Ruler
from promptpotter.application.optimizers.nodes import Selection
from promptpotter.application.optimizers.potter.knobs import PoBBKnobs
from promptpotter.application.optimizers.potter.records import PotterCheckpointKind
from promptpotter.application.scoring.selection import (
    closest_to_bar,
    elect_round_winner,
    readable_lifts,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from promptpotter.application.bench.node_context import NodeContext
    from promptpotter.application.intelligence.rasch import RoundAbilities
    from promptpotter.application.optimizers.nodes import MeasuredRound, Proposals
    from promptpotter.domain.paired_reading import ArmKey
    from promptpotter.domain.scoring import CellSheet
    from promptpotter.domain.strict_model import StrictModel


def _theta(value: float | None, *, signed: bool = True) -> str:
    """A θ never fit prints ``n/a``, never ``0.000``, which is a real ability."""
    if value is None:
        return "n/a"
    return f"{value:+.3f}" if signed else f"{value:.3f}"


def _verdict_reason(
    *,
    winner: ArmKey | None,
    leading: ArmKey | None,
    reads: Mapping[ArmKey, float],
    electable: Sequence[ArmKey],
    abilities: RoundAbilities,
    labels: Mapping[ArmKey, str],
    coverage_floor: int,
    n_scored: int,
    cold: bool,
) -> str:
    scale = (
        " (cold ruler — θ is logit-accuracy on the cells each arm shares with the parent)"
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
    lift = reads[leading]
    parent = abilities.bar[leading][0]
    numbers = (
        f"θ {_theta(abilities.theta.get(leading))} vs parent {_theta(parent)}",
        f"se {_theta(abilities.theta_se.get(leading), signed=False)}",
    )
    if winner is not None:
        runner = closest_to_bar(reads, beside=winner)
        return (
            f"{labels[winner]} won on θ lift {lift:+.3f} ({numbers[0]}, {numbers[1]})"
            + (
                f"; runner-up {labels[runner]} at {reads[runner]:+.3f}"
                if runner is not None
                else ""
            )
            + scale
        )
    return (
        f"no arm cleared the parent: best {labels[leading]} {numbers[0]} "
        f"(lift {lift:+.3f}, {numbers[1]}); {census}{scale}"
    )


def elect_on_theta(
    ctx: NodeContext[StrictModel], measured: MeasuredRound, proposals: Proposals
) -> Selection:
    ruler = ctx.read(Ruler).difficulty.ruler
    lineage = ctx.read(Lineage)
    electable = measured.electable
    rows: dict[ArmKey, CellSheet] = {key: measured.pooled(key) for key in electable}
    epsilon = ctx.knobs_of(PoBBKnobs).epsilon
    selected, abilities = elect_round_winner(
        electable,
        rows,
        measured.pooled(measured.parent.key),
        measured.coverage_floor,
        ruler,
        epsilon=epsilon,
    )
    reads = readable_lifts(electable, measured.coverage_floor, abilities)
    leading = selected or closest_to_bar(reads)
    ctx.decide(
        PotterCheckpointKind.ROUND_WINNER,
        {
            "arms": electable,
            "round_num": ctx.round_num,
            "coverage_floor": measured.coverage_floor,
            "epsilon": epsilon,
            # Names which of the round's banked references the bar was; its rows are the close's.
            "parent": measured.parent.key,
            "crowned": sorted(measured.crowned),
        },
        selected,
    )
    ctx.keep([lineage.parent if selected is None else measured.arms[selected].individual])
    return Selection(
        selected=selected,
        leading=leading,
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
