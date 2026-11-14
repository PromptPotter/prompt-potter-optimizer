from __future__ import annotations

import asyncio
import functools
import logging
import time
from collections import Counter
from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from promptpotter.application.datasets.loaders import archive_entry
from promptpotter.application.intelligence.indexes.sample import SampleIndex
from promptpotter.application.scoring.metrics import compute_composite_fitness
from promptpotter.application.scoring.query_loop import QueryLoopState, Walk, run_walks
from promptpotter.domain.arms import degradation_reading
from promptpotter.domain.cell_fold import ScoreSummary
from promptpotter.domain.measurement_provenance import grade_answer
from promptpotter.domain.pass_kinds import PASSES
from promptpotter.domain.phases import WalkEnd
from promptpotter.domain.records.run_records import StopLoop
from promptpotter.domain.results_health import is_deprecated
from promptpotter.domain.sample import ArchiveEntry, FiledAnswer, Sample
from promptpotter.domain.scoring import NO_CELLS, CellSheet, GradedCell, MeasuredCell, WalkedCell
from promptpotter.domain.search_point import JobSearchPoint
from promptpotter.domain.validators import BrokenSignal, StopRule, StopSignal
from promptpotter.infrastructure.llm.heartbeat import CellBeat, heartbeat
from promptpotter.infrastructure.llm.telemetry import current_round
from promptpotter.infrastructure.run_scope import run_scope, scoped
from promptpotter.infrastructure.store.io import HeldLock
from promptpotter.infrastructure.store.measurement_archive import ReplayFeed
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import SendRefusedError
from promptpotter.shared.measurement_context import (
    MeasuredPass,
    MeasurementRole,
    measured_pass_context,
)

if TYPE_CHECKING:
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.scoring.query_loop import BlockRace, QueryLoopResult

logger = logging.getLogger(__name__)


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


def _split_off_deprecated_samples(
    cached_sample_results: dict[int, MeasuredCell],
) -> tuple[dict[int, MeasuredCell], dict[int, MeasuredCell]]:
    deprecated = {sid: r for sid, r in cached_sample_results.items() if is_deprecated(r)}
    kept = {sid: r for sid, r in cached_sample_results.items() if sid not in deprecated}
    return kept, deprecated


def replayable_on(
    dataset: list[Sample], reusable: dict[str, FiledAnswer]
) -> dict[int, MeasuredCell]:
    """A cell replays on its sample's key alone, whichever bank filed it."""
    return {s.id: held.cell for s in dataset if (held := reusable.get(s.key)) is not None}


def _resolve_prior_cache(
    dataset: list[Sample],
    *,
    feed: ReplayFeed | None,
) -> tuple[dict[int, MeasuredCell], dict[int, MeasuredCell]]:
    cached_sample_results: dict[int, MeasuredCell] = {}
    if feed is not None:
        cached_sample_results = replayable_on(dataset, feed.advance())

    cached_sample_results, deprecated_samples = _split_off_deprecated_samples(cached_sample_results)
    if deprecated_samples:
        logger.info(
            "Evicted %d deprecated prior result(s) (fatal warnings); will remeasure.",
            len(deprecated_samples),
        )
    return cached_sample_results, deprecated_samples


_CLAIM_POLL_S = 0.5


async def _claim_cell(
    sample: Sample,
    *,
    feed: ReplayFeed,
    session: Session,
    dataset: list[Sample],
    cached: dict[int, MeasuredCell],
) -> tuple[MeasuredCell | None, HeldLock | None]:
    waiting: asyncio.Task[None] | None = None
    try:
        while True:
            claim = feed.claim(sample.key)
            if claim is not None:
                try:
                    held = replayable_on(dataset, feed.advance())
                    cached.update({sid: f for sid, f in held.items() if not is_deprecated(f)})
                except BaseException:
                    claim.retire()
                    raise
                if (row := cached.get(sample.id)) is None:
                    return None, claim
                claim.retire()
                return row, None
            if waiting is None:
                waiting = asyncio.create_task(
                    heartbeat(
                        CellBeat(sample.id),
                        round_num=current_round(),
                        start_monotonic=time.monotonic(),
                        detail_fn=lambda: "another run is measuring this cell",
                    )
                )
            if session.state.control.pause_requested():
                raise asyncio.CancelledError("claim wait aborted by a pause")
            await asyncio.sleep(_CLAIM_POLL_S)
    finally:
        if waiting is not None:
            waiting.cancel()


def _walk_stop_signal(batch: QueryLoopResult) -> StopSignal | None:
    """An unsignalled stop outside ``_UNSIGNALLED_ENDS`` becomes a CANDIDATE-scoped signal, never the round's."""
    ended_on = batch.ended_on
    if ended_on is None or batch.stop_signal is not None or ended_on in _UNSIGNALLED_ENDS:
        return batch.stop_signal
    return _build_scoring_error_signal(batch.results)


@dataclass
class _ArchiveRecorder:
    session: Session
    search_point: JobSearchPoint
    kind: MeasurementRole
    entry: ArchiveEntry

    def sheet(self, results: Sequence[GradedCell]) -> CellSheet:
        return CellSheet(self.session.state.scoring.scorer.id, tuple(results))

    def scores(self, results: Sequence[GradedCell]) -> ScoreSummary:
        return compute_composite_fitness(
            self.sheet(results), self.session.state.scoring.scorer, self.session.pipeline_schema
        )

    def provenance(self, facts: MeasuredCell) -> str:
        return grade_answer(
            self.kind,
            facts,
            self.session.pipeline_schema,
            human_intervened=self.session.state.human_intervened,
        )

    def take(self, cell: GradedCell) -> GradedCell:
        """Files a cell with no address yet; a claimed one was filed as its claim retired."""
        return cell if cell.facts.answer is not None else self._file([cell])[0]

    def bank(self, cells: Sequence[GradedCell]) -> None:
        self._file(cells)

    def _file(self, cells: Sequence[GradedCell]) -> list[GradedCell]:
        if not cells:
            return list(cells)
        answers = self.session.store.archive.file_answers(
            self.entry,
            ((cell.facts, self.provenance(cell.facts)) for cell in cells),
            role=self.kind,
            created_at=utcnow_iso(),
        )
        return [
            cell.filed(replace(cell.facts, answer=answer))
            for cell, answer in zip(cells, answers, strict=True)
        ]


async def score_search_point(
    search_point: JobSearchPoint,
    dataset: list[Sample],
    session: Session,
    *,
    kind: MeasurementRole,
    sample_index: SampleIndex | None = None,
    measured: str | None,
    held: CellSheet = NO_CELLS,
) -> ScoredWalk:
    """``measured`` has NO default on purpose: it decides what the numbers MEAN."""
    (scored,) = (
        await score_search_points(
            {kind: (search_point, dataset, measured)},
            session,
            kind=kind,
            sample_index=sample_index,
            held=held,
        )
    ).values()
    return scored


async def score_search_points[K: Hashable](
    points: Mapping[K, tuple[JobSearchPoint, list[Sample], str | None]],
    session: Session,
    *,
    kind: MeasurementRole,
    sample_index: SampleIndex | None = None,
    held: CellSheet = NO_CELLS,
    blocks: BlockRace[K] | None = None,
) -> dict[K, ScoredWalk]:
    """Each point on its own rows, in ONE pass: the slowest walk sets the wall clock, never their sum;
    *blocks* races them as a round's arms are raced."""
    row = PASSES[kind]
    # Opened inside the filing too: the walk's cells run in a copy of the context taken here.
    with scoped(filed_as=row.spend or run_scope().filed_as):
        walks = {
            key: open_walk(
                search_point,
                dataset,
                session,
                kind=kind,
                sample_index=sample_index,
                measured=measured,
                held=held,
            )
            for key, (search_point, dataset, measured) in points.items()
        }
        try:
            cut = await run_walks(walks, session, keep_cut=row.keeps, blocks=blocks)
        except (KeyboardInterrupt, asyncio.CancelledError, StopLoop, SendRefusedError) as halt:
            if not row.keeps:
                raise
            return {key: _halted(walk, halt) for key, walk in walks.items()}
    if cut is not None:
        return {key: replace(close_walk(walk), halt=StopLoop(cut)) for key, walk in walks.items()}
    return {key: close_walk(walk) for key, walk in walks.items()}


def _halted(walk: Walk, halt: BaseException) -> ScoredWalk:
    """The rows it took before *halt*: a spent ceiling is kept by its walk (`keep_cut`) instead."""
    sheet = CellSheet(walk.ctx.scorer.id, tuple(walk.results))
    return ScoredWalk(sheet, walk.ctx.recorder.scores(walk.results), None, None, halt)


def reread_cells(
    search_point: JobSearchPoint, dataset: list[Sample], session: Session, *, kind: MeasurementRole
) -> CellSheet:
    """The rows a walk of *dataset* would take free off the archive, graded as it takes them."""
    ctx = open_walk(search_point, dataset, session, kind=kind, measured=None).ctx
    held = ctx.cached_sample_results
    return ctx.scorer.sheet(held[s.id].replayed() for s in dataset if s.id in ctx.rereads)


def open_walk(
    search_point: JobSearchPoint,
    dataset: list[Sample],
    session: Session,
    *,
    kind: MeasurementRole,
    checks: Sequence[StopRule] = (),
    sample_index: SampleIndex | None = None,
    measured: str | None,
    skip_at: int | None = None,
    held: CellSheet = NO_CELLS,
) -> Walk:
    """Writes nothing: an answer is filed as its cell is taken, so a walk never taken leaves no trace."""
    store = session.store
    pipeline_schema = session.pipeline_schema

    entry = archive_entry(
        search_point, dataset_hash=session.bank.dataset_hash, pipeline_schema=pipeline_schema
    )
    row = PASSES[kind]
    node_configs = [] if row.fresh else entry.node_configs
    # No node configs, no cell identity: every such searchpoint would share one claim per sample.
    feed = ReplayFeed(store.archive, node_configs) if node_configs else None
    cached_sample_results, deprecated_samples = _resolve_prior_cache(dataset, feed=feed)
    cell_keys = {} if feed is None else {s.id: feed.cell_key(s.key) for s in dataset}
    book = run_scope().book
    rereads = frozenset(
        s for s in cached_sample_results if book is None or book.replays_free(cell_keys[s])
    )

    measuring = None if measured is None else MeasuredPass(measured, kind)
    ctx = QueryLoopState(
        search_point=search_point,
        session=session,
        kind=kind,
        cached_sample_results=cached_sample_results,
        recorded=measuring if row.recorded else None,
        sample_index=sample_index,
        deprecated_samples=deprecated_samples,
        recorder=_ArchiveRecorder(
            session=session, search_point=search_point, kind=kind, entry=entry
        ),
        claim_cell=None
        if feed is None
        else functools.partial(
            _claim_cell,
            feed=feed,
            session=session,
            dataset=dataset,
            cached=cached_sample_results,
        ),
        cell_keys=cell_keys,
        rereads=rereads,
        held=held,
    )
    return Walk(
        dataset=dataset,
        ctx=ctx,
        checks=[*session.state.scoring.degradation_checks, *checks] if row.checks else checks,
        context=measured_pass_context(measuring),
        skip_at=skip_at,
    )


def close_walk(walk: Walk) -> ScoredWalk:
    outcome = walk.outcome
    assert outcome is not None, "close_walk needs a decided walk"
    results = outcome.results
    scores = walk.ctx.recorder.scores(results)
    stopped = None if len(results) == walk.n else outcome.ended_on
    sheet = CellSheet(walk.ctx.scorer.id, tuple(results))
    return ScoredWalk(sheet, scores, _walk_stop_signal(outcome), stopped)
