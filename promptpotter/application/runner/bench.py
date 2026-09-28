"""The bench's pass: one individual scored on the held-out bench set under the campaign's formula.
Run-level archive views skip ``MeasurementRole.BENCH``; row-level ones admit only ``admitted_ids``."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, NamedTuple

from promptpotter.application.scoring.search_point_scorer import score_search_point
from promptpotter.application.scoring.selection import matched_parent_lift, mean_fitness_ci
from promptpotter.domain.bench import BenchReading, BenchScore
from promptpotter.domain.phases import CampaignPhase, emit_phase
from promptpotter.domain.results import resolved_fitness
from promptpotter.shared.instrument import NO_ROUND_SLOT, MeasurementRole

if TYPE_CHECKING:
    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.run_observers import RunCallbacks
    from promptpotter.domain.scoring import QueryMeasurement
    from promptpotter.domain.search_point import JobSearchPoint
    from promptpotter.domain.spend import SpendRollup

__all__ = ["BenchPass", "bench_selection", "score_on_bench"]


class BenchPass(NamedTuple):
    reading: BenchReading
    rows: list[QueryMeasurement]
    # What the pass cost priced cold — replayed rows at what they would have cost — and the tokens
    # it billed: the price the search sets aside for the pass that grades its selection.
    incurred_usd: float
    billed_tokens: int


async def score_on_bench(
    session: Session,
    search_point: JobSearchPoint,
    *,
    round_num: int,
    cb: RunCallbacks,
    spend: SpendRollup,
) -> BenchPass:
    usd_before, tokens_before = spend.total_incurred_usd, spend.total_tokens_used
    # Bracketed without a round: the pass scores after the loop, and a round here would move the
    # dashboard's round back to the one being graded.
    emit_phase(cb.on_phase, CampaignPhase.BENCH, "enter")
    try:
        scored = await score_search_point(
            search_point,
            list(session.scoring.require_partition().bench),
            session,
            label=MeasurementRole.BENCH,
            measured=None,
            on_sample_scored=partial(cb.on_sample_scored, NO_ROUND_SLOT, 0),
            on_sample_starting=partial(cb.on_sample_started, NO_ROUND_SLOT, 0),
        )
    finally:
        emit_phase(cb.on_phase, CampaignPhase.BENCH, "exit")
    scores = scored.scores
    accuracy = scores["accuracy"]
    # The walk's served band is accuracy's; the headline is the composite, so its band is too.
    ci_lo, ci_hi = mean_fitness_ci(scored.results, grade="objective")
    reading = BenchReading(
        round=round_num,
        sp_hash=search_point.sp_hash(session.pipeline_schema),
        accuracy=accuracy,
        # The composite floors at 0.0 over no scoreable row; a headline over none has no value.
        composite_fitness=(
            None if accuracy is None else resolved_fitness(scores["composite_fitness"], accuracy)
        ),
        ci_lo=ci_lo,
        ci_hi=ci_hi,
        n_scored=len(scored.results),
        run_id=scored.run_id,
        stopped=scored.stopped,
    )
    return BenchPass(
        reading,
        list(scored.results),
        incurred_usd=spend.total_incurred_usd - usd_before,
        billed_tokens=spend.total_tokens_used - tokens_before,
    )


async def bench_selection(
    cycle: Cycle, session: Session, *, origin: BenchPass, cb: RunCallbacks, spend: SpendRollup
) -> BenchScore:
    """The selection is the pick the optimizer declared (``Cycle.selection``), graded here on rows
    it never read. Scored once where it is the origin itself."""
    picked, selected_sp = cycle.selection, cycle.selected_sp
    selected = (
        origin
        if selected_sp.sp_hash(session.pipeline_schema) == origin.reading.sp_hash
        else await score_on_bench(session, selected_sp, round_num=picked.round, cb=cb, spend=spend)
    )
    paired = matched_parent_lift(selected.rows, origin.rows, grade="objective")
    score = BenchScore(
        bench_size=len(session.scoring.require_partition().bench),
        origin=origin.reading,
        selected=selected.reading,
        lift=None if paired is None else paired[0],
        lift_ci_lo=None if paired is None else paired[1],
        lift_ci_hi=None if paired is None else paired[2],
    )
    emit_phase(cb.on_phase, CampaignPhase.BENCH, "scored", bench=score)
    return score
