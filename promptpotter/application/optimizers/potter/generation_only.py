from __future__ import annotations

from promptpotter.application import optimizers
from promptpotter.application.bench.node_context import NodeContext
from promptpotter.application.optimizers.nodes import RoundContext
from promptpotter.application.optimizers.potter.knobs import L1GenerateKnobs
from promptpotter.application.optimizers.potter.l1.candidate_source import propose_l1_population
from promptpotter.application.optimizers.potter.l1.stats import round_facts
from promptpotter.application.runner.output import (
    write_hard_samples_artifacts,
    write_log_md,
    write_review_md,
)
from promptpotter.application.runner.round import (
    announce_opening,
    announce_population,
    bank_walk,
    settle_owed_outputs,
)
from promptpotter.domain.paired_reading import ReadingState
from promptpotter.domain.results import OverlapReading, RoundResult


async def run_generation_only_round(ctx: RoundContext) -> None:
    cycle, cb, round_num = ctx.cycle, ctx.callbacks, ctx.round_num
    session = cycle.session
    cb.on_round_entered(round_num)

    await settle_owed_outputs(ctx)
    walk = cycle.optimizer.plan.walk(cycle.population)
    opening = announce_opening(ctx, walk)
    generate = NodeContext[L1GenerateKnobs](ctx, optimizers.member("l1_generate"))
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
        overlap=OverlapReading.unpaired(ReadingState.NOT_ASKED, round_num, False),
        optimizer_state=cycle.close_state(round_num),
    )
    cb.on_round_close(
        generated.model_copy(update={"optimizer_facts": round_facts(generated)}),
        cycle.rounds,
        session.bank.slot_of,
        cycle.difficulty,
    )
    if session.state.cycle_id:
        write_hard_samples_artifacts(session, cycle)
        write_log_md(session, cycle.config)
        write_review_md(
            session,
            accuracy_ceiling=cycle.config.accuracy_ceiling,
            optimizer=cycle.optimizer,
            framing=cycle.framing,
        )


__all__ = ["run_generation_only_round"]
