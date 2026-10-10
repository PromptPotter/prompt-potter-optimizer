from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.application.optimizers.nodes import Selection
from promptpotter.application.optimizers.potter.records import PotterCheckpointKind
from promptpotter.application.scoring.selection import (
    closest_to_bar,
    elect_round_winner,
    parent_cells,
    parent_selection_bias,
    readable_lifts,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from promptpotter.application.bench.node_context import NodeContext
    from promptpotter.application.intelligence.rasch import RoundAbilities
    from promptpotter.application.optimizers.nodes import Measured, Proposals
    from promptpotter.application.optimizers.potter.knobs import ThetaElectionKnobs

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
    winner_id: str,
    leading_id: str,
    reads: Mapping[str, tuple[float, float]],
    n_electable: int,
    abilities: RoundAbilities,
    labels: Mapping[str, str],
    coverage_floor: int,
    n_scored: int,
    ruler_n: int,
) -> str:
    scale = "" if ruler_n else " (cold ruler — θ is logit-accuracy on each arm's own subset)"
    parent = abilities.parent[0] if abilities.parent is not None else None
    census = f"{n_electable} of {n_scored} electable, coverage floor {coverage_floor}"
    if not leading_id:
        return f"no arm could be read against the parent on the round's δ ruler; {census}{scale}"
    read = reads[leading_id]
    numbers = (
        f"θ {_theta(abilities.theta.get(leading_id))} vs parent {_theta(parent)}",
        f"se {_theta(abilities.theta_se.get(leading_id), signed=False)}",
    )
    if winner_id:
        runner = closest_to_bar(reads, beside=winner_id)
        return (
            f"{labels[winner_id]} won on θ lift {_lift(read)} ({numbers[0]}, {numbers[1]})"
            + (f"; runner-up {labels[runner]} at {sum(reads[runner]):+.3f}" if runner else "")
            + scale
        )
    return (
        f"no arm cleared the parent: best {labels[leading_id]} {numbers[0]} "
        f"(lift {_lift(read)}, {numbers[1]}); {census}{scale}"
    )


def elect_on_theta(
    ctx: NodeContext[ThetaElectionKnobs], measured: Measured, proposals: Proposals
) -> Selection:
    ruler = ctx.difficulty.ruler
    scores = list(measured.scores)
    electable = [ind.id for ind in measured.electable]
    # Persisted with the decision: it derives from the ROUND HISTORY, which a replay may not hold.
    parent_bias = parent_selection_bias(ctx.rounds)
    winner_id, abilities = elect_round_winner(
        electable,
        measured.rows,
        measured.parent_rows,
        measured.coverage_floor,
        ruler,
        parent_bias=parent_bias,
    )
    reads = readable_lifts(
        electable,
        measured.rows,
        measured.parent_rows,
        measured.coverage_floor,
        abilities,
        parent_bias,
    )
    leading_id = winner_id or closest_to_bar(reads)
    ctx.decide(
        PotterCheckpointKind.ROUND_WINNER,
        {
            "candidate_ids": electable,
            "round_num": ctx.round_num,
            "coverage_floor": measured.coverage_floor,
            "parent_bias": parent_bias,
        },
        winner_id,
        # The round file cannot carry the PARENT: on a won round `results` is the winner's rows.
        data={"parent_cells": parent_cells(measured.parent_rows)},
    )
    crowned = next((ind for ind in measured.electable if ind.id == winner_id), ctx.parent)
    ctx.keep([crowned])
    return Selection(
        selected_id=winner_id,
        leading_id=leading_id,
        scores=scores,
        verdict_reason=_verdict_reason(
            winner_id=winner_id,
            leading_id=leading_id,
            reads=reads,
            n_electable=len(electable),
            abilities=abilities,
            labels={cs.candidate_id: cs.label for cs in scores},
            coverage_floor=measured.coverage_floor,
            n_scored=len(measured.scored),
            ruler_n=len(ruler.delta) if ruler is not None else 0,
        ),
    )
