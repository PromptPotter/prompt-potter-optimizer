"""The adopted line, read on one shared set of cells — see ``domain/results.py::OverlapReading``
for what the reading means and why measuring the winner ALONE is unbiased."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.scoring.metrics import _compute_accuracy
from promptpotter.domain.results import (
    OverlapMember,
    OverlapReading,
    TrajectoryStep,
    choose_overlap_set,
    incumbent_key,
    measured_cells,
    merge_known_outcomes,
    winner_trajectory,
)
from promptpotter.shared.instrument import MeasuredCandidate, MeasurementRole

if TYPE_CHECKING:
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.domain.results import RoundResult
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import QueryMeasurement

logger = logging.getLogger(__name__)

__all__ = ["measure_overlap"]


async def measure_overlap(
    cycle: Cycle, round_result: RoundResult, scoring_pool: list[Sample]
) -> None:
    """Read this round's incumbent on the cells its whole line has already answered — buying only
    the ones it is missing — and stamp the reading onto *round_result*.

    Called after the election, the ruler extension and the panel gate, so every decision this
    round makes is already made before the first of these cells is bought. That ordering IS the
    quarantine; the fields it writes are outside `results` / `all_candidate_results` so the NEXT
    round's acquisition, ruler and floor cannot see them either.
    """
    winner_id = round_result.winner_id
    if not winner_id:
        return
    # The line INCLUDING this round: on a HELD round the subject is the retained incumbent, whose
    # coverage this round's parent re-score just widened, and on a won round it is the new arm.
    steps = winner_trajectory([*cycle.rounds, round_result])
    # By CONFIGURATION, never by id: an L2/L3 transition re-mints the incumbent's OSP, so this
    # round's `winner_id` can differ from the id the same configuration first arrived as.
    key = incumbent_key(round_result)
    subject = next((s for s in steps if s.key == key), None)
    others = [s for s in steps if s.key != key]
    if subject is None or not others:
        return  # C0 alone — there is nothing yet to read it against
    # Cells this cycle can still BUY. One the pool cannot serve would strand the set the moment
    # the line grew a member missing it, and there would be no way to top that member up.
    poolable = {s.id for s in scoring_pool}
    common = set.intersection(*(measured_cells(s.rows) for s in others)) & poolable
    if not common:
        logger.info(
            "trajectory overlap: round %d shares no measurable cell with the line before it — "
            "no 1-to-1 reading is possible on this round",
            round_result.round,
        )
        return

    have = measured_cells(subject.rows)
    prior = cycle.rounds[-1].overlap
    chosen = choose_overlap_set(
        common,
        already_measured=have,
        previous=prior.sample_ids if prior is not None else (),
        size=cycle.config.sp_budget_ttest,
    )
    gaps = [sid for sid in chosen if sid not in have]
    fresh = (
        await _measure_gaps(cycle, round_result, gaps, scoring_pool, subject=subject)
        if gaps
        else []
    )

    keep = set(chosen)
    rows_by_key = {s.key: s.rows for s in steps}
    rows_by_key[key] = merge_known_outcomes(subject.rows, fresh)
    members = [_member(s, rows_by_key[s.key], keep) for s in sorted(steps, key=lambda s: s.round)]
    round_result.overlap_results = fresh
    round_result.overlap = OverlapReading(sample_ids=chosen, members=members, measured=len(fresh))


def _member(step: TrajectoryStep, rows: list[dict[str, Any]], keep: set[int]) -> OverlapMember:
    on_set = [r for r in rows if (sid := r.get("sample_id")) is not None and int(sid) in keep]
    stats = _compute_accuracy(cast("list[QueryMeasurement]", on_set))
    return OverlapMember(
        round=step.round,
        candidate_id=step.candidate_id,
        label=step.label,
        accuracy=float(stats["accuracy"]),
        total=int(stats["total"]),
    )


async def _measure_gaps(
    cycle: Cycle,
    round_result: RoundResult,
    gaps: list[int],
    scoring_pool: list[Sample],
    *,
    subject: TrajectoryStep,
) -> list[dict[str, Any]]:
    from promptpotter.application.scoring.search_point_scorer import score_search_point

    opt_sp = round_result.opt_sp
    schema = cycle.session.pipeline_schema
    assert opt_sp is not None, "the overlap pass runs after the winner OSP is stamped"
    assert schema is not None, "the overlap pass requires pipeline_schema"
    want = set(gaps)
    samples = [s for s in scoring_pool if s.id in want]
    logger.info(
        "trajectory overlap: measuring %s on %d cell(s) its line had already answered",
        subject.label,
        len(samples),
    )
    results, _scores, _signal = await score_search_point(
        opt_sp.to_job_search_point(
            base_pipeline_params=round_result.pipeline_params, schema=schema
        ),
        samples,
        cycle.session,
        # One run per (winner, gap set) in the archive, so the pass is identifiable on disk and
        # a re-run of the same round replays it free rather than paying twice.
        label="line_overlap",
        # Vacuous by design, and this is the whole quarantine in two arguments. `opt_sp=None`
        # puts every searchpoint-aware evaluator on its fallback — the pass publishes a RATE, so
        # a composite is not wanted. `axes=None` withholds the AxisIndex: ingesting here would
        # feed `axis_memory`, which is an optimizer panel, from rows one arm alone paid for.
        opt_sp=None,
        axes=None,
        n_total_candidates=0,
        degradation_checks=None,
        on_sample_scored=None,
        on_sample_starting=None,
        measured=MeasuredCandidate(
            idx=0,
            candidate_id=subject.candidate_id,
            label=subject.label,
            role=MeasurementRole.OVERLAP,
        ),
    )
    return list(cast("list[dict[str, Any]]", results))
