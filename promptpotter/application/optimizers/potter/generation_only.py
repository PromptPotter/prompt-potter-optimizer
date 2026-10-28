from __future__ import annotations

from promptpotter.application import optimizers
from promptpotter.application.bench.node_context import NodeContext
from promptpotter.application.optimizers.nodes import RoundContext
from promptpotter.application.optimizers.potter.knobs import L1GenerateKnobs
from promptpotter.application.optimizers.potter.l1.candidate_source import propose_l1_population
from promptpotter.application.runner.round import (
    announce_opening,
    announce_population,
    bank_walk,
    close_round,
    settle_owed_outputs,
)
from promptpotter.domain.paired_reading import ReadingState
from promptpotter.domain.results import OverlapReading, RoundResult
from promptpotter.domain.results_health import assemble_prior_healths, compute_round_health


async def run_generation_only_round(ctx: RoundContext) -> None:
    cycle, cb, round_num = ctx.cycle, ctx.callbacks, ctx.round_num
    cb.on_round_entered(round_num)

    await settle_owed_outputs(ctx)
    walk = cycle.optimizer.plan.walk(cycle.population)
    opening = announce_opening(ctx, walk)
    generate = NodeContext[L1GenerateKnobs](ctx, optimizers.NODE_MEMBERS.get("l1_generate"))
    population = await propose_l1_population(generate)
    # Banked unmeasured: the resume after `--diag` measures these very arms.
    bank_walk(ctx, population.proposals)
    announce_population(ctx, opening, population)

    # No election: nothing was scored, so the round's arms stay proposed.
    generated = RoundResult(
        round=round_num,
        arms=dict(population.arms(round_num)),
        folds={},
        election=None,
        health=compute_round_health(
            results=[], prior_healths=assemble_prior_healths(cycle.rounds, round_num)
        ),
        overlap=OverlapReading.unpaired(ReadingState.NOT_ASKED, round_num, False),
    )
    cycle.absorb_round(generated)
    await close_round(cycle, generated, round_num, cycle.session, cb)
