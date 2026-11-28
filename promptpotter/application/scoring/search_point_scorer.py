from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from promptpotter.application.run_stop import StopLoop
from promptpotter.application.scoring.query_loop import Walk, open_walk, run_walks
from promptpotter.domain.arms import degradation_reading
from promptpotter.domain.cell_fold import ScoreSummary
from promptpotter.domain.pass_kinds import PASSES
from promptpotter.domain.phases import WalkEnd
from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import CellSheet, GradedCell, WalkedCell
from promptpotter.domain.search_point import JobSearchPoint
from promptpotter.domain.validators import BrokenSignal, StopSignal
from promptpotter.infrastructure.run_scope import run_scope, scoped
from promptpotter.shared.errors import SendRefusedError
from promptpotter.shared.measurement_context import MeasurementRole

if TYPE_CHECKING:
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.scoring.query_loop import QueryLoopResult
    from promptpotter.application.scoring.race_protocol import Decider


@dataclass(frozen=True)
class ScoredWalk:
    """``stopped`` is ``None`` once the walk took every cell, whatever decided it there."""

    sheet: CellSheet
    scores: ScoreSummary
    signal: StopSignal | None
    stopped: WalkEnd | None
    # The run stop a keeping pass ended short on (`Pass.keeps`): its caller raises it.
    halt: BaseException | None = None

    @property
    def cells(self) -> list[WalkedCell]:
        return self.sheet.addresses()


SCORING_ERROR_ABORT = "scoring_error_abort"

_UNSIGNALLED_ENDS = frozenset({WalkEnd.SKIP, WalkEnd.BUDGET})


def _build_scoring_error_signal(results: Sequence[GradedCell]) -> BrokenSignal:
    # An abort pads no synthetic tail, so the row that aborted the walk is the last error.
    real_errors = [cell.facts for cell in results if cell.facts.errored]
    warning_types = Counter(str(facts.error_category) for facts in real_errors)
    return BrokenSignal(
        check_name=SCORING_ERROR_ABORT,
        outcome="broken",
        check_result=degradation_reading(
            source=SCORING_ERROR_ABORT,
            degraded_count=len(real_errors),
            total_scored=len(results),
            warning_types=warning_types,
            dominant_warning=str(real_errors[-1].error),
        ),
    )


def _walk_stop_signal(batch: QueryLoopResult) -> StopSignal | None:
    """An unsignalled stop outside ``_UNSIGNALLED_ENDS`` becomes a CANDIDATE-scoped signal, never the round's."""
    ended_on = batch.ended_on
    if ended_on is None or batch.stop_signal is not None or ended_on in _UNSIGNALLED_ENDS:
        return batch.stop_signal
    return _build_scoring_error_signal(batch.results)


async def score_search_point(
    search_point: JobSearchPoint,
    dataset: list[Sample],
    session: Session,
    *,
    kind: MeasurementRole,
    measured: str | None,
) -> ScoredWalk:
    """``measured`` has NO default on purpose: it decides what the numbers MEAN."""
    (scored,) = (
        await score_search_points(
            {kind: (search_point, dataset, measured)},
            session,
            kind=kind,
        )
    ).values()
    return scored


async def score_search_points[K: Hashable](
    points: Mapping[K, tuple[JobSearchPoint, list[Sample], str | None]],
    session: Session,
    *,
    kind: MeasurementRole,
    race: Decider[K] | None = None,
) -> dict[K, ScoredWalk]:
    """Each point on its own rows, in ONE pass: the slowest walk sets the wall clock, never their sum;
    *race* races them as a round's arms are raced."""
    row = PASSES[kind]
    # Opened inside the filing too: the walk's cells run in a copy of the context taken here.
    with scoped(filed_as=row.spend or run_scope().filed_as):
        walks = {
            key: open_walk(
                search_point,
                dataset,
                session,
                kind=kind,
                measured=measured,
            )
            for key, (search_point, dataset, measured) in points.items()
        }
        try:
            cut = (await run_walks(walks, session, keep_cut=row.keeps, race=race)).stop
        except (KeyboardInterrupt, asyncio.CancelledError, StopLoop, SendRefusedError) as halt:
            if not row.keeps:
                raise
            return {key: _halted(walk, halt) for key, walk in walks.items()}
    if cut is not None:
        return {key: replace(close_walk(walk), halt=cut) for key, walk in walks.items()}
    return {key: close_walk(walk) for key, walk in walks.items()}


def _halted(walk: Walk, halt: BaseException) -> ScoredWalk:
    """The rows it took before *halt*: a spent ceiling is kept by its walk (`keep_cut`) instead."""
    sheet = walk.ctx.scorer.graded(walk.results)
    return ScoredWalk(sheet, walk.ctx.recorder.scores(walk.results), None, None, halt)


def reread_cells(
    search_point: JobSearchPoint, dataset: list[Sample], session: Session, *, kind: MeasurementRole
) -> CellSheet:
    """The rows a walk of *dataset* would take free off the archive, graded as it takes them."""
    ctx = open_walk(search_point, dataset, session, kind=kind, measured=None).ctx
    return ctx.scorer.sheet(
        replay.replayed() for s in dataset if ctx.rereads(s) and (replay := ctx.replay(s))
    )


def close_walk(walk: Walk) -> ScoredWalk:
    outcome = walk.outcome
    assert outcome is not None, "close_walk needs a decided walk"
    results = outcome.results
    scores = walk.ctx.recorder.scores(results)
    stopped = None if len(results) == walk.budget else outcome.ended_on
    sheet = walk.ctx.scorer.graded(results)
    return ScoredWalk(sheet, scores, _walk_stop_signal(outcome), stopped)
