"""Generation-only round — L1 variants without scoring, the round ``--diag`` ends on; the round
document carries ``status='generation_only'``, the word every reader of it already uses (``output.py::gen_only_rounds``,
``review_md.py::_is_generation_only``), no scoreboard and no accuracy."""

from __future__ import annotations

from promptpotter.application.bench.cycle import Cycle
from promptpotter.application.initialization.session import Session
from promptpotter.application.optimizers.nodes import RoundContext
from promptpotter.application.optimizers.potter.l1.candidate_source import (
    generate_or_load_candidates,
)
from promptpotter.application.optimizers.potter.l1.stats import round_facts
from promptpotter.application.optimizers.potter.state import PotterState
from promptpotter.application.run_observers import RunCallbacks
from promptpotter.application.runner.output import (
    write_hard_samples_artifacts,
    write_log_md,
    write_review_md,
)
from promptpotter.application.runner.round import announce_opening, announce_population
from promptpotter.domain.results import RoundResult
from promptpotter.domain.run_records import PhaseRecord
from promptpotter.shared.errors import graceful


async def run_generation_only_round(
    cycle: Cycle,
    state: PotterState,
    session: Session,
    cb: RunCallbacks,
    round_num: int,
) -> None:
    cb.set_round(round_num)
    if (ledger := session.state.ledger) is not None:
        ledger.append(PhaseRecord(phase="round", event="enter", round=round_num))

    ctx = RoundContext(cycle=cycle, round_num=round_num, callbacks=cb)
    opening = announce_opening(ctx, "l1_generate")
    candidates, yield_stats = await generate_or_load_candidates(round_num, cycle, state)
    announce_population(ctx, opening, candidates, n_cells=0)

    if session.state.cycle_id:
        with graceful("Generation-only round_data write failed"):
            # A generation-only round IS a round — same document, `status` telling every
            # reader it was never scored. The scoring scalars below are structural zeros
            # (no measurement happened), not measurements of zero; `health` and the
            # matched-parent pair stay None because a degradation verdict and an
            # origin-restricted-to-the-winner's-samples both need scored samples.
            axes = state.axes(cycle)
            generated = RoundResult(
                round=round_num,
                label="diag_gen_only",
                status="generation_only",
                accuracy=0.0,
                composite_fitness=0.0,
                total=0,
                improved=False,
                prompt_fields=cycle.opt_sp.prompt_field_dict(),
                candidates_scored=0,
                selected_labels=[],
                opt_sp=cycle.opt_sp,
                optimizer_state=state.snapshot(
                    l1_yield=yield_stats.l1_yield,
                    l1_parse_failure=yield_stats.l1_parse_failure,
                    prompt_hashes={},
                    axis_memory_peaked=sorted(axes.peaked_axes()) if axes else [],
                ),
            )
            generated.optimizer_facts = round_facts(generated)
            session.store.campaigns.save_round_file(session.hop, generated)
        write_hard_samples_artifacts(session, cycle)
        write_log_md(session, cycle.config)
        write_review_md(session, cycle)

    if (ledger := session.state.ledger) is not None:
        ledger.append(
            PhaseRecord(
                phase="round",
                event="complete",
                round=round_num,
                payload={"status": "generation_only", "n_candidates": len(candidates)},
            )
        )


__all__ = ["run_generation_only_round"]
