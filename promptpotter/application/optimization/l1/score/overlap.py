"""The parent line, read on the ORIGIN PANEL — see ``domain/results.py::OverlapReading`` for what
the reading means and ``origin_panel`` for why the set is fixed at C0 rather than re-chosen."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.scoring.metrics import _compute_accuracy
from promptpotter.domain.results import (
    OverlapMember,
    OverlapReading,
    ParentStep,
    measured_cells,
    merge_known_outcomes,
    origin_panel,
    parent_line,
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
    """Put the whole parent line back on the origin panel, buying only the cells each member is
    missing, and stamp the reading onto *round_result*.

    Called after the election, the ruler extension and the panel gate, so every decision this
    round makes is already made before the first of these cells is bought. That ordering IS the
    quarantine; the fields it writes are outside `results` / `all_candidate_results` so the NEXT
    round's acquisition, ruler and floor cannot see them either.

    Usually one member pays — the arm this round crowned, on the panel cells it had not sat. A
    second one pays only where it predates the panel it is now read on, and then only once.
    """
    if not round_result.winner_id:
        return
    # The line INCLUDING this round: on a HELD round the subject is the retained parent, whose
    # coverage this round's parent re-score just widened, and on a won round it is the new arm.
    steps = parent_line([*cycle.rounds, round_result])
    if len(steps) < 2:
        return  # C0 alone — there is nothing yet to read it against
    ordered = sorted(steps, key=lambda s: s.round)
    origin = ordered[0]
    # Cells this cycle can still BUY. One the pool cannot serve would strand a member with no way
    # to be topped up onto it.
    panel = origin_panel(
        measured_cells(origin.rows),
        poolable={s.id for s in scoring_pool},
        size=cycle.config.sp_budget_ttest,
    )
    if not panel:
        logger.info(
            "overlap: round %d — the origin holds no cell this cycle can still buy, so no "
            "1-to-1 reading is possible",
            round_result.round,
        )
        return

    keep = set(panel)
    bought: dict[str, list[dict[str, Any]]] = {}
    rows_by_key = {s.key: s.rows for s in steps}
    for step in ordered:
        gaps = sorted(keep - measured_cells(step.rows))
        if not gaps:
            continue
        fresh = await _measure_gaps(cycle, gaps, scoring_pool, step=step)
        bought[step.candidate_id] = fresh
        rows_by_key[step.key] = merge_known_outcomes(step.rows, fresh)

    members = [_member(s, rows_by_key[s.key], keep) for s in ordered]
    round_result.overlap_results = bought
    round_result.overlap = OverlapReading(
        sample_ids=panel, members=members, measured=sum(len(r) for r in bought.values())
    )


def _member(step: ParentStep, rows: list[dict[str, Any]], keep: set[int]) -> OverlapMember:
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
    gaps: list[int],
    scoring_pool: list[Sample],
    *,
    step: ParentStep,
) -> list[dict[str, Any]]:
    """*step*'s OWN configuration on *gaps* — never the round subject's. A member measured under
    another arm's prompt is that arm's reading wearing this one's label."""
    from promptpotter.application.scoring.search_point_scorer import score_search_point

    schema = cycle.session.pipeline_schema
    assert step.opt_sp is not None, (
        "a parent line member carries the OSP its round was stamped with"
    )
    assert schema is not None, "the overlap pass requires pipeline_schema"
    want = set(gaps)
    samples = [s for s in scoring_pool if s.id in want]
    logger.info(
        "overlap: measuring %s on %d cell(s) of the origin panel it had not sat",
        step.label,
        len(samples),
    )
    results, _scores, _signal = await score_search_point(
        step.opt_sp.to_job_search_point(base_pipeline_params=step.pipeline_params, schema=schema),
        samples,
        cycle.session,
        # One run per (member, gap set) in the archive, so the pass is identifiable on disk and
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
            candidate_id=step.candidate_id,
            label=step.label,
            role=MeasurementRole.OVERLAP,
        ),
    )
    return list(cast("list[dict[str, Any]]", results))
