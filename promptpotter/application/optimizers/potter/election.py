from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.application.optimizers.nodes import Selection
from promptpotter.application.optimizers.potter.records import PotterCheckpointKind
from promptpotter.application.scoring.selection import (
    closest_to_bar,
    elect_round_winner,
    parent_selection_bias,
    readable_lifts,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from promptpotter.application.bench.node_context import NodeContext
    from promptpotter.application.intelligence.rasch import RoundAbilities
    from promptpotter.application.optimizers.nodes import MeasuredRound, Proposals
    from promptpotter.application.optimizers.potter.knobs import ThetaElectionKnobs
    from promptpotter.domain.paired_reading import ArmKey
    from promptpotter.domain.scoring import CellSheet

__all__ = ["elect_on_theta"]


def _theta(value: float | None, *, signed: bool = True) -> str:
    """A θ never fit prints ``n/a``, never ``0.000``, which is a real ability."""
    if value is None:
        return "n/a"
    return f"{value:+.3f}" if signed else f"{value:.3f}"


def _lift(read: tuple[float, float]) -> str:
    """With a credit, BOTH halves: the first alone reports a round won on the credit as won on nothing."""
    over, credit = read
    if not credit:
        return f"{over:+.3f}"
    return (
        f"{over + credit:+.3f} = {over:+.3f} over the parent + {credit:.3f} parent selection bias"
    )


def _verdict_reason(
    *,
    winner: ArmKey | None,
    leading: ArmKey | None,
    reads: Mapping[ArmKey, tuple[float, float]],
    n_electable: int,
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
    census = f"{n_electable} of {n_scored} electable, coverage floor {coverage_floor}"
    if leading is None:
        return f"no arm could be read against the parent on the round's δ ruler; {census}{scale}"
    read = reads[leading]
    parent = abilities.bar[leading][0]
    numbers = (
        f"θ {_theta(abilities.theta.get(leading))} vs parent {_theta(parent)}",
        f"se {_theta(abilities.theta_se.get(leading), signed=False)}",
    )
    if winner is not None:
        runner = closest_to_bar(reads, beside=winner)
        return (
            f"{labels[winner]} won on θ lift {_lift(read)} ({numbers[0]}, {numbers[1]})"
            + (
                f"; runner-up {labels[runner]} at {sum(reads[runner]):+.3f}"
                if runner is not None
                else ""
            )
            + scale
        )
    return (
        f"no arm cleared the parent: best {labels[leading]} {numbers[0]} "
        f"(lift {_lift(read)}, {numbers[1]}); {census}{scale}"
    )


def elect_on_theta(
    ctx: NodeContext[ThetaElectionKnobs], measured: MeasuredRound, proposals: Proposals
) -> Selection:
    ruler = ctx.difficulty.ruler
    electable = measured.electable
    rows: dict[ArmKey, CellSheet] = {key: measured.rows[key] for key in electable}
    # Persisted with the decision: it derives from the ROUND HISTORY, which a replay may not hold.
    parent_bias = parent_selection_bias(ctx.rounds)
    selected, abilities = elect_round_winner(
        electable,
        rows,
        measured.parent_rows,
        measured.coverage_floor,
        ruler,
        parent_bias=parent_bias,
    )
    reads = readable_lifts(
        electable,
        rows,
        measured.parent_rows,
        measured.coverage_floor,
        abilities,
        parent_bias,
    )
    leading = selected or closest_to_bar(reads)
    ctx.decide(
        PotterCheckpointKind.ROUND_WINNER,
        {
            "arms": electable,
            "round_num": ctx.round_num,
            "coverage_floor": measured.coverage_floor,
            "parent_bias": parent_bias,
            # Names which of the round's banked references the bar was; its rows are the close's.
            "parent": measured.parent.key,
        },
        selected,
    )
    ctx.keep([ctx.parent if selected is None else measured.arms[selected].individual])
    return Selection(
        selected=selected,
        leading=leading,
        verdict_reason=_verdict_reason(
            winner=selected,
            leading=leading,
            reads=reads,
            n_electable=len(electable),
            abilities=abilities,
            labels=measured.labels,
            coverage_floor=measured.coverage_floor,
            n_scored=len(measured.arms),
            cold=ruler is None,
        ),
    )
