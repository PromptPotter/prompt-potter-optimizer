from __future__ import annotations

import math
from typing import TYPE_CHECKING

from promptpotter.application.bench.node_context import Lineage, Replay, Ruler
from promptpotter.application.optimizers.nodes import Selection
from promptpotter.application.optimizers.potter.knobs import ElectionKnobs
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
    from promptpotter.application.optimizers.nodes import MeasuredRound
    from promptpotter.domain.paired_reading import ArmKey
    from promptpotter.domain.scoring import CellSheet


def _theta(value: float | None) -> str:
    """A θ never fit prints ``n/a``, never ``0.000``, which is a real ability."""
    return "n/a" if value is None else f"{value:+.3f}"


def _lift(key: ArmKey, lift: float, abilities: RoundAbilities) -> str:
    """The lift's own se, the one admission divides by: never either arm's alone."""
    se = math.hypot(abilities.theta_se[key], abilities.bar[key][1])
    return (
        f"θ lift {lift:+.3f} logits, se {se:.3f} "
        f"(θ {_theta(abilities.theta.get(key))} vs parent {_theta(abilities.bar[key][0])})"
    )


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
    lift = _lift(leading, reads[leading], abilities)
    if winner is not None:
        runner = closest_to_bar(reads, beside=winner)
        return (
            f"{labels[winner]} won on {lift}"
            + (
                f"; runner-up {labels[runner]} at θ lift {reads[runner]:+.3f} logits"
                if runner is not None
                else ""
            )
            + scale
        )
    return f"no arm cleared the parent: best {labels[leading]} {lift}; {census}{scale}"


def _kept_for_a_fresh_read(
    leading: ArmKey, reads: Mapping[ArmKey, float], lineage: Lineage
) -> bool:
    """A held round's leader ahead of its parent, once: the cells that ranked it first are the ones
    its luck rode, so only the next round's panel says whether it clears the bar."""
    return reads[leading] > 0.0 and not any(leading in rr.arms for rr in lineage.rounds)


def elect_on_theta(ctx: NodeContext, measured: MeasuredRound) -> Selection:
    ruler = ctx.read(Ruler).difficulty.ruler
    lineage = ctx.read(Lineage)
    electable = measured.electable
    rows: dict[ArmKey, CellSheet] = {key: measured.pooled(key) for key in electable}
    admission = ctx.knobs(ElectionKnobs).admission
    selected, abilities = elect_round_winner(
        electable,
        rows,
        measured.pooled(measured.parent.key),
        measured.coverage_floor,
        ruler,
        admission=admission,
    )
    reads = readable_lifts(electable, measured.coverage_floor, abilities)
    leading = selected or closest_to_bar(reads)
    if selected is not None:
        ranked = [measured.arms[selected].individual]
    elif leading is not None and _kept_for_a_fresh_read(leading, reads, lineage):
        ranked = [lineage.parent, measured.arms[leading].individual]
    else:
        ranked = [lineage.parent]
    ctx.read(Replay).decide(
        PotterCheckpointKind.ROUND_WINNER,
        {
            "arms": electable,
            "round_num": ctx.round_num,
            "coverage_floor": measured.coverage_floor,
            "admission": admission,
            # Names which of the round's banked references the bar was; its rows are the close's.
            "parent": measured.parent.key,
            "crowned": sorted(measured.crowned),
        },
        selected,
    )
    return Selection(
        selected=selected,
        leading=leading,
        ranked=ranked,
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
