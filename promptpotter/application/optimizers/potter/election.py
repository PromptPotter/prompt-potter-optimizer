"""Potter's selector: the arm with the strictly positive θ lift over the parent on the cycle's δ
ruler, stated in the numbers that decided it."""

from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.application.bench.resume_and_fork.decisions import record_decision
from promptpotter.application.intelligence.exploration import PARENT_ABILITY_ID
from promptpotter.application.optimizers.nodes import Selection, population_as
from promptpotter.application.optimizers.potter.records import PotterCheckpointKind
from promptpotter.application.optimizers.potter.state import L1Population
from promptpotter.application.scoring.selection import (
    elect_round_winner,
    lift_over_bar,
    parent_cells,
    parent_selection_bias,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from promptpotter.application.intelligence.exploration import RaschPosterior
    from promptpotter.application.optimizers.nodes import Measured, Population, RoundContext

__all__ = ["elect_on_theta"]


def _theta(value: float | None, *, signed: bool = True) -> str:
    """A θ that was never fit prints as ``n/a`` — never as ``0.000``, which is a real ability.
    ``signed=False`` for an SE, which has no direction to carry a leading ``+``."""
    if value is None:
        return "n/a"
    return f"{value:+.3f}" if signed else f"{value:.3f}"


def _lift(read: tuple[float, float]) -> str:
    """The lift admission read. With a credit it states both halves, since printing only the first
    reports a round won on the credit as won on nothing."""
    over, credit = read
    if not credit:
        return f"{over:+.3f}"
    return (
        f"{over + credit:+.3f} = {over:+.3f} over the parent + {credit:.3f} parent selection bias"
    )


def _verdict_reason(
    *,
    winner_id: str,
    electable: Sequence[str],
    abilities: RaschPosterior,
    parent_bias: float,
    labels: Mapping[str, str],
    coverage_floor: int,
    n_scored: int,
    ruler_n: int,
) -> str:
    """This round's outcome stated in the numbers that decided it.

    Written whether the round was won or held. The election admits on θ-lift over the parent's bar,
    so the sentence names that lift (`lift_over_bar`, the reading the election took), both
    abilities and the SE behind them — the operator reading a lower-accuracy winner needs the
    number it actually won on, and a held round needs to say which arm came closest and how far
    short. On a COLD ruler it says so: θ there is logit-accuracy on each arm's own subset, which is
    not the scale the word promises."""

    scale = "" if ruler_n else " (cold ruler — θ is logit-accuracy on each arm's own subset)"
    parent = abilities.theta.get(PARENT_ABILITY_ID)
    census = f"{len(electable)} of {n_scored} electable, coverage floor {coverage_floor}"
    ranked = sorted(
        (
            (sum(read), read, cid)
            for cid in electable
            if (read := lift_over_bar(abilities, cid, parent_bias)) is not None
        ),
        reverse=True,
    )
    if not ranked:
        return f"no arm could be read against the parent on the round's δ ruler; {census}{scale}"
    if winner_id:
        read = next(r for _, r, cid in ranked if cid == winner_id)
        runner = next(
            (
                f"; runner-up {labels.get(cid, cid[:12])} at {total:+.3f}"
                for total, _, cid in ranked
                if cid != winner_id
            ),
            "",
        )
        return (
            f"{labels.get(winner_id, winner_id[:12])} won on θ lift {_lift(read)} "
            f"(θ {_theta(abilities.theta.get(winner_id))} vs parent {_theta(parent)}, "
            f"se {_theta(abilities.theta_se.get(winner_id), signed=False)}){runner}{scale}"
        )
    _, read, best = ranked[0]
    return (
        f"no arm cleared the parent: best {labels.get(best, best[:12])} "
        f"θ {_theta(abilities.theta.get(best))} vs parent {_theta(parent)} "
        f"(lift {_lift(read)}, se {_theta(abilities.theta_se.get(best), signed=False)}); "
        f"{census}{scale}"
    )


def elect_on_theta(
    ctx: RoundContext, measured: Measured, population: Population, *, node: str
) -> Selection:
    cycle = ctx.cycle
    ruler = cycle.difficulty.ruler
    scores = list(measured.scores)
    cs_by_id = {cs.candidate_id: i for i, cs in enumerate(scores)}
    electable = [ind.lineage.id for ind in measured.electable]
    # Persisted with the decision, as ``coverage_floor`` is: it derives from the ROUND HISTORY,
    # which a replay does not necessarily hold.
    parent_bias = parent_selection_bias(cycle.rounds)
    winner_id, abilities = elect_round_winner(
        electable,
        measured.rows,
        measured.parent_rows,
        measured.coverage_floor,
        ruler,
        parent_bias=parent_bias,
    )
    # The election's own fit, never a second one, and none on a cold ruler, where θ is
    # logit-accuracy on each arm's own subset; ``electable`` stays whole as the decision's input.
    for cid in electable if ruler is not None else ():
        theta_c = abilities.theta.get(cid)
        if theta_c is None:
            continue
        cs_idx = cs_by_id[cid]
        # θ and its SE only: the band and `theta_caveat` are stamped where the arm is measured,
        # which reaches every arm and round 0 — this loop reaches the electable arms alone.
        scores[cs_idx] = scores[cs_idx].model_copy(
            update={"theta": theta_c, "theta_se": abilities.theta_se[cid]}
        )
    record_decision(
        cycle.pending_decisions,
        PotterCheckpointKind.ROUND_WINNER,
        {
            "candidate_ids": electable,
            "round_num": ctx.round_num,
            "coverage_floor": measured.coverage_floor,
            "parent_bias": parent_bias,
        },
        winner_id,
        node=node,
        # The PARENT this election ranked against; the round document cannot carry it, since on a
        # won round `results` is the winner's rows.
        data={"parent_cells": parent_cells(measured.parent_rows)},
        round=ctx.round_num,
    )
    return Selection(
        selected_id=winner_id,
        scores=scores,
        verdict_reason=_verdict_reason(
            winner_id=winner_id,
            electable=electable,
            abilities=abilities,
            parent_bias=parent_bias,
            labels={cs.candidate_id: cs.label for cs in scores},
            coverage_floor=measured.coverage_floor,
            n_scored=len(measured.scored),
            ruler_n=len(ruler.delta) if ruler is not None else 0,
        ),
        payload=population_as(population, L1Population).payload,
    )
