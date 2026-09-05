"""The per-sample inner loop ``score_search_point`` drives — prior-cache reuse, stale-data recovery, and error
classification into an abort reason. The gateway turns its result into the archived run."""

from __future__ import annotations

import asyncio
import logging
import re
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.run_phase_control import declare_run_phase, pause_requested
from promptpotter.application.scoring.formula import rescore_results
from promptpotter.application.scoring.sample_measurement import (
    _error_result,
    measure_sample,
)
from promptpotter.application.scoring.sample_measurement import (
    execute_stale_data_protocol as _execute_stale_data_protocol,
)
from promptpotter.domain.escalation_signals import EscalationSignal
from promptpotter.domain.phases import RunPhase, StopLoop
from promptpotter.domain.scoring import CellScorer, QueryMeasurement, is_hit
from promptpotter.domain.validators import StopRule
from promptpotter.infrastructure.runtime_flags import effective_lookahead
from promptpotter.shared.errors import (
    ErrorCategory,
    error_category,
    is_error_result,
)
from promptpotter.shared.errors import (
    has_pipeline_warnings as _has_pipeline_warnings,
)

if TYPE_CHECKING:
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.intelligence.indexes.axis import AxisIndex
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.search_point import JobSearchPoint

logger = logging.getLogger(__name__)

__all__ = ["QueryLoopResult", "run_query_loop"]


MAX_CONSECUTIVE_ERRORS: int = 3
"""Abort the per-sample loop after this many consecutive client/pipeline
errors — a runaway backend shouldn't burn the round's compute budget."""

# How many samples a walk may hold in flight is the BACKEND's to declare
# (`Connector.max_cells_in_flight`), not a constant here. It was a fixed 2 while the depth was
# pinned off `execution` — a transport fact answering a cost question, wrongly.


@dataclass
class QueryLoopResult:
    results: list[QueryMeasurement]
    completed: bool = True
    # "graceful" | "force" (both unwind the cycle) | "skip" (operator early-abort:
    # accept partial, cycle continues) | "escalation" | abort reason
    stop_reason: str | None = None
    escalation_signal: EscalationSignal | None = None


_BOLD_MARKER_RE = re.compile(r"\*\*[^*]+\*\*")


def _with_running(result: QueryMeasurement, running: dict[str, Any]) -> QueryMeasurement:
    """A shallow copy carrying the candidate's running fitness — a transient projection hint for the live dashboard. The
    persisted results keep the clean result, not this copy."""
    out = dict(result)
    out["_running"] = running
    return cast(QueryMeasurement, out)


def _materialize_cached(item: QueryMeasurement, scorer: CellScorer) -> QueryMeasurement:
    """Mark prior as cached + rescored; warn on hit/no-hit drift unless explained by bold-strip."""
    # Deliberately BINARY, on the archived vs rescored verdict rather than the graded fitness:
    # a float comparison fires per sample on every formula tweak, and this warning is calibrated
    # for one known-benign cause. ``fitness is None`` is never-scored, distinct from a 0.0.
    archived_fitness = item.get("fitness")
    r: dict[str, Any] = {**item, "cached": True}
    pd = r.get("pipeline_data")
    if isinstance(pd, dict):
        r["pipeline_data"] = {**pd, "total_time": 0.0}
    rescore_results([r], scorer)
    archived_hit = is_hit(archived_fitness)
    rescored_hit = is_hit(r.get("fitness"))
    if archived_fitness is not None and archived_hit != rescored_hit:
        predicted = r.get("predicted") or ""
        if not _BOLD_MARKER_RE.search(predicted):
            logger.warning(
                "Cache rescore drift on %r: archived hit=%s → rescored hit=%s. "
                "Policy divergence — not explained by bold-wrapper strip.",
                (r.get("query") or "")[:60],
                archived_hit,
                rescored_hit,
            )
    return cast(QueryMeasurement, r)


def _emit_cached_step_tokens(row: QueryMeasurement) -> None:
    """Meter a measurement-cache hit off the tokens the archived row already carries. Replaying them costs nothing, but the
    search still made the call, so the ledger has to say so."""
    from promptpotter.application.scoring.sample_measurement import (
        StepTokenUsage,
        emit_step_token_usage,
    )

    pd = row.get("pipeline_data")
    if not isinstance(pd, dict):
        return
    step_tokens = pd.get("step_tokens")
    if not isinstance(step_tokens, dict) or not step_tokens:
        return
    step_timings = pd.get("step_timings")
    emit_step_token_usage(
        cast("Mapping[str, StepTokenUsage]", step_tokens),
        step_timings if isinstance(step_timings, dict) else {},
        cached=True,
    )


@dataclass
class QueryLoopState:
    """Read-only context threaded through per-sample processing."""

    search_point: JobSearchPoint
    session: Session
    cached_sample_results: dict[int, QueryMeasurement]
    on_sample_scored: Callable[[QueryMeasurement, int, int], None] | None
    axes: AxisIndex | None
    scorer: CellScorer  # narrowed from session.scoring.scorer (asserted non-None on construction)
    # The cached entry itself, so display can show the original DEPR row before the retry row.
    deprecated_samples: dict[int, QueryMeasurement]
    # Persists results-so-far after each fresh measurement and returns the running fitness over
    # them. It fires BEFORE the error-abort return and before ``on_sample_pre_check``, which can
    # re-enter the gateway and be Ctrl+C'd, so it must stay its own call site. The promise: a
    # sample is on disk iff it was ABSORBED — a discarded look-ahead acquisition is paid for and
    # deliberately not here.
    persist_fresh: Callable[[list[QueryMeasurement]], dict[str, Any]]
    # Ridden out on the sample snapshot so the live surfaces show a candidate fitness that moves
    # in real time instead of sitting at 0. A fresh sample reads it off ``persist_fresh``'s
    # return rather than folding twice.
    running_scores: Callable[[list[QueryMeasurement]], dict[str, Any]]
    # Fired after each sample lands, before degradation checks read prior coverage: the
    # candidate loop catches PoBB priors up on the just-measured sample, so paired comparison
    # sees full coverage without an upfront full-dataset wall.
    on_sample_pre_check: Callable[[Sample], Awaitable[None]] | None


@dataclass
class _LoopState:
    """Mutable state accumulated across the dataset loop."""

    results: list[QueryMeasurement]
    consecutive_errors: int = 0


@dataclass
class _SampleOutcome:
    """Outcome of processing one sample. Both fields ``None`` ⇒ continue normally."""

    abort_reason: str | None = None
    escalation: EscalationSignal | None = None


def _armed_cells(session: Session) -> int:
    """What the operator ASKED for, clamped to what the backend declares it can hold — the request
    alone would let the browser outrun the box, the ceiling alone would widen every walk unbidden.

    **Sample look-ahead is LIVE, and every part of it looks removable.** It defaults off and no
    committed campaign enables it, so a reader concludes the branch never fires; it fires on every
    dataset the moment the operator presses the control — ``promptpotter-self`` included, where one
    press releases a GROUP of inner campaigns. Four pieces move together or not at all: the
    ``.runtime/sample_lookahead.json`` write/poll/consume triple, the acquire/absorb split below,
    ``dashboard.json::sample_lookahead`` + ``sample_lookahead_discards`` with the two connector
    declarations beside them, and the ``campaign.lookahead`` cap. **Never "recover" the discarded
    acquisition** — recording it makes the run's rows depend on in-flight depth, forcing a
    ``human_intervened`` stamp and devaluing the campaign; that discard is the design. Why it is
    browser-only with no CLI verb: ``docs/operations/access-model.md`` § host-admin ↔ user."""
    check = session.sample_lookahead_check
    requested = check() if check is not None else 1
    return effective_lookahead(requested, session.backend_client.max_cells_in_flight)


async def _maybe_recover_degraded(
    result: QueryMeasurement,
    sample: Sample,
    ctx: QueryLoopState,
) -> QueryMeasurement:
    from promptpotter.application.scoring.sample_measurement import STALE_DATA_LOAD_PROTOCOL

    if not _has_pipeline_warnings(result):
        return result
    recovered, _step = await _execute_stale_data_protocol(
        list(STALE_DATA_LOAD_PROTOCOL),
        sample,
        cast(dict[str, Any], result),
        ctx.session,
        pipeline_params=ctx.search_point.pipeline_params,
        axes=ctx.axes,
    )
    return cast(QueryMeasurement, recovered)


def _classify_abort(
    result: QueryMeasurement,
    state: _LoopState,
) -> str:
    """Abort reason on error or "" to continue. Mutates state.consecutive_errors."""
    cat = error_category(result)
    if cat in {ErrorCategory.CLIENT, ErrorCategory.PIPELINE}:
        return f"skipped_after_{cat}_error"
    state.consecutive_errors += 1
    if state.consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
        return "skipped_after_consecutive_errors"
    return ""


@dataclass
class _Acquired:
    """Carries no side effect a reader could order against: every append, persist, callback and
    ledger write belongs to ``_absorb``."""

    sample: Sample
    idx: int
    result: QueryMeasurement
    fresh: bool  # False ⇒ replayed from the prior cache (no ``persist_fresh``)
    deprecated_display: QueryMeasurement | None = None


@dataclass
class _Slot:
    sample_id: int  # kept beside the task: a cancelled one is never consulted for it
    task: asyncio.Task[_Acquired]


async def _acquire(sample: Sample, idx: int, ctx: QueryLoopState) -> _Acquired:
    cached = ctx.cached_sample_results.get(sample.id)
    if cached is not None:
        cached_r = _materialize_cached(cached, ctx.scorer)
        # Can re-measure for real, so a hit gets a slot like anything else.
        cached_r = await _maybe_recover_degraded(cached_r, sample, ctx)
        # Overlay current-run sample_id — archived traces may predate the field.
        cached_r["sample_id"] = sample.id
        return _Acquired(sample=sample, idx=idx, result=cached_r, fresh=False)

    deprecated_display: QueryMeasurement | None = None
    if (cached_deprecated := ctx.deprecated_samples.get(sample.id)) is not None:
        # Rescored here, rendered in `_absorb`: a display call from a slot prints out of walk order.
        deprecated_display = _materialize_cached(cached_deprecated, ctx.scorer)

    result = await measure_sample(
        sample,
        ctx.session,
        pipeline_params=ctx.search_point.pipeline_params,
    )
    result = await _maybe_recover_degraded(result, sample, ctx)
    if sample.id in ctx.deprecated_samples:
        cast(dict[str, Any], result)["retry_of_deprecated_cache"] = True
    return _Acquired(
        sample=sample,
        idx=idx,
        result=result,
        fresh=True,
        deprecated_display=deprecated_display,
    )


async def _absorb(
    acq: _Acquired,
    dataset_len: int,
    state: _LoopState,
    ctx: QueryLoopState,
    check_escalation: Callable[[], EscalationSignal | None],
) -> _SampleOutcome:
    """The only writer of ``state``, persister of a row, and decider. Runs in walk order however
    many were acquired in parallel — what keeps the record independent of the look-ahead depth."""
    if not acq.fresh:
        # Only if it STAYED a replay: the stale-data protocol may have re-measured for real,
        # and that path already emitted its own fresh records.
        if acq.result.get("cached"):
            _emit_cached_step_tokens(acq.result)
    elif acq.deprecated_display is not None and ctx.on_sample_scored is not None:
        ctx.on_sample_scored(acq.deprecated_display, acq.idx, dataset_len)

    state.results.append(acq.result)
    running = ctx.persist_fresh(state.results) if acq.fresh else ctx.running_scores(state.results)

    if acq.fresh:
        if is_error_result(acq.result):
            if abort_reason := _classify_abort(acq.result, state):
                return _SampleOutcome(abort_reason=abort_reason)
        else:
            state.consecutive_errors = 0

    if ctx.on_sample_scored is not None:
        ctx.on_sample_scored(_with_running(acq.result, running), acq.idx, dataset_len)

    if ctx.on_sample_pre_check is not None:
        await ctx.on_sample_pre_check(acq.sample)

    # Elimination must see cached rows too — otherwise a candidate
    # whose priors already dominate it runs one extra real query.
    esc = check_escalation()
    return _SampleOutcome(escalation=esc) if esc else _SampleOutcome()


async def run_query_loop(
    search_point: JobSearchPoint,
    dataset: list[Sample],
    session: Session,
    *,
    cached_sample_results: dict[int, QueryMeasurement],
    deprecated_samples: dict[int, QueryMeasurement],
    on_sample_scored: Callable[[QueryMeasurement, int, int], None] | None,
    on_sample_starting: Callable[[str, int, int, int, int], None] | None,
    # ``on_sample_scored`` takes no default, so every call site declares its per-sample
    # visibility intent — see ``score_search_point`` for the bug class that guards.
    degradation_checks: list[StopRule] | None,
    candidate_idx: int,
    n_total_candidates: int,
    axes: AxisIndex | None,
    persist_fresh: Callable[[list[QueryMeasurement]], dict[str, Any]],
    running_scores: Callable[[list[QueryMeasurement]], dict[str, Any]],
    on_sample_pre_check: Callable[[Sample], Awaitable[None]] | None = None,
) -> QueryLoopResult:
    """Score samples in the order GIVEN — ``score_population`` reorders the round's dataset once, so walking it as-given IS
    the round order. Elimination checks fire after every sample."""
    assert session.scoring.scorer is not None, "session.scoring.scorer required for scoring"
    state = _LoopState(results=[])
    ctx = QueryLoopState(
        search_point=search_point,
        session=session,
        cached_sample_results=cached_sample_results,
        on_sample_scored=on_sample_scored,
        axes=axes,
        scorer=session.scoring.scorer,
        deprecated_samples=deprecated_samples,
        persist_fresh=persist_fresh,
        running_scores=running_scores,
        on_sample_pre_check=on_sample_pre_check,
    )

    def _check_escalation() -> EscalationSignal | None:
        for c in degradation_checks or []:
            signal = c.check(state.results, candidate_idx, n_total_candidates)
            if signal is not None:
                return signal
        return None

    by_id: dict[int, Sample] = {s.id: s for s in dataset}
    walk_order: list[int] = [s.id for s in dataset]
    n = len(dataset)
    # ``submitted`` is how far LAUNCHING reached, ``scored_ids`` how far ABSORPTION did;
    # look-ahead is the gap between them.
    scored_ids: set[int] = set()
    submitted = 0
    window: deque[_Slot] = deque()

    def _remaining_ids() -> list[int]:
        return [sid for sid in walk_order if sid not in scored_ids]

    def _launch(depth: int) -> None:
        nonlocal submitted
        sample = by_id[walk_order[submitted]]
        if on_sample_starting is not None:
            # The depth the walk is RUNNING at, not how many happen to be in flight this
            # instant: a window is empty at the start of every candidate, so reporting the
            # latter unlights the operator's armed indicator once per candidate. Passed in, so a
            # press consumed mid-fill cannot make its own group's tail report depth 1.
            on_sample_starting(sample.query, submitted, n, sample.id, depth)
        window.append(
            _Slot(
                sample_id=sample.id,
                task=asyncio.create_task(
                    _acquire(sample, submitted, ctx), name=f"scoring:{sample.id}"
                ),
            )
        )
        submitted += 1

    def _operator_stop() -> QueryLoopResult | None:
        """The three run-control checkpoints, polled at the SAMPLE boundary rather than at a
        launch: under group arming the loop blocks in `await slot.task` for a whole group, so a
        check that only runs while refilling is a check a pause waits out."""
        # Checked FIRST, so a one-shot skip never falls through to the sticky pause path. It cuts
        # this searchpoint's remaining samples and consumes the flag, and the gateway takes the
        # partial — no PAUSED phase, no cycle exit.
        if session.skip_check and session.skip_check():
            if session.skip_consume:
                session.skip_consume()
            logger.info(
                "Operator skip after query %d/%d; accepting partial searchpoint.",
                len(state.results),
                n,
            )
            return QueryLoopResult(state.results, completed=False, stop_reason="skip")
        # Between samples, where every ABSORBED result is already on disk, so this exits cleanly
        # and `resume` continues into the remaining samples.
        if pause_requested(session):
            logger.debug("Pause after query %d/%d.", len(state.results), n)
            declare_run_phase(session, RunPhase.PAUSED)
            return QueryLoopResult(state.results, completed=False, stop_reason="graceful")
        # Same cadence, because the round-boundary gate cannot fire until the round closes — and
        # for an L4 outer round every sample is an entire inner CAMPAIGN. `StopLoop` is the
        # existing mid-round stop channel and carries the gate's own reason; unwinding via
        # `graceful` would relabel a budget halt as an operator PAUSE.
        if session.budget_tripped is not None and (stop := session.budget_tripped()) is not None:
            logger.warning(
                "Budget ceiling reached after query %d/%d (%s); halting mid-round.",
                len(state.results),
                n,
                stop.value,
            )
            raise StopLoop(stop)
        return None

    try:
        while True:
            if (stopped := _operator_stop()) is not None:
                return stopped
            # Re-read every iteration, so a press landing mid-walk TOPS THE WINDOW UP rather than
            # waiting for it to drain — a running sample is JOINED, not waited out, and
            # `sample_started` still records each launch depth. Nothing is spent here: the round
            # that scored under the depth spends it (`l1/score/winner.py`).
            depth = _armed_cells(session)
            while submitted < n and len(window) < depth:
                _launch(depth)

            if not window:
                break

            # Popping from the left IS the in-order-absorption invariant.
            slot = window.popleft()
            acquired = await slot.task
            scored_ids.add(slot.sample_id)
            outcome = await _absorb(acquired, n, state, ctx, _check_escalation)

            if outcome.escalation:
                return QueryLoopResult(
                    state.results,
                    completed=False,
                    stop_reason="escalation",
                    escalation_signal=outcome.escalation,
                )
            if outcome.abort_reason:
                # The failed sample is already in `scored_ids`, so the complement is the
                # untouched tail — a discarded look-ahead acquisition included, still owed a row.
                remaining = _remaining_ids()
                logger.warning(
                    "Aborting scoring: %s on query %d. Marking remaining %d as errors.",
                    outcome.abort_reason,
                    len(state.results),
                    len(remaining),
                )
                state.results.extend(
                    _error_result(
                        by_id[rsid], outcome.abort_reason, category=ErrorCategory.PIPELINE
                    )
                    for rsid in remaining
                )
                return QueryLoopResult(
                    state.results, completed=False, stop_reason=outcome.abort_reason
                )
    except KeyboardInterrupt:
        logger.warning("Query loop force-interrupted at query %d/%d.", len(state.results), n)
        return QueryLoopResult(state.results, completed=False, stop_reason="force")
    finally:
        # A slot still in flight is DISCARDED, never appended or persisted: recording it makes
        # the run's rows depend on the in-flight depth, which forces a `human_intervened` stamp.
        # Not awaited either — an `await` here can swallow a CancelledError aimed at this
        # coroutine, which the note below forbids.
        if window:
            logger.info(
                "Discarding %d look-ahead acquisition(s) after query %d/%d.",
                len(window),
                len(state.results),
                n,
            )
            for slot in window:
                slot.task.cancel()
            window.clear()
    # ``asyncio.CancelledError`` is deliberately NOT caught beside the KeyboardInterrupt above.
    # The two arrive from opposite directions — one is the operator asking to stop, the other
    # is this program's own machinery stopping us. Answering a cancellation with a normal
    # return tells the canceller it succeeded while the work runs on, which is how the L4
    # sample wall clock became unenforceable: ``asyncio.timeout`` raises TimeoutError only if a
    # CancelledError comes back. Measured samples are on disk, so propagating loses nothing.

    return QueryLoopResult(state.results)
