from __future__ import annotations

import asyncio
import functools
import logging
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from promptpotter.application.datasets.loaders import archive_entry
from promptpotter.application.scoring.metrics import compute_composite_fitness
from promptpotter.application.scoring.query_loop import ArmSlot, QueryLoopState, Walk, run_walks
from promptpotter.domain.measurement_provenance import grade_answer
from promptpotter.domain.phases import WalkEnd
from promptpotter.domain.results import ArmOutcome, ScoreSummary, degradation_reading
from promptpotter.domain.results_health import is_deprecated
from promptpotter.domain.scoring import CellSheet, GradedCell, MeasuredCell, WalkedCell
from promptpotter.domain.spend import ROLE_SPEND_KIND
from promptpotter.domain.validators import BrokenSignal, StopRule, StopSignal
from promptpotter.infrastructure.llm.heartbeat import heartbeat
from promptpotter.infrastructure.llm.telemetry import _CURRENT_ROUND, filed_as
from promptpotter.infrastructure.store import archive_queries
from promptpotter.infrastructure.store.measurement_archive import ReplayFeed
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import DatasetIdentityError
from promptpotter.shared.measurement_context import MeasuredCandidate, measured_candidate_context

if TYPE_CHECKING:
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.intelligence.indexes.sample import SampleIndex
    from promptpotter.application.scoring.query_loop import QueryLoopResult
    from promptpotter.domain.measurement_provenance import RunSource
    from promptpotter.domain.sample import ArchiveEntry, FiledAnswer, Sample
    from promptpotter.domain.search_point import JobSearchPoint
    from promptpotter.infrastructure.store.measurement_archive import CellClaim

logger = logging.getLogger(__name__)

__all__ = [
    "SCORING_ERROR_ABORT",
    "ScoredWalk",
    "close_walk",
    "open_walk",
    "reread_cells",
    "score_search_point",
]


@dataclass(frozen=True)
class ScoredWalk:
    """``stopped`` is ``None`` once the walk took every cell, whatever decided it there."""

    sheet: CellSheet
    scores: ScoreSummary
    signal: StopSignal | None
    stopped: WalkEnd | None

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
        outcome=ArmOutcome.BROKEN,
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


def _replayable_on(
    dataset: list[Sample],
    reusable: dict[str, FiledAnswer],
    dataset_name: str | None,
) -> dict[int, MeasuredCell]:
    """Readers key by ``(dataset_name, sample_id)``: a slot holding another sample means rows re-cut under a used name."""
    here = {s.id: s for s in dataset}
    for prior in reusable.values():
        if not dataset_name or prior.dataset_name != dataset_name:
            continue
        stored = prior.cell
        sample = here.get(stored.sample_id)
        if sample is not None and sample.key != stored.sample_key:
            raise DatasetIdentityError(
                dataset_name=dataset_name,
                sample_id=stored.sample_id,
                stored=(stored.query, stored.ground_truth),
                current=(sample.query, sample.ground_truth or ""),
            )
    return {
        s.id: replace(held.cell, sample_id=s.id)
        for s in dataset
        if (held := reusable.get(s.key)) is not None
    }


def _resolve_prior_cache(
    dataset: list[Sample],
    session: Session,
    *,
    feed: ReplayFeed | None,
    label: str,
) -> tuple[dict[int, MeasuredCell], dict[int, MeasuredCell]]:
    cached_sample_results: dict[int, MeasuredCell] = {}
    if feed is not None:
        cached_sample_results = _replayable_on(dataset, feed.advance(), session.dataset_name)

    cached_sample_results, deprecated_samples = _split_off_deprecated_samples(cached_sample_results)
    if deprecated_samples:
        logger.info(
            "Evicted %d deprecated prior result(s) (fatal warnings); will remeasure.",
            len(deprecated_samples),
        )

    if replays := len(cached_sample_results):
        total = len(dataset)
        logger.debug(
            "%s cache: %d/%d already measured for this JSP — will replay %d, measure %d fresh.",
            label,
            replays,
            total,
            replays,
            total - replays,
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
) -> tuple[MeasuredCell | None, CellClaim | None]:
    waiting: asyncio.Task[None] | None = None
    try:
        while True:
            claim = feed.claim(sample.key)
            if claim is not None:
                try:
                    replayable = _replayable_on(dataset, feed.advance(), session.dataset_name)
                    cached.update({sid: f for sid, f in replayable.items() if not is_deprecated(f)})
                except BaseException:
                    claim.release()
                    raise
                if (row := cached.get(sample.id)) is None:
                    return None, claim
                claim.release()
                return row, None
            if (measured := feed.claimed_cell(sample.key)) is not None:
                return replace(measured, sample_id=sample.id), None
            if waiting is None:
                waiting = asyncio.create_task(
                    heartbeat(
                        session.state.ledger,
                        call_id=f"scoring:{sample.id}",
                        node="backend_scoring",
                        round_num=_CURRENT_ROUND.get(),
                        start_monotonic=time.monotonic(),
                        detail_fn=lambda: "another run is measuring this cell",
                    )
                )
            if session.control.pause_requested():
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
    source: RunSource
    search_point: JobSearchPoint
    role: str
    entry: ArchiveEntry

    def sheet(self, results: Sequence[GradedCell]) -> CellSheet:
        return CellSheet(self.session.scoring.require_scorer().id, tuple(results))

    def scores(self, results: Sequence[GradedCell]) -> ScoreSummary:
        return compute_composite_fitness(
            self.sheet(results), pipeline_schema=self.session.pipeline_schema
        )

    def provenance(self, facts: MeasuredCell) -> str:
        return grade_answer(
            self.source,
            facts,
            self.session.pipeline_schema,
            human_intervened=self.session.human_intervened,
        )

    def take(self, cell: GradedCell) -> GradedCell:
        # A replay with no address is a row a concurrent walk measured and may never take.
        if not cell.facts.cached or cell.facts.answer is None:
            cell = self._file([cell])[0]
        if (answer := cell.facts.answer) is not None:
            archive_queries.note_walked(answer)
        return cell

    def bank(self, cells: Sequence[GradedCell]) -> None:
        self._file(cells)

    def _file(self, cells: Sequence[GradedCell]) -> list[GradedCell]:
        if not cells or not self.session.backend_id:
            return list(cells)
        answers = self.session.store.archive.file_answers(
            self.entry,
            ((cell.facts, self.provenance(cell.facts)) for cell in cells),
            role=self.role,
            source=self.source.value,
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
    label: str,
    slot: ArmSlot | None = None,
    sample_index: SampleIndex | None = None,
    measured: MeasuredCandidate | None,
    force_fresh: bool = False,
) -> ScoredWalk:
    """``measured`` has NO default on purpose: it decides what the numbers MEAN."""
    # Opened inside the filing too: the walk's cells run in a copy of the context taken here.
    with filed_as(ROLE_SPEND_KIND.get(label)):
        walk = open_walk(
            search_point,
            dataset,
            session,
            label=label,
            slot=slot,
            sample_index=sample_index,
            measured=measured,
            force_fresh=force_fresh,
        )
        await run_walks([walk], session, keep_cut=False)
    return close_walk(walk)


def reread_cells(
    search_point: JobSearchPoint, dataset: list[Sample], session: Session, *, label: str
) -> list[Sample]:
    walk = open_walk(search_point, dataset, session, label=label, measured=None)
    return [sample for sample in dataset if sample.id in walk.ctx.rereads]


def open_walk(
    search_point: JobSearchPoint,
    dataset: list[Sample],
    session: Session,
    *,
    label: str,
    slot: ArmSlot | None = None,
    checks: Sequence[StopRule] = (),
    sample_index: SampleIndex | None = None,
    measured: MeasuredCandidate | None,
    force_fresh: bool = False,
    skip_at: int | None = None,
) -> Walk:
    """Writes nothing: an answer is filed as its cell is taken, so a walk never taken leaves no trace."""
    source = session.source
    assert source is not None, "populate_session_scoring arms the run source before any scoring"
    store = session.store
    backend_id = session.backend_id
    pipeline_schema = session.pipeline_schema

    entry = archive_entry(
        search_point, dataset_name=session.dataset_name, pipeline_schema=pipeline_schema
    )
    replaying = bool(backend_id) and not force_fresh
    node_configs = entry.node_configs if replaying else []
    # No node configs, no cell identity: every such searchpoint would share one claim per sample.
    feed = ReplayFeed(store.archive, node_configs) if node_configs else None
    cached_sample_results, deprecated_samples = _resolve_prior_cache(
        dataset, session, feed=feed, label=label
    )
    cell_keys = {} if feed is None else {s.id: feed.cell_key(s.key) for s in dataset}
    counted = session.state.priced_keys
    rereads = frozenset(s for s in cached_sample_results if cell_keys.get(s) in counted)

    ctx = QueryLoopState(
        search_point=search_point,
        session=session,
        cached_sample_results=cached_sample_results,
        slot=slot,
        role=str(label),
        sample_index=sample_index,
        deprecated_samples=deprecated_samples,
        recorder=_ArchiveRecorder(
            session=session, source=source, search_point=search_point, role=str(label), entry=entry
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
        counted=counted,
        rereads=rereads,
    )
    return Walk(
        dataset=dataset,
        ctx=ctx,
        checks=checks,
        context=measured_candidate_context(measured),
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
