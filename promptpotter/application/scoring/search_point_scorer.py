"""Dataset scoring — query loop orchestration, archival, and observability.

Reuses per-query results from prior dataset_runs via
``DatasetRunStore.load_reusable_results`` (addressed by
``PipelineSchema.node_configs``).  Single-sample measurement lives in
``sample_measurement``; the stale-data ladder in ``stale_data``.

Interrupt handling is pulled in from the caller via ``ScoringEnv.stop_check``
(polled between queries).  A hard cancel (``KeyboardInterrupt`` /
``CancelledError`` propagating through ``measure_sample``) still yields
``stop_reason="force"``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.datasets.builder import build_dataset_run_data
from promptpotter.application.scoring.metrics import compute_composite_score
from promptpotter.application.scoring.sample_measurement import (
    _error_result,
    measure_sample,
)
from promptpotter.application.scoring.stale_data import (
    execute_stale_data_protocol as _execute_stale_data_protocol,
)
from promptpotter.application.scoring.stale_data import is_degraded as _is_degraded
from promptpotter.domain.scoring import QueryResult
from promptpotter.shared.errors import ErrorCategory, error_category, is_error_result

if TYPE_CHECKING:
    from promptpotter.domain.analysis import EscalationSignal
    from promptpotter.domain.scoring import ScoringEnv
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


def _materialize_cached(item: QueryResult) -> QueryResult:
    """Stamp a prior-cache item as cached with zeroed timing."""
    r: dict = {**item, "cached": True}
    pd = r.get("pipeline_data")
    if isinstance(pd, dict):
        r["pipeline_data"] = {**pd, "total_time": 0.0}
    return cast(QueryResult, r)


async def _run_query_loop(
    search_point: JobSearchPoint,
    dataset: list[dict[str, Any]],
    ctx: ScoringEnv,
    *,
    prior_results: dict[str, QueryResult],
    on_result: Callable[[QueryResult, int, int], None] | None,
    degradation_checks: list | None,
    candidate_idx: int,
    n_total_candidates: int,
    save_run: Callable[..., None] | None,
) -> QueryLoopResult:
    """Evaluate dataset queries, reusing prior results where available."""
    results: list[QueryResult] = []
    consecutive_errors = 0

    try:
        for i, qd in enumerate(dataset):
            if ctx.stop_check and ctx.stop_check():
                logger.debug("Graceful stop after query %d/%d.", len(results), len(dataset))
                return QueryLoopResult(results, completed=False, stop_reason="graceful")

            query = qd["query"]

            # Reuse: prior-result cache from previous dataset_runs.
            if query in prior_results:
                cached_r = _materialize_cached(prior_results[query])
                results.append(cached_r)
                if on_result is not None:
                    on_result(cached_r, i, len(dataset))
                continue

            # Miss → backend call.
            result = await measure_sample(
                qd,
                ctx,
                pipeline_params=search_point.pipeline_params,
            )

            if _is_degraded(result) and ctx.stale_data_load_protocol:
                stale_result, _step = await _execute_stale_data_protocol(
                    ctx.stale_data_load_protocol,
                    qd,
                    cast(dict[str, Any], result),
                    ctx,
                    pipeline_params=search_point.pipeline_params,
                )
                result = cast(QueryResult, stale_result)

            results.append(result)

            # Consecutive-error abort policy.
            if is_error_result(result):
                cat = error_category(result.get("error"))
                if cat in {ErrorCategory.CLIENT, ErrorCategory.PIPELINE}:
                    abort_reason = f"skipped_after_{cat or 'pipeline'}_error"
                else:
                    consecutive_errors += 1
                    abort_reason = (
                        "skipped_after_consecutive_errors"
                        if consecutive_errors >= ctx.max_consecutive_errors
                        else ""
                    )
                if abort_reason:
                    logger.warning(
                        "Aborting scoring: %s on query %d. Marking remaining %d as errors.",
                        abort_reason,
                        i + 1,
                        len(dataset) - i - 1,
                    )
                    results.extend(
                        _error_result(rq["query"], rq.get("ground_truth", ""), abort_reason)
                        for rq in dataset[i + 1 :]
                    )
                    return QueryLoopResult(results, stop_reason=abort_reason)
            else:
                consecutive_errors = 0

            if on_result is not None:
                on_result(result, i, len(dataset))

            if save_run and ctx.pipeline_schema:
                save_run(
                    results,
                    compute_composite_score(results, ctx.pipeline_schema),
                    partial=True,
                )

            esc_signal = next(
                (
                    s
                    for c in (degradation_checks or [])
                    if c.enabled
                    for s in (c.evaluate(results, candidate_idx, n_total_candidates),)
                    if s
                ),
                None,
            )
            if esc_signal:
                return QueryLoopResult(
                    results,
                    completed=False,
                    stop_reason="escalation",
                    escalation_signal=esc_signal,
                )
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.warning("Query loop force-interrupted at query %d/%d.", len(results), len(dataset))
        return QueryLoopResult(results, completed=False, stop_reason="force")

    return QueryLoopResult(results)


async def score_search_point(
    search_point: JobSearchPoint,
    dataset: list[dict[str, Any]],
    ctx: ScoringEnv,
    *,
    label: str = "Eval",
    on_result: Callable[[QueryResult, int, int], None] | None = None,
    source: str = "",
    degradation_checks: list | None = None,
    candidate_idx: int = 0,
    n_total_candidates: int = 1,
) -> tuple[list[QueryResult], dict[str, Any], bool]:
    """Evaluate a search point via backend with chain-addressed caching.

    Two cache tiers (prior-result + per-node) share one prefix chain.
    Results are persisted incrementally and again on completion; obs logging
    fires on the final save.  ``KeyboardInterrupt`` is raised on graceful or
    force stops so the caller can unwind without discarding partial work
    (the incremental save already wrote it).
    """
    store = ctx.store
    backend_id = ctx.backend_id
    pipeline_schema = ctx.pipeline_schema
    source = source or ctx.source
    assert pipeline_schema is not None, "pipeline_schema required for scoring"

    content_hash = search_point.content_hash(dataset)
    safe_label = label.lower().replace(" ", "_")
    run_id = f"{safe_label}_{content_hash[:8]}"

    prior_results: dict[str, QueryResult] = {}
    if store and backend_id:
        node_configs = pipeline_schema.node_configs(search_point.pipeline_params or {})
        prior_results = cast(
            "dict[str, QueryResult]",
            store.dataset_runs.load_reusable_results(backend_id, node_configs),
        )

    display_name = f"{ctx.experiment_id}_{safe_label}" if ctx.experiment_id else safe_label

    def _save_run(
        results: list[QueryResult], scores: dict[str, Any], *, partial: bool = False
    ) -> None:
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
            experiment_id=ctx.experiment_id,
            pipeline_schema=pipeline_schema,
        )
        if partial:
            run_data["partial"] = True
        store.dataset_runs.save(backend_id, run_id, run_data)

    batch = await _run_query_loop(
        search_point,
        dataset,
        ctx,
        prior_results=prior_results,
        on_result=on_result,
        degradation_checks=degradation_checks,
        candidate_idx=candidate_idx,
        n_total_candidates=n_total_candidates,
        save_run=_save_run,
    )
    results = batch.results
    escalation_signal = batch.escalation_signal

    if not batch.completed and not escalation_signal:
        # Graceful/force stop — incremental save already persisted what we have.
        raise KeyboardInterrupt()

    scores = compute_composite_score(results, pipeline_schema)
    if escalation_signal:
        scores["escalation_signal"] = escalation_signal.to_dict()

    _save_run(results, scores)
    if store and backend_id:
        from promptpotter.infrastructure.tracing import ObservabilityBridge
        from promptpotter.infrastructure.tracing.events import DatasetRun
        from promptpotter.shared.errors import graceful

        with graceful("DatasetRun emit failed"):
            obs = ctx.obs or ObservabilityBridge.file_only(store.base_dir, backend_id)
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

    return results, scores, False
