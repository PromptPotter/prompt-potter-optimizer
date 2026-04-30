"""Dataset scoring — query loop orchestration, archival, and observability."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.datasets.datasets import build_dataset_run_data
from promptpotter.application.scoring.formula import rescore_results
from promptpotter.application.scoring.metrics import compute_composite_score
from promptpotter.application.scoring.sample_measurement import (
    _error_result,
    measure_sample,
)
from promptpotter.application.scoring.sample_measurement import (
    execute_stale_data_protocol as _execute_stale_data_protocol,
)
from promptpotter.domain.analysis import EscalationSignal, EscalationTarget
from promptpotter.domain.scoring import QueryResult, Scorer
from promptpotter.shared.errors import (
    ErrorCategory,
    error_category,
    is_error_result,
)
from promptpotter.shared.errors import (
    is_degraded as _is_degraded,
)

if TYPE_CHECKING:
    from promptpotter.application.bootstrap import Session
    from promptpotter.application.intelligence.indexes import AxisIndex
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.search_point import JobSearchPoint

logger = logging.getLogger(__name__)

__all__ = ["QueryLoopResult", "score_search_point"]


@dataclass
class QueryLoopResult:
    """Return value from ``_run_query_loop()``.

    ``completed`` + ``stop_reason`` signal graceful/force stops; ``escalation_signal``
    carries the first triggered degradation check.
    """

    results: list[QueryResult]
    completed: bool = True
    stop_reason: str | None = None  # "graceful" | "force" | "escalation" | abort reason
    escalation_signal: EscalationSignal | None = None


_BOLD_MARKER_RE = re.compile(r"\*\*[^*]+\*\*")


def _materialize_cached(
    item: QueryResult,
    scorer: Scorer,
    scorer_id: str,
    scorer_formula: str | None,
) -> QueryResult:
    """Stamp a prior-cache item as cached with zeroed timing, rescored.

    ``hit``/``score`` persisted on the prior run are a stale view — the
    active scorer owns those fields on load, and its result lands in
    ``r["scored"][scorer_id]`` so the trace accumulates a multi-scorer
    audit map (see ``shared/scoring.py::rescore_results``).

    Drift detection: when the cached item was previously scored (carried a
    ``hit`` field on disk) and rescoring flips the outcome, emit a warning
    so policy divergences don't silently accumulate. The known-benign case
    is bold-wrapper stripping (``**answer**`` vs ``answer``) — when the
    predicted string contains ``**…**`` markers we silently overwrite.
    """
    archived_hit = item.get("hit") if "hit" in item else None
    r: dict = {**item, "cached": True}
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
    return cast(QueryResult, r)


def _build_scoring_error_signal(
    *,
    results: list[QueryResult],
    stop_reason: str,
    candidate_idx: int,
    n_total_candidates: int,
) -> EscalationSignal:
    """Build ELIMINATE_CANDIDATE signal carrying error histogram for RuntimeFailure mint."""
    # Skip rows whose error is the abort-reason padding — those are synthetic
    # markers inserted after the cascade to bring results up to dataset length,
    # not the real backend failure that triggered it.
    real_errors = [
        r for r in results if is_error_result(r) and str(r.get("error") or "") != stop_reason
    ]
    warning_types: dict[str, int] = {}
    for r in real_errors:
        key = str(error_category(r.get("error")) or "unknown")
        warning_types[key] = warning_types.get(key, 0) + 1
    last_error = ""
    for r in reversed(real_errors):
        err = r.get("error")
        if err:
            last_error = str(err)
            break
    dominant = last_error or stop_reason or "scoring_error"
    return EscalationSignal(
        check_name="scoring_error_abort",
        target=EscalationTarget.ELIMINATE_CANDIDATE,
        check_result={
            "stop_reason": stop_reason,
            "dominant_warning": dominant,
            "warning_types": warning_types,
            "degraded_count": len(real_errors),
            "total_scored": len(results),
            "last_error": last_error,
        },
        candidate_idx=candidate_idx,
        candidates_scored=candidate_idx + 1,
        candidates_skipped=n_total_candidates - candidate_idx - 1,
    )


def _filter_deprecated_priors(
    prior_results: dict[str, QueryResult],
) -> tuple[dict[str, QueryResult], dict[str, QueryResult]]:
    """Split prior-results cache into ``(kept, evicted)``; evicted entries force a fresh backend call so the optimizer doesn't replay known-bad measurements (load-side filter only — disk archive untouched)."""
    from promptpotter.application.optimization.elimination import is_deprecated

    evicted = {q: r for q, r in prior_results.items() if is_deprecated(r)}
    kept = {q: r for q, r in prior_results.items() if q not in evicted}
    return kept, evicted


@dataclass
class _LoopContext:
    """Read-only context threaded through per-sample processing."""

    search_point: JobSearchPoint
    session: Session
    prior_results: dict[str, QueryResult]
    on_result: Callable[[QueryResult, int, int], None] | None
    on_start: Callable[[str, int, int], None] | None
    axes: AxisIndex | None
    scorer: Scorer  # narrowed from session.scoring.scorer (asserted non-None on construction)
    scorer_id: str
    scorer_formula: str | None
    # Queries whose prior-cache entry was a deprecated sample — value is the
    # cached entry itself so display can show the original DEPR row before
    # the retry row is rendered.
    evicted_priors: dict[str, QueryResult]
    # Persist the results-so-far after each fresh measurement. Cache hits do
    # not call this — their on-disk row is unchanged.
    persist_fresh: Callable[[list[QueryResult]], None]


@dataclass
class _LoopState:
    """Mutable state accumulated across the dataset loop."""

    results: list[QueryResult]
    consecutive_errors: int = 0


@dataclass
class _SampleOutcome:
    """Outcome of processing one sample. Both fields ``None`` ⇒ continue normally."""

    abort_reason: str | None = None
    escalation: EscalationSignal | None = None


async def _maybe_recover_degraded(
    result: QueryResult,
    sample: Sample,
    ctx: _LoopContext,
) -> QueryResult:
    """Run the stale-data protocol if the result is degraded and protocol is wired."""
    if not (_is_degraded(result) and ctx.session.stale_data_load_protocol):
        return result
    recovered, _step = await _execute_stale_data_protocol(
        ctx.session.stale_data_load_protocol,
        sample,
        cast(dict[str, Any], result),
        ctx.session,
        pipeline_params=ctx.search_point.pipeline_params,
        axes=ctx.axes,
    )
    return cast(QueryResult, recovered)


def _classify_abort(
    result: QueryResult,
    state: _LoopState,
    session: Session,
) -> str:
    """Return abort reason for an errored result, or "" to continue.

    Mutates ``state.consecutive_errors``: increments on server-side errors,
    resets to 0 here (caller must reset on success path separately).
    """
    cat = error_category(result.get("error"))
    if cat in {ErrorCategory.CLIENT, ErrorCategory.PIPELINE}:
        return f"skipped_after_{cat or 'pipeline'}_error"
    state.consecutive_errors += 1
    if state.consecutive_errors >= session.max_consecutive_errors:
        return "skipped_after_consecutive_errors"
    return ""


async def _process_cache_hit(
    sample: Sample,
    idx: int,
    dataset_len: int,
    state: _LoopState,
    ctx: _LoopContext,
    check_escalation: Callable[[], EscalationSignal | None],
) -> _SampleOutcome:
    """Materialize a prior-cache result, append, and check escalation."""
    cached_r = _materialize_cached(
        ctx.prior_results[sample.query],
        ctx.scorer,
        ctx.scorer_id,
        ctx.scorer_formula,
    )
    cached_r = await _maybe_recover_degraded(cached_r, sample, ctx)
    # Overlay current-run sample_id — archived traces may predate the field.
    cached_r["sample_id"] = sample.id
    state.results.append(cached_r)
    if ctx.on_result is not None:
        ctx.on_result(cached_r, idx, dataset_len)
    # Elimination must see cached rows too — otherwise a candidate
    # whose priors already dominate it runs one extra real query.
    esc = check_escalation()
    return _SampleOutcome(escalation=esc) if esc else _SampleOutcome()


async def _process_fresh_sample(
    sample: Sample,
    idx: int,
    dataset_len: int,
    state: _LoopState,
    ctx: _LoopContext,
    check_escalation: Callable[[], EscalationSignal | None],
) -> _SampleOutcome:
    """Backend-measure one sample, classify errors, check escalation.

    When the query had a deprecated prior, render the original cached row
    (rescored under the active scorer) before the fresh measurement so the
    operator sees the broken DEPR row immediately above its retry.
    """
    if (cached_deprecated := ctx.evicted_priors.get(sample.query)) is not None:
        display_cached = _materialize_cached(
            cached_deprecated, ctx.scorer, ctx.scorer_id, ctx.scorer_formula
        )
        display_cached["sample_id"] = sample.id
        if ctx.on_result is not None:
            ctx.on_result(display_cached, idx, dataset_len)
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
    if sample.query in ctx.evicted_priors:
        cast(dict[str, Any], result)["retry_of_deprecated_cache"] = True
    state.results.append(result)
    ctx.persist_fresh(state.results)

    if is_error_result(result):
        abort_reason = _classify_abort(result, state, ctx.session)
        if abort_reason:
            return _SampleOutcome(abort_reason=abort_reason)
    else:
        state.consecutive_errors = 0

    if ctx.on_result is not None:
        ctx.on_result(result, idx, dataset_len)

    esc = check_escalation()
    return _SampleOutcome(escalation=esc) if esc else _SampleOutcome()


async def _run_query_loop(
    search_point: JobSearchPoint,
    dataset: list[Sample],
    session: Session,
    *,
    prior_results: dict[str, QueryResult],
    evicted_priors: dict[str, QueryResult],
    on_result: Callable[[QueryResult, int, int], None] | None,
    on_start: Callable[[str, int, int], None] | None,
    degradation_checks: list | None,
    candidate_idx: int,
    n_total_candidates: int,
    axes: AxisIndex | None,
    persist_fresh: Callable[[list[QueryResult]], None],
) -> QueryLoopResult:
    """Score dataset samples, reusing prior results where available."""
    assert session.scoring.scorer is not None, "session.scoring.scorer required for scoring"
    state = _LoopState(results=[])
    ctx = _LoopContext(
        search_point=search_point,
        session=session,
        prior_results=prior_results,
        on_result=on_result,
        on_start=on_start,
        axes=axes,
        scorer=session.scoring.scorer,
        scorer_id=session.scoring.scorer_id,
        scorer_formula=session.scoring.scorer_formula,
        evicted_priors=evicted_priors,
        persist_fresh=persist_fresh,
    )

    def _check_escalation() -> EscalationSignal | None:
        for c in degradation_checks or []:
            signal = c.check(state.results, candidate_idx, n_total_candidates)
            if signal is not None:
                return signal
        return None

    try:
        for i, sample in enumerate(dataset):
            if session.stop_check and session.stop_check():
                logger.debug("Graceful stop after query %d/%d.", len(state.results), len(dataset))
                return QueryLoopResult(state.results, completed=False, stop_reason="graceful")

            if on_start is not None:
                on_start(sample.query, i, len(dataset))

            handler = _process_cache_hit if sample.query in prior_results else _process_fresh_sample
            outcome = await handler(sample, i, len(dataset), state, ctx, _check_escalation)

            if outcome.escalation:
                return QueryLoopResult(
                    state.results,
                    completed=False,
                    stop_reason="escalation",
                    escalation_signal=outcome.escalation,
                )
            if outcome.abort_reason:
                logger.warning(
                    "Aborting scoring: %s on query %d. Marking remaining %d as errors.",
                    outcome.abort_reason,
                    i + 1,
                    len(dataset) - i - 1,
                )
                state.results.extend(
                    _error_result(rq, outcome.abort_reason) for rq in dataset[i + 1 :]
                )
                return QueryLoopResult(
                    state.results, completed=False, stop_reason=outcome.abort_reason
                )
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.warning(
            "Query loop force-interrupted at query %d/%d.", len(state.results), len(dataset)
        )
        return QueryLoopResult(state.results, completed=False, stop_reason="force")

    return QueryLoopResult(state.results)


async def score_search_point(
    search_point: JobSearchPoint,
    dataset: list[Sample],
    session: Session,
    *,
    label: str = "Score",
    on_result: Callable[[QueryResult, int, int], None] | None = None,
    on_start: Callable[[str, int, int], None] | None = None,
    source: str = "",
    degradation_checks: list | None = None,
    candidate_idx: int = 0,
    n_total_candidates: int = 1,
    axes: AxisIndex | None = None,
    l1_diversity: float = 1.0,
) -> tuple[list[QueryResult], dict[str, Any], bool, EscalationSignal | None]:
    """Score a search point via backend with chain-addressed caching.

    Two cache tiers (prior-result + per-node) share one prefix chain. Each
    fresh measurement is persisted immediately so retried deprecated samples
    and any in-progress baseline/candidate work survive a Ctrl+C. Cache hits
    do not write — the on-disk row is unchanged. Obs logging fires on the
    final completion save.
    """
    store = session.store
    backend_id = session.backend_id
    pipeline_schema = session.pipeline_schema
    source = source or session.source
    assert pipeline_schema is not None, "pipeline_schema required for scoring"

    content_hash = search_point.content_hash(dataset)
    safe_label = label.lower().replace(" ", "_")
    run_id = f"{safe_label}_{content_hash[:8]}"

    prior_results: dict[str, QueryResult] = {}
    if store and backend_id:
        from promptpotter.application.optimization.elimination import is_deprecated

        node_configs = pipeline_schema.node_configs(search_point.pipeline_params or {})
        prior_results = cast(
            "dict[str, QueryResult]",
            store.archive.load_reusable_results(backend_id, node_configs, is_fatal=is_deprecated),
        )

    prior_results, evicted_priors = _filter_deprecated_priors(prior_results)
    if evicted_priors:
        logger.info(
            "Evicted %d deprecated prior result(s) (fatal warnings); will remeasure.",
            len(evicted_priors),
        )

    display_name = f"{session.experiment_id}_{safe_label}" if session.experiment_id else safe_label

    def _save_run(results: list[QueryResult], scores: dict[str, Any]) -> None:
        if not (store and backend_id):
            return
        run_data = build_dataset_run_data(
            run_id,
            display_name,
            content_hash,
            search_point,
            scores,
            results,
            source=source,
            experiment_id=session.experiment_id,
            pipeline_schema=pipeline_schema,
        )
        # Tee per-cycle Measurement records onto the ledger when one is
        # wired (Phase 3). The archive write is the source of truth; the
        # ledger captures which measurements happened in this cycle so a
        # fork's iter() can replay without re-scanning the global archive.
        store.archive.save(
            backend_id,
            run_id,
            run_data,
            ledger=getattr(session.state, "ledger", None) if hasattr(session, "state") else None,
        )

    def _persist_fresh(results: list[QueryResult]) -> None:
        if not (store and backend_id):
            return
        scores = compute_composite_score(
            results,
            pipeline_schema,
            round_scorer=session.scoring.round_scorer,
            l1_diversity=l1_diversity,
        )
        _save_run(results, scores)

    # Pre-register Samples so the SampleIndex carries primitives for any
    # query that lands. ``Sample.run_ids`` accumulates later, when
    # ``AxisIndex.refresh`` ingests this run from the archive.
    if session.scoring.sample_index is not None:
        session.scoring.sample_index.register_many(dataset)

    batch = await _run_query_loop(
        search_point,
        dataset,
        session,
        prior_results=prior_results,
        evicted_priors=evicted_priors,
        on_result=on_result,
        on_start=on_start,
        degradation_checks=degradation_checks,
        candidate_idx=candidate_idx,
        n_total_candidates=n_total_candidates,
        axes=axes,
        persist_fresh=_persist_fresh,
    )
    results = batch.results
    escalation_signal = batch.escalation_signal

    if not batch.completed and not escalation_signal:
        # Graceful/force stop — partial state is already on disk via
        # per-fresh-sample persist; just unwind.
        if batch.stop_reason in {"graceful", "force"}:
            raise KeyboardInterrupt()
        # Per-candidate scoring-error abort (consecutive 5xx, client 4xx,
        # pipeline ERROR). Synthesize a candidate-scoped escalation so the
        # caller can attach a RuntimeFailure and continue with the
        # next candidate — never kill the round.
        escalation_signal = _build_scoring_error_signal(
            results=results,
            stop_reason=batch.stop_reason or "",
            candidate_idx=candidate_idx,
            n_total_candidates=n_total_candidates,
        )

    scores = compute_composite_score(
        results,
        pipeline_schema,
        round_scorer=session.scoring.round_scorer,
        l1_diversity=l1_diversity,
    )

    _save_run(results, scores)
    if store and backend_id:
        from promptpotter.infrastructure.tracing import DatasetRun, ObservabilityBridge
        from promptpotter.shared.errors import graceful

        with graceful("DatasetRun emit failed"):
            obs = session.state.obs or ObservabilityBridge.file_only(store.base_dir, backend_id)
            obs.emit(
                DatasetRun(
                    campaign_id="",
                    round_num=-1,
                    run_id=run_id,
                    content_hash=content_hash,
                    prompt_fields_id=search_point.sp_hash(pipeline_schema),
                    accuracy=scores["accuracy"],
                    hits=scores["hits"],
                    total=scores["total"],
                )
            )

    return results, scores, False, escalation_signal
