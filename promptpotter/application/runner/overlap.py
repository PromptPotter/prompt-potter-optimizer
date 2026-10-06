"""The bench's best-so-far line, read on the ORIGIN PANEL — see ``domain/results.py::OverlapReading``
for what the reading means and ``origin_panel`` for why the set is fixed at C0 rather than re-chosen."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.scoring.metrics import _compute_accuracy
from promptpotter.application.scoring.search_point_scorer import score_search_point
from promptpotter.application.scoring.selection import paired_fitness
from promptpotter.domain.results import (
    LineStep,
    OverlapMember,
    OverlapReading,
    best_line,
    measured_cells,
    merge_known_outcomes,
    origin_panel,
)
from promptpotter.shared.instrument import MeasuredCandidate, MeasurementRole
from promptpotter.shared.statistics import paired_reading

if TYPE_CHECKING:
    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.domain.results import RoundResult
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import QueryMeasurement

logger = logging.getLogger(__name__)

__all__ = ["measure_overlap"]


async def measure_overlap(
    cycle: Cycle, round_result: RoundResult, scoring_pool: list[Sample]
) -> None:
    """Put the whole best-so-far line back on the origin panel, buying only the cells each member
    is missing, and stamp the reading onto *round_result*.

    Called after the election, the ruler extension and the panel gate, so every pick this
    round makes is already made before the first of these cells is bought. That ordering IS the
    quarantine; the fields it writes are outside `results` / `all_candidate_results` so the NEXT
    round's acquisition, ruler and floor cannot see them either.

    Usually one member pays — the individual this round made the new best, on the panel cells it
    had not sat. A second one pays only where it predates the panel it is now read on, and once.
    """
    if round_result.opt_sp is None:
        return
    # The line INCLUDING this round, which may have made its own result the new best.
    steps = best_line([*cycle.rounds, round_result])
    if len(steps) < 2:
        return  # C0 alone — there is nothing yet to read it against
    ordered = sorted(steps, key=lambda s: s.round)
    origin = ordered[0]
    # Cells this cycle can still BUY. One the pool cannot serve would strand a member with no way
    # to be topped up onto it.
    panel = origin_panel(
        measured_cells(origin.rows),
        poolable={s.id for s in scoring_pool},
        size=cycle.optimizer.round_cells(len(scoring_pool)),
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
        if fresh is None:
            # A member short of the set breaks the one claim the reading makes. What it did buy
            # is archived, so the next round's pass replays it rather than paying twice.
            return
        bought[step.candidate_id] = fresh
        rows_by_key[step.key] = merge_known_outcomes(step.rows, fresh)

    members = [_member(s, rows_by_key[s.key], keep) for s in ordered]
    newest, first = paired_fitness(
        _on_set(rows_by_key[ordered[-1].key], keep),
        _on_set(rows_by_key[origin.key], keep),
        grade="fitness",
    )
    _lead, lead_lo, lead_hi, _p, _n = paired_reading(newest, first)
    round_result.overlap_results = bought
    round_result.overlap = OverlapReading(
        sample_ids=panel,
        members=members,
        measured=sum(len(r) for r in bought.values()),
        lead_interval=(lead_lo, lead_hi) if lead_lo is not None and lead_hi is not None else None,
    )


def _on_set(rows: list[dict[str, Any]], keep: set[int]) -> list[QueryMeasurement]:
    return cast(
        "list[QueryMeasurement]",
        [r for r in rows if (sid := r.get("sample_id")) is not None and int(sid) in keep],
    )


def _member(step: LineStep, rows: list[dict[str, Any]], keep: set[int]) -> OverlapMember:
    stats = _compute_accuracy(_on_set(rows, keep))
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
    step: LineStep,
) -> list[dict[str, Any]] | None:
    """*step*'s OWN configuration on *gaps* — never the round subject's. A member measured under
    another arm's prompt is that arm's reading wearing this one's label. ``None`` when the pass
    stopped before its last gap."""

    schema = cycle.session.pipeline_schema
    assert step.opt_sp is not None, "a line member carries the OSP its round was stamped with"
    assert schema is not None, "the overlap pass requires pipeline_schema"
    want = set(gaps)
    samples = [s for s in scoring_pool if s.id in want]
    logger.info(
        "overlap: measuring %s on %d cell(s) of the origin panel it had not sat",
        step.label,
        len(samples),
    )
    scored = await score_search_point(
        step.opt_sp.to_job_search_point(
            base_pipeline_params=step.pipeline_params,
            schema=schema,
            framing=cycle.framing,
            demo=cycle.session.scoring.require_partition().demo,
        ),
        samples,
        cycle.session,
        # One run per (member, gap set) in the archive, so the pass is identifiable on disk and
        # a re-run of the same round replays it free rather than paying twice.
        label=MeasurementRole.OVERLAP,
        # No sample history: a report-only pass re-sends no degraded cached cell.
        sample_index=None,
        on_sample_scored=None,
        on_sample_starting=None,
        measured=MeasuredCandidate(
            idx=0,
            candidate_id=step.candidate_id,
            label=step.label,
            role=MeasurementRole.OVERLAP,
        ),
    )
    if scored.stopped is not None:
        logger.warning(
            "overlap: %s stopped after %d/%d cell(s) (%s); no reading this round",
            step.label,
            len(scored.results),
            len(samples),
            scored.stopped,
        )
        return None
    return list(cast("list[dict[str, Any]]", scored.results))
