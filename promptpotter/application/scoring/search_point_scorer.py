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
from promptpotter.application.scoring.metrics import compute_composite_fitness
from promptpotter.application.scoring.sample_measurement import (
    _error_result,
    measure_sample,
)
from promptpotter.application.scoring.sample_measurement import (
    execute_stale_data_protocol as _execute_stale_data_protocol,
)
from promptpotter.domain.analysis import EscalationSignal, EscalationTarget
from promptpotter.domain.scoring import QueryMeasurement, Scorer
from promptpotter.domain.validators import StopRule
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

__all__ = ["QueryLoopResult", "merge_with_unprocessed_priors", "score_search_point"]


def merge_with_unprocessed_priors(
    results: list[QueryMeasurement],
    *,
    cached_sample_results: dict[str, QueryMeasurement],
    dataset_queries: set[str],
    deprecated_samples: dict[str, QueryMeasurement],
    scorer: Scorer | None,
    scorer_id: str,
    scorer_formula: str | None,
) -> list[QueryMeasurement]:
    """Union results with cache priors for unprocessed dataset queries; rescore via active scorer.

    Without this, partial runs (cache hits + Ctrl+C) shrink the archive on
    overwrite. Evicted (deprecated) priors must re-measure, not re-archive.
    """
    processed = {r["query"] for r in results}
    merged = list(results)
    for q, prior in cached_sample_results.items():
        if q not in dataset_queries or q in processed or q in deprecated_samples:
            continue
        entry = cast(QueryMeasurement, dict(prior))
        if scorer is not None:
            rescore_results([cast(dict, entry)], scorer, scorer_id, scorer_formula)
        merged.append(entry)
    return merged


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
    return cast(QueryMeasurement, r)


def _build_scoring_error_signal(
    *,
    results: list[QueryMeasurement],
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


def _split_off_deprecated_samples(
    cached_sample_results: dict[str, QueryMeasurement],
) -> tuple[dict[str, QueryMeasurement], dict[str, QueryMeasurement]]:
    """Load-side cache split: (kept, deprecated rows that need fresh re-measure)."""
    from promptpotter.application.optimization.elimination import is_deprecated

    deprecated = {q: r for q, r in cached_sample_results.items() if is_deprecated(r)}
    kept = {q: r for q, r in cached_sample_results.items() if q not in deprecated}
    return kept, deprecated


@dataclass
class _LoopContext:
    """Read-only context threaded through per-sample processing."""

    search_point: JobSearchPoint
    session: Session
    cached_sample_results: dict[str, QueryMeasurement]
    on_sample_scored: Callable[[QueryMeasurement, int, int], None] | None
    on_sample_starting: Callable[[str, int, int], None] | None
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
    ctx: _LoopContext,
) -> QueryMeasurement:
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
    return cast(QueryMeasurement, recovered)


def _classify_abort(
    result: QueryMeasurement,
    state: _LoopState,
    session: Session,
) -> str:
    """Abort reason on error or "" to continue. Mutates state.consecutive_errors."""
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
        abort_reason = _classify_abort(result, state, ctx.session)
        if abort_reason:
            return _SampleOutcome(abort_reason=abort_reason)
    else:
        state.consecutive_errors = 0

    if ctx.on_sample_scored is not None:
        ctx.on_sample_scored(result, idx, dataset_len)

    esc = check_escalation()
    return _SampleOutcome(escalation=esc) if esc else _SampleOutcome()


async def _run_query_loop(
    search_point: JobSearchPoint,
    dataset: list[Sample],
    session: Session,
    *,
    cached_sample_results: dict[str, QueryMeasurement],
    deprecated_samples: dict[str, QueryMeasurement],
    on_sample_scored: Callable[[QueryMeasurement, int, int], None] | None,
    on_sample_starting: Callable[[str, int, int], None] | None,
    degradation_checks: list[StopRule] | None,
    candidate_idx: int,
    n_total_candidates: int,
    axes: AxisIndex | None,
    persist_fresh: Callable[[list[QueryMeasurement]], None],
) -> QueryLoopResult:
    """Score dataset samples, reusing prior results where available."""
    assert session.scoring.scorer is not None, "session.scoring.scorer required for scoring"
    state = _LoopState(results=[])
    ctx = _LoopContext(
        search_point=search_point,
        session=session,
        cached_sample_results=cached_sample_results,
        on_sample_scored=on_sample_scored,
        on_sample_starting=on_sample_starting,
        axes=axes,
        scorer=session.scoring.scorer,
        scorer_id=session.scoring.scorer_id,
        scorer_formula=session.scoring.scorer_formula,
        deprecated_samples=deprecated_samples,
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

            if on_sample_starting is not None:
                on_sample_starting(sample.query, i, len(dataset))

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
    on_sample_scored: Callable[[QueryMeasurement, int, int], None] | None = None,
    on_sample_starting: Callable[[str, int, int], None] | None = None,
    source: str = "",
    degradation_checks: list[StopRule] | None = None,
    candidate_idx: int = 0,
    n_total_candidates: int = 1,
    axes: AxisIndex | None = None,
    l1_diversity: float = 1.0,
) -> tuple[list[QueryMeasurement], dict[str, Any], bool, EscalationSignal | None]:
    """Score search point with chain-addressed cache; per-sample persist (Ctrl+C-safe)."""
    store = session.store
    backend_id = session.backend_id
    pipeline_schema = session.pipeline_schema
    source = source or session.source
    assert pipeline_schema is not None, "pipeline_schema required for scoring"

    content_hash = search_point.content_hash(dataset)
    safe_label = label.lower().replace(" ", "_")
    run_id = f"{safe_label}_{content_hash[:8]}"

    cached_sample_results: dict[str, QueryMeasurement] = {}
    if store and backend_id:
        from promptpotter.application.optimization.elimination import is_deprecated

        node_configs = pipeline_schema.node_configs(search_point.pipeline_params or {})
        cached_sample_results = cast(
            "dict[str, QueryMeasurement]",
            store.archive.load_reusable_results(backend_id, node_configs, is_fatal=is_deprecated),
        )

    cached_sample_results, deprecated_samples = _split_off_deprecated_samples(cached_sample_results)
    if deprecated_samples:
        logger.info(
            "Evicted %d deprecated prior result(s) (fatal warnings); will remeasure.",
            len(deprecated_samples),
        )

    display_name = f"{session.experiment_id}_{safe_label}" if session.experiment_id else safe_label
    dataset_queries = {s.query for s in dataset}

    # Preamble: when the JSP-keyed archive already covers some/all of this
    # dataset's queries, announce the split so the operator sees inline
    # whether the upcoming per-sample lines are cache replays vs fresh
    # measurements. Suppressed when no priors match the current dataset.
    cached_in_dataset = sum(
        1 for q in cached_sample_results if q in dataset_queries and q not in deprecated_samples
    )
    if cached_in_dataset:
        total = len(dataset)
        fresh = total - cached_in_dataset
        print(
            f"{label} cache: {cached_in_dataset}/{total} already measured for this JSP "
            f"— will replay {cached_in_dataset}, measure {fresh} fresh.",
            flush=True,
        )

    def _merged_view(results: list[QueryMeasurement]) -> list[QueryMeasurement]:
        return merge_with_unprocessed_priors(
            results,
            cached_sample_results=cached_sample_results,
            dataset_queries=dataset_queries,
            deprecated_samples=deprecated_samples,
            scorer=session.scoring.scorer,
            scorer_id=session.scoring.scorer_id,
            scorer_formula=session.scoring.scorer_formula,
        )

    def _save_run(results: list[QueryMeasurement], scores: dict[str, Any]) -> None:
        if not (store and backend_id):
            return
        merged = _merged_view(results)
        run_data = build_dataset_run_data(
            run_id,
            display_name,
            content_hash,
            search_point,
            scores,
            merged,
            source=source,
            experiment_id=session.experiment_id,
            pipeline_schema=pipeline_schema,
        )
        store.archive.save(backend_id, run_id, run_data)

    def _persist_fresh(results: list[QueryMeasurement]) -> None:
        if not (store and backend_id):
            return
        merged = _merged_view(results)
        scores = compute_composite_fitness(
            merged,
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
        cached_sample_results=cached_sample_results,
        deprecated_samples=deprecated_samples,
        on_sample_scored=on_sample_scored,
        on_sample_starting=on_sample_starting,
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

    scores = compute_composite_fitness(
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
