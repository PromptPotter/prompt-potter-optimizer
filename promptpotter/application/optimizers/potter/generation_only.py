from __future__ import annotations

from promptpotter.application.bench.node_context import NodeContext
from promptpotter.application.optimizers.nodes import RoundContext, round_state
from promptpotter.application.optimizers.potter.knobs import L1GenerateKnobs
from promptpotter.application.optimizers.potter.l1.candidate_source import propose_l1_population
from promptpotter.application.optimizers.potter.l1.stats import round_facts
from promptpotter.application.optimizers.potter.state import potter_state
from promptpotter.application.runner.output import (
    write_hard_samples_artifacts,
    write_log_md,
    write_review_md,
)
from promptpotter.application.runner.round import announce_opening, announce_population
from promptpotter.domain.paired_reading import ReadingState
from promptpotter.domain.results import OverlapReading, RoundResult


async def run_generation_only_round(ctx: RoundContext) -> None:
    cycle, cb, round_num = ctx.cycle, ctx.callbacks, ctx.round_num
    session = cycle.session
    cb.set_round(round_num)
    cb.on_round_entered(round_num)

    opening = announce_opening(ctx)
    population = await propose_l1_population(
        NodeContext[L1GenerateKnobs](ctx, "l1_generate"), potter_state(cycle.working_state)
    )
    candidates = population.proposals
    announce_population(ctx, opening, candidates, n_cells=0)

    generated = RoundResult(
        round=round_num,
        # Nothing was scored, so the round ends on the parent it opened with.
        label=cycle.rounds[-1].label,
        generation_only=True,
        accuracy=None,
        composite_fitness=None,
        total=0,
        improved=False,
        elects_on=cycle.optimizer.elects_on,
        overlap=OverlapReading.unpaired(ReadingState.NOT_ASKED, round_num, False),
        prompt_fields=cycle.opt_sp.prompt_field_dict(),
        candidates_scored=0,
        selected_labels=[],
        leading_label=None,
        opt_sp=cycle.opt_sp,
        optimizer_state=round_state(
            cycle.optimizer, cycle.working_state.round_payload(), cycle.population
        ),
    )
    cb.on_round_close(generated.model_copy(update={"optimizer_facts": round_facts(generated)}))
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
