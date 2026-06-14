"""Per-sample query loop — the inner loop ``score_search_point`` drives.

:func:`run_query_loop` walks a dataset sample by sample (online adaptive
queue mechanism or insertion order), reusing prior-cache results where available,
running the stale-data recovery protocol on degraded results, and
classifying client/pipeline errors into an abort reason. It returns a
:class:`QueryLoopResult` — the gateway turns that into the archived run.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.run_phase_control import declare_run_phase, pause_gate
from promptpotter.application.scoring.formula import rescore_results
from promptpotter.application.scoring.sample_measurement import (
    _error_result,
    measure_sample,
)
from promptpotter.application.scoring.sample_measurement import (
    execute_stale_data_protocol as _execute_stale_data_protocol,
)
from promptpotter.domain.escalation_signals import EscalationSignal
from promptpotter.domain.phases import RunPhase
from promptpotter.domain.scoring import QueryMeasurement, Scorer
from promptpotter.domain.validators import StopRule
from promptpotter.shared.errors import (
    ErrorCategory,
    error_category,
    is_error_result,
)
from promptpotter.shared.errors import (
    has_pipeline_warnings as _has_pipeline_warnings,
)

if TYPE_CHECKING:
    from promptpotter.application.bootstrap.session import Session
    from promptpotter.application.intelligence.indexes import AxisIndex
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.search_point import JobSearchPoint

logger = logging.getLogger(__name__)

__all__ = ["QueryLoopResult", "run_query_loop"]


MAX_CONSECUTIVE_ERRORS: int = 3
"""Abort the per-sample loop after this many consecutive client/pipeline
errors — a runaway backend shouldn't burn the round's compute budget."""


@dataclass
class QueryLoopResult:
    """_run_query_loop return: results + completed/stop_reason + first escalation_signal."""

    results: list[QueryMeasurement]
    completed: bool = True
    stop_reason: str | None = None  # "graceful" | "force" | "escalation" | abort reason
    escalation_signal: EscalationSignal | None = None


_BOLD_MARKER_RE = re.compile(r"\*\*[^*]+\*\*")


def _materialize_cached(
    item: QueryMeasurement,
    scorer: Scorer,
    scorer_id: str,
    scorer_formula: str | None,
) -> QueryMeasurement:
    """Mark prior as cached + rescored; warn on hit/no-hit drift unless explained by bold-strip."""
    archived_hit = item.get("hit") if "hit" in item else None
    r: dict[str, Any] = {**item, "cached": True}
    pd = r.get("pipeline_data")
    if isinstance(pd, dict):
        r["pipeline_data"] = {**pd, "total_time": 0.0}
    rescore_results([r], scorer, scorer_id, scorer_formula)
    rescored_hit = r.get("hit", False)
    if archived_hit is not None and bool(archived_hit) != bool(rescored_hit):
        predicted = r.get("predicted") or ""
        if not _BOLD_MARKER_RE.search(predicted):
            logger.warning(
                "Cache rescore drift on %r: archived hit=%s → rescored hit=%s "
                "(scorer=%s). Policy divergence — not explained by bold-wrapper strip.",
                (r.get("query") or "")[:60],
                archived_hit,
                rescored_hit,
                scorer_id,
            )
    return cast(QueryMeasurement, r)


@dataclass
class QueryLoopState:
    """Read-only context threaded through per-sample processing."""

    search_point: JobSearchPoint
    session: Session
    cached_sample_results: dict[str, QueryMeasurement]
    on_sample_scored: Callable[[QueryMeasurement, int, int], None] | None
    axes: AxisIndex | None
    scorer: Scorer  # narrowed from session.scoring.scorer (asserted non-None on construction)
    scorer_id: str
    scorer_formula: str | None
    # Queries whose prior-cache entry was a deprecated sample — value is the
    # cached entry itself so display can show the original DEPR row before
    # the retry row is rendered.
    deprecated_samples: dict[str, QueryMeasurement]
    # Persist the results-so-far after each fresh measurement. Cache hits do
    # not call this — their on-disk row is unchanged.
    persist_fresh: Callable[[list[QueryMeasurement]], None]
    # Async hook fired after each sample lands, before degradation checks
    # read prior coverage. The candidate loop uses it to catch PoBB priors
    # up on the just-measured sample so paired comparison sees full coverage
    # without an upfront full-dataset wall.
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


async def _maybe_recover_degraded(
    result: QueryMeasurement,
    sample: Sample,
    ctx: QueryLoopState,
) -> QueryMeasurement:
    """Run the stale-data protocol if the result is degraded."""
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
        return f"skipped_after_{cat or 'pipeline'}_error"
    state.consecutive_errors += 1
    if state.consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
        return "skipped_after_consecutive_errors"
    return ""


async def _process_cache_hit(
    sample: Sample,
    idx: int,
    dataset_len: int,
    state: _LoopState,
    ctx: QueryLoopState,
    check_escalation: Callable[[], EscalationSignal | None],
) -> _SampleOutcome:
    """Materialize a prior-cache result, append, and check escalation."""
    cached_r = _materialize_cached(
        ctx.cached_sample_results[sample.query],
        ctx.scorer,
        ctx.scorer_id,
        ctx.scorer_formula,
    )
    cached_r = await _maybe_recover_degraded(cached_r, sample, ctx)
    # Overlay current-run sample_id — archived traces may predate the field.
    cached_r["sample_id"] = sample.id
    state.results.append(cached_r)
    if ctx.on_sample_scored is not None:
        ctx.on_sample_scored(cached_r, idx, dataset_len)
    if ctx.on_sample_pre_check is not None:
        await ctx.on_sample_pre_check(sample)
    # Elimination must see cached rows too — otherwise a candidate
    # whose priors already dominate it runs one extra real query.
    esc = check_escalation()
    return _SampleOutcome(escalation=esc) if esc else _SampleOutcome()


async def _process_fresh_sample(
    sample: Sample,
    idx: int,
    dataset_len: int,
    state: _LoopState,
    ctx: QueryLoopState,
    check_escalation: Callable[[], EscalationSignal | None],
) -> _SampleOutcome:
    """Backend-measure one sample; render rescored DEPR row before retry; classify errors."""
    if (cached_deprecated := ctx.deprecated_samples.get(sample.query)) is not None:
        display_cached = _materialize_cached(
            cached_deprecated, ctx.scorer, ctx.scorer_id, ctx.scorer_formula
        )
        display_cached["sample_id"] = sample.id
        if ctx.on_sample_scored is not None:
            ctx.on_sample_scored(display_cached, idx, dataset_len)
        from promptpotter.infrastructure.llm import (
            DEPR_RETRY_COOLDOWN_SEC,
            wait_with_countdown,
        )

        await wait_with_countdown(DEPR_RETRY_COOLDOWN_SEC, "deprecated retry")

    result = await measure_sample(
        sample,
        ctx.session,
        pipeline_params=ctx.search_point.pipeline_params,
    )
    result = await _maybe_recover_degraded(result, sample, ctx)
    if sample.query in ctx.deprecated_samples:
        cast(dict[str, Any], result)["retry_of_deprecated_cache"] = True
    state.results.append(result)
    ctx.persist_fresh(state.results)

    if is_error_result(result):
        abort_reason = _classify_abort(result, state)
        if abort_reason:
            return _SampleOutcome(abort_reason=abort_reason)
    else:
        state.consecutive_errors = 0

    if ctx.on_sample_scored is not None:
        ctx.on_sample_scored(result, idx, dataset_len)

    if ctx.on_sample_pre_check is not None:
        await ctx.on_sample_pre_check(sample)

    esc = check_escalation()
    return _SampleOutcome(escalation=esc) if esc else _SampleOutcome()


async def run_query_loop(
    search_point: JobSearchPoint,
    dataset: list[Sample],
    session: Session,
    *,
    cached_sample_results: dict[str, QueryMeasurement],
    deprecated_samples: dict[str, QueryMeasurement],
    on_sample_scored: Callable[[QueryMeasurement, int, int], None] | None,
    on_sample_starting: Callable[[str, int, int, int], None] | None,
    # ``on_sample_scored`` is required (no default) so every call site
    # declares its per-sample visibility intent. See ``score_search_point``
    # docstring for the bug class this guards.
    degradation_checks: list[StopRule] | None,
    candidate_idx: int,
    n_total_candidates: int,
    axes: AxisIndex | None,
    persist_fresh: Callable[[list[QueryMeasurement]], None],
    next_sample: Callable[[dict[int, bool]], int | None] | None = None,
    on_sample_pre_check: Callable[[Sample], Awaitable[None]] | None = None,
) -> QueryLoopResult:
    """Score dataset samples, reusing prior results where available.

    ``next_sample`` is the online adaptive queue mechanism (1PL Rasch CAT).
    When supplied, the loop calls it before each iteration with the
    candidate's accumulated ``scored_outcomes`` (sample_id → hit) and
    receives the next sample id to measure. Returning ``None`` from
    ``next_sample`` ends the loop. Without ``next_sample``, the loop
    falls back to dataset insertion order — the path used by paths
    that don't need adaptive selection (PoBB backfill, unit tests).
    """
    assert session.scoring.scorer is not None, "session.scoring.scorer required for scoring"
    state = _LoopState(results=[])
    ctx = QueryLoopState(
        search_point=search_point,
        session=session,
        cached_sample_results=cached_sample_results,
        on_sample_scored=on_sample_scored,
        axes=axes,
        scorer=session.scoring.scorer,
        scorer_id=session.scoring.scorer_id,
        scorer_formula=session.scoring.scorer_formula,
        deprecated_samples=deprecated_samples,
        persist_fresh=persist_fresh,
        on_sample_pre_check=on_sample_pre_check,
    )

    def _check_escalation() -> EscalationSignal | None:
        for c in degradation_checks or []:
            signal = c.check(state.results, candidate_idx, n_total_candidates)
            if signal is not None:
                return signal
        return None

    # Per-step adaptive picking when ``next_sample`` is supplied;
    # otherwise iterate the dataset as-given. ``scored_outcomes`` is the
    # full (sample_id → hit) map the queue mechanism folds its posterior over.
    by_id: dict[int, Sample] = {s.id: s for s in dataset}
    fallback_order: list[int] = [s.id for s in dataset]
    scored_outcomes: dict[int, bool] = {}

    def _pick_next() -> int | None:
        if next_sample is not None:
            return next_sample(scored_outcomes)
        for sid in fallback_order:
            if sid not in scored_outcomes:
                return sid
        return None

    def _remaining_ids() -> list[int]:
        return [sid for sid in fallback_order if sid not in scored_outcomes]

    try:
        i = 0
        while True:
            sid = _pick_next()
            if sid is None:
                break
            sample = by_id[sid]
            # Mid-searchpoint pause/stop checkpoint. Sits between samples — every
            # already-scored result is on disk (persist_fresh ran per fresh
            # sample), so pausing here finishes the current sample then blocks,
            # and a stop aborts with the accumulated datapoints saved. A pause
            # resumes into the remaining samples; a stop (incl. one requested
            # during the pause) falls through to the graceful return below.
            if await pause_gate(session) or (session.stop_check and session.stop_check()):
                logger.debug("Graceful stop after query %d/%d.", len(state.results), len(dataset))
                declare_run_phase(session, RunPhase.STOPPING)
                return QueryLoopResult(state.results, completed=False, stop_reason="graceful")

            if on_sample_starting is not None:
                on_sample_starting(sample.query, i, len(dataset), sample.id)

            handler = (
                _process_cache_hit
                if sample.query in cached_sample_results
                else _process_fresh_sample
            )
            outcome = await handler(sample, i, len(dataset), state, ctx, _check_escalation)

            if outcome.escalation:
                return QueryLoopResult(
                    state.results,
                    completed=False,
                    stop_reason="escalation",
                    escalation_signal=outcome.escalation,
                )
            if outcome.abort_reason:
                remaining = _remaining_ids()
                # The current sample also failed — mark it errored. The
                # _process_* handler already appended the failed result,
                # so synthesize errors only for the untouched tail.
                remaining = [r for r in remaining if r != sid]
                logger.warning(
                    "Aborting scoring: %s on query %d. Marking remaining %d as errors.",
                    outcome.abort_reason,
                    i + 1,
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

            # Record the outcome (hit / non-hit) so the queue mechanism's
            # posterior fold sees it on the next iteration. Errored
            # results carry no ``hit`` field — treat them as miss for
            # the posterior since the queue mechanism is about *capability*,
            # not infrastructure; PoBB and DegradationCheck already
            # gate elimination on the error class itself.
            latest_hit: bool = bool(state.results[-1].get("hit")) if state.results else False
            scored_outcomes[sid] = latest_hit
            i += 1
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.warning(
            "Query loop force-interrupted at query %d/%d.", len(state.results), len(dataset)
        )
        return QueryLoopResult(state.results, completed=False, stop_reason="force")

    return QueryLoopResult(state.results)
