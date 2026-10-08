"""Dataset scoring gateway — ``open_walk`` / ``close_walk``, and ``score_search_point`` for a
search point scored alone: cache resolution, archival, observability. The sole scoring ingress
(§0.5)."""

from __future__ import annotations

import asyncio
import functools
import logging
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.datasets.loaders import build_dataset_run_data
from promptpotter.application.scoring.metrics import ScoreSummary, compute_composite_fitness
from promptpotter.application.scoring.query_loop import QueryLoopState, Walk, WalkEnd, run_walks
from promptpotter.domain.measurement_provenance import REUSABLE_MIN_GRADE, grade_run, meets_grade
from promptpotter.domain.results import ArmOutcome, degradation_reading
from promptpotter.domain.results_health import is_deprecated
from promptpotter.domain.scoring import QueryMeasurement
from promptpotter.domain.spend import ROLE_SPEND_KIND
from promptpotter.domain.validators import BrokenSignal, StopRule, StopSignal
from promptpotter.infrastructure.llm.heartbeat import heartbeat
from promptpotter.infrastructure.llm.telemetry import _CURRENT_ROUND, filed_as
from promptpotter.infrastructure.store import archive_queries
from promptpotter.infrastructure.tracing.events import DatasetRun
from promptpotter.shared.errors import (
    DatasetIdentityError,
    error_category,
    graceful,
    is_error_result,
)
from promptpotter.shared.instrument import MeasuredCandidate, measured_candidate_context

if TYPE_CHECKING:
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.intelligence.indexes.sample import SampleIndex
    from promptpotter.application.scoring.query_loop import QueryLoopResult
    from promptpotter.domain.measurement_provenance import RunSource
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.search_point import JobSearchPoint
    from promptpotter.infrastructure.store.measurement_archive import (
        CellClaim,
        ReplayableRow,
        ReplayFeed,
    )

logger = logging.getLogger(__name__)

__all__ = [
    "SCORING_ERROR_ABORT",
    "ScoredWalk",
    "close_walk",
    "merge_with_unprocessed_priors",
    "open_walk",
    "reread_cells",
    "score_search_point",
]


@dataclass(frozen=True)
class ScoredWalk:
    """A decided walk, recorded. ``stopped`` is why it ended before its last cell, and ``None``
    once it took every cell, whatever decided it there. A partial walk means something different
    to each caller, so each one says what."""

    results: list[QueryMeasurement]
    scores: ScoreSummary
    signal: StopSignal | None
    stopped: WalkEnd | None
    # The archive run the rows were filed under — what a score report carries so each of its cells
    # is addressable as ``(run_id, sample_id)``.
    run_id: str


def merge_with_unprocessed_priors(
    results: list[QueryMeasurement],
    prior_tail: dict[int, QueryMeasurement],
) -> list[QueryMeasurement]:
    """Union results with the priors for samples the walk has not reached, or a partial run (cache
    hits + Ctrl+C) shrinks the archive's record. The run's derived fields read this merged view."""
    if not prior_tail:
        return results
    processed = {r["sample_id"] for r in results}
    return results + [p for sid, p in prior_tail.items() if sid not in processed]


# The gateway's own stop: the query loop gave up on this walk. One of the bench's two BROKEN rules.
SCORING_ERROR_ABORT = "scoring_error_abort"

# The ends whose partial scores as it stands, carrying no signal.
_UNSIGNALLED_ENDS = frozenset({WalkEnd.SKIP, WalkEnd.BUDGET})


def _build_scoring_error_signal(results: list[QueryMeasurement]) -> BrokenSignal:
    # Every error row here is a cell that was actually SENT: an abort pads no synthetic tail, and
    # the row that aborted the walk is the last of them.
    real_errors = [r for r in results if is_error_result(r)]
    warning_types = Counter(str(error_category(r) or "unknown") for r in real_errors)
    return BrokenSignal(
        check_name=SCORING_ERROR_ABORT,
        outcome=ArmOutcome.BROKEN,
        check_result=degradation_reading(
            source=SCORING_ERROR_ABORT,
            degraded_count=len(real_errors),
            total_scored=len(results),
            warning_types=warning_types,
            dominant_warning=str(real_errors[-1]["error"]),
        ),
    )


def _split_off_deprecated_samples(
    cached_sample_results: dict[int, QueryMeasurement],
) -> tuple[dict[int, QueryMeasurement], dict[int, QueryMeasurement]]:
    """Load-side cache split: (kept, deprecated rows that need fresh re-measure)."""

    deprecated = {sid: r for sid, r in cached_sample_results.items() if is_deprecated(r)}
    kept = {sid: r for sid, r in cached_sample_results.items() if sid not in deprecated}
    return kept, deprecated


def _replayable_on(
    dataset: list[Sample],
    reusable: dict[str, ReplayableRow],
    dataset_name: str | None,
) -> dict[int, QueryMeasurement]:
    """The banked rows this dataset replays, each moved to the position its sample holds HERE.

    Replay itself cannot go wrong on content — it matches ``sample_key``. What can is every reader
    that stays positional (the δ ruler, the sample index, hard samples), which keys a sample by
    ``(dataset_name, sample_id)``. So a row this dataset measured earlier must still find its own
    sample at its own slot; one that does not means the rows were re-cut under a name already used."""
    here = {s.id: s for s in dataset}
    for prior in reusable.values():
        if not dataset_name or prior.dataset_name != dataset_name:
            continue
        slot = prior.row["sample_id"]
        sample = here.get(slot)
        if sample is not None and sample.key != prior.row["sample_key"]:
            raise DatasetIdentityError(
                dataset_name=dataset_name,
                sample_id=slot,
                stored=(prior.row["query"], prior.row["ground_truth"]),
                current=(sample.query, sample.ground_truth or ""),
            )
    return {
        s.id: cast(QueryMeasurement, {**banked.row, "sample_id": s.id})
        for s in dataset
        if (banked := reusable.get(s.key)) is not None
    }


def _resolve_prior_cache(
    dataset: list[Sample],
    session: Session,
    *,
    feed: ReplayFeed | None,
    label: str,
) -> tuple[dict[int, QueryMeasurement], dict[int, QueryMeasurement]]:
    """Load-side cache resolution — reusable-archive lookup, deprecated-row split, preamble log.
    No ``feed`` ⇒ no reuse. Cached rows are already this dataset's cells alone (``_replayable_on``)."""
    cached_sample_results: dict[int, QueryMeasurement] = {}
    if feed is not None:
        cached_sample_results = _replayable_on(dataset, feed.advance(), session.dataset_name)

    cached_sample_results, deprecated_samples = _split_off_deprecated_samples(cached_sample_results)
    if deprecated_samples:
        logger.info(
            "Evicted %d deprecated prior result(s) (fatal warnings); will remeasure.",
            len(deprecated_samples),
        )

    # Preamble: when the JSP-keyed archive already covers some/all of this dataset's
    # samples, announce the split so the operator sees inline whether the upcoming
    # per-sample lines are cache replays vs fresh. Suppressed when no priors match.
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
    cached: dict[int, QueryMeasurement],
    shareable: Callable[[dict[str, Any]], bool],
) -> tuple[QueryMeasurement | None, CellClaim | None]:
    """The row a concurrent walk banked or is measuring for this cell, else this walk's hold on it:
    a cell another process reached after this walk opened is never bought, or drawn, twice. The
    wait sends nothing, so a pause breaks it at once, as it breaks a throttle wait."""
    waiting: asyncio.Task[None] | None = None
    try:
        while True:
            claim = feed.claim(sample.key, shareable=shareable)
            if claim is not None:
                try:
                    banked = {k: b for k, b in feed.advance().items() if not is_deprecated(b.row)}
                    cached.update(_replayable_on(dataset, banked, session.dataset_name))
                except BaseException:
                    claim.release()
                    raise
                if (row := cached.get(sample.id)) is None:
                    return None, claim
                claim.release()
                return row, None
            if (measured := feed.claimed_row(sample.key)) is not None:
                return cast(QueryMeasurement, {**measured, "sample_id": sample.id}), None
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
    """The stop signal a decided walk carries. A skip is the operator's early-abort of THIS
    search point: its partial is on disk and scores like an eliminator's cut, with no signal — as
    does the partial a kept budget cut left. Any other unsignalled stop is a scoring-error abort
    (consecutive 5xx, client 4xx, pipeline ERROR), made a candidate-scoped signal so the caller can
    attach a RuntimeFailure and go on — never killing the round."""
    ended_on = batch.ended_on
    if ended_on is None or batch.stop_signal is not None or ended_on in _UNSIGNALLED_ENDS:
        return batch.stop_signal
    return _build_scoring_error_signal(batch.results)


@dataclass
class _ArchiveRecorder:
    """One walk's run in the archive — the gateway's half of ``query_loop.py::RunRecorder``. The
    log opens at the first row saved, so a walk never taken leaves no trace, and is append-only."""

    session: Session
    source: RunSource
    search_point: JobSearchPoint
    run_id: str
    run_label: str
    content_hash: str
    force_fresh: bool
    # The cache priors this run archives without re-measuring, plus what a stop banked.
    prior_tail: dict[int, QueryMeasurement]
    _opened: bool = False
    # How many of the walk's ``results`` are already down, and whether the priors are.
    _appended: int = 0
    _priors_appended: bool = False

    def scores(self, results: list[QueryMeasurement]) -> ScoreSummary:
        """The walk's close and the loop's running number alike, so what a live surface shows
        converging is what the round banks — never a second fold."""
        return compute_composite_fitness(results, pipeline_schema=self.session.pipeline_schema)

    def persist(self, results: list[QueryMeasurement]) -> ScoreSummary:
        self._save(results)
        return self.scores(results)

    def bank(self, results: list[QueryMeasurement], rows: list[QueryMeasurement]) -> None:
        for row in rows:
            self.prior_tail[row["sample_id"]] = row
        self._save(results, rows)

    def close(self, results: list[QueryMeasurement], scores: ScoreSummary) -> None:
        self._save(results)
        if not self.session.backend_id:
            return
        archive_queries.compact_measurement_run(self.session.store, self.run_id)
        state = self.session.state
        if state.obs is None:
            return
        with graceful("DatasetRun emit failed"):
            state.obs.emit(
                DatasetRun(
                    campaign_id=state.tracing_campaign_id,
                    round_num=_CURRENT_ROUND.get(),
                    run_id=self.run_id,
                    content_hash=self.content_hash,
                    prompt_fields_id=self.search_point.sp_hash(self.session.pipeline_schema),
                    accuracy=scores["accuracy"],
                    total=scores["total"],
                )
            )

    def _save(
        self, results: list[QueryMeasurement], banked: Sequence[QueryMeasurement] = ()
    ) -> None:
        store = self.session.store
        if not self.session.backend_id:
            return
        if not self._opened:
            self._opened = True
            if self.force_fresh:
                # An append-only log does not overwrite: force_fresh means REPLACE these rows.
                archive_queries.reset_measurement_run(store, self.run_id)
            else:
                # Drop a previous walk's dead header rows before appending more (a no-op on a log
                # that closed cleanly — the end-of-walk compaction already tightened it).
                archive_queries.compact_measurement_run(store, self.run_id)
        merged = merge_with_unprocessed_priors(results, self.prior_tail)
        run_data = build_dataset_run_data(
            self.run_id,
            self.run_label,
            self.content_hash,
            self.search_point,
            merged,
            dataset_name=self.session.dataset_name,
            source=self.source,
            pipeline_schema=self.session.pipeline_schema,
            human_intervened=self.session.human_intervened,
        )
        # The cursor is over ``results``, not "the last row": a cache hit appends a MATERIALIZED
        # row without persisting. Priors go down once; a row walked later supersedes its own prior.
        new_rows: list[QueryMeasurement] = []
        if not self._priors_appended:
            new_rows.extend(merged[len(results) :])
            self._priors_appended = True
        else:
            new_rows.extend(banked)
        new_rows.extend(results[self._appended :])
        self._appended = len(results)
        archive_queries.record_measurement_run(
            store, self.run_id, run_data, cast("list[dict[str, Any]]", new_rows)
        )


async def score_search_point(
    search_point: JobSearchPoint,
    dataset: list[Sample],
    session: Session,
    *,
    label: str,
    on_sample_scored: Callable[[QueryMeasurement, int, int], None] | None,
    on_sample_starting: Callable[[str, int, int, int, int, int | None], None] | None,
    sample_index: SampleIndex | None = None,
    measured: MeasuredCandidate | None,
    force_fresh: bool = False,
) -> ScoredWalk:
    """One search point scored on ``dataset``, alone in its phase. ``measured`` and the two
    per-sample callbacks are required keywords with NO default — each decides what the numbers
    MEAN, and the signature is the only enforcement."""
    # Opened inside the filing too: the walk's cells run in a copy of the context taken here.
    with filed_as(ROLE_SPEND_KIND.get(label)):
        walk = open_walk(
            search_point,
            dataset,
            session,
            label=label,
            on_sample_scored=on_sample_scored,
            on_sample_starting=on_sample_starting,
            sample_index=sample_index,
            measured=measured,
            force_fresh=force_fresh,
        )
        await run_walks([walk], session, keep_cut=False)
    return close_walk(walk)


def reread_cells(
    search_point: JobSearchPoint, dataset: list[Sample], session: Session, *, label: str
) -> list[Sample]:
    """The cells of ``dataset`` the searchpoint replays at no price: all a tripped budget lets a
    walk take (`query_loop.py::Walk.rereads_ahead`)."""
    walk = open_walk(
        search_point,
        dataset,
        session,
        label=label,
        on_sample_scored=None,
        on_sample_starting=None,
        measured=None,
    )
    return [sample for sample in dataset if sample.id in walk.ctx.rereads]


def open_walk(
    search_point: JobSearchPoint,
    dataset: list[Sample],
    session: Session,
    *,
    label: str,
    on_sample_scored: Callable[[QueryMeasurement, int, int], None] | None,
    on_sample_starting: Callable[[str, int, int, int, int, int | None], None] | None,
    checks: Sequence[StopRule] = (),
    sample_index: SampleIndex | None = None,
    measured: MeasuredCandidate | None,
    force_fresh: bool = False,
) -> Walk:
    """A walk ready to be driven by :func:`run_walks` and closed by :func:`close_walk`. Writes
    nothing: the run's log opens at its first row, so a walk that is never taken leaves no trace."""
    source = session.source
    assert source is not None, "populate_session_scoring arms the run source before any scoring"
    store = session.store
    backend_id = session.backend_id
    pipeline_schema = session.pipeline_schema

    content_hash = search_point.content_hash(dataset)
    run_label = str(label)
    # The FULL hash, not a second cut of it. `label` names WHY the pass ran (`panel`, `origin`),
    # which every campaign repeats, so the hash is the only thing telling two runs apart. A
    # collision does not raise: `append_run` folds last-wins per `m:{sample_id}`, so both
    # searchpoints' measurements merge into one run file and the archive reports a configuration
    # that did not produce them.
    run_id = f"{run_label}_{content_hash}"

    replaying = bool(backend_id) and not force_fresh
    node_configs = pipeline_schema.node_configs(search_point.pipeline_params) if replaying else []
    # No node configs, no cell identity: every such searchpoint would share one claim per sample.
    feed = archive_queries.replay_feed(store, node_configs) if node_configs else None
    cached_sample_results, deprecated_samples = _resolve_prior_cache(
        dataset, session, feed=feed, label=label
    )
    cell_keys = {} if feed is None else {s.id: feed.cell_key(s.key) for s in dataset}
    counted = session.state.priced_keys
    rereads = frozenset(s for s in cached_sample_results if cell_keys.get(s) in counted)

    def _shareable(row: dict[str, Any]) -> bool:
        """What a waiting walk may replay — a row this walk's own run would serve back."""
        if is_error_result(row) or is_deprecated(row):
            return False
        graded = grade_run(
            source, [row], pipeline_schema, human_intervened=session.human_intervened
        )
        return meets_grade(graded.grade, REUSABLE_MIN_GRADE)

    ctx = QueryLoopState(
        search_point=search_point,
        session=session,
        run_id=run_id,
        cached_sample_results=cached_sample_results,
        on_sample_scored=on_sample_scored,
        sample_index=sample_index,
        deprecated_samples=deprecated_samples,
        recorder=_ArchiveRecorder(
            session=session,
            source=source,
            search_point=search_point,
            run_id=run_id,
            run_label=run_label,
            content_hash=content_hash,
            force_fresh=force_fresh,
            # As the priors stood when the walk opened: a replay re-grades its row in place, and
            # a concurrent walk's rows join the cache later.
            prior_tail={
                sid: cast(QueryMeasurement, dict(prior))
                for sid, prior in cached_sample_results.items()
            },
        ),
        claim_cell=None
        if feed is None
        else functools.partial(
            _claim_cell,
            feed=feed,
            session=session,
            dataset=dataset,
            cached=cached_sample_results,
            shareable=_shareable,
        ),
        cell_keys=cell_keys,
        counted=counted,
        rereads=rereads,
    )
    return Walk(
        dataset=dataset,
        ctx=ctx,
        checks=checks,
        on_sample_starting=on_sample_starting,
        # The one reader is the backend seam, which the walk's cells reach in copies of this.
        context=measured_candidate_context(measured),
    )


def close_walk(walk: Walk) -> ScoredWalk:
    """A decided walk, with its run recorded."""
    outcome = walk.outcome
    assert outcome is not None, "close_walk needs a decided walk"
    results = outcome.results
    scores = walk.ctx.recorder.scores(results)
    walk.ctx.recorder.close(results, scores)
    stopped = None if len(results) == walk.n else outcome.ended_on
    return ScoredWalk(results, scores, _walk_stop_signal(outcome), stopped, walk.ctx.run_id)
