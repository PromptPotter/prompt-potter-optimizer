"""Dataset scoring — query loop orchestration, archival, and scoring.

Unified per-query caching: prior-result cache (from dataset_runs) is
checked first, then per-node intermediate cache (multi-step only),
then backend call.  Also handles stale data protocol, error tracking,
escalation checks, dataset run archival, and observability logging.
Single-sample measurement lives in ``sample_measurement``.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from promptpotter.models.query_result import QueryResult
from promptpotter.services.dataset_builder import build_dataset_run_data
from promptpotter.services.metrics import compute_composite_score
from promptpotter.services.sample_measurement import (
    _error_result,
    measure_sample,
)
from promptpotter.services.stale_data import (
    execute_stale_data_protocol as _execute_stale_data_protocol,
)
from promptpotter.services.stale_data import is_degraded as _is_degraded
from promptpotter.shared.errors import ErrorCategory, error_category, is_error_result


@contextmanager
def _graceful_interrupt():
    """Yield a mutable ``[stop_requested]`` flag. 1st Ctrl+C sets it; 2nd cancels the task."""
    flag = [False]
    task = asyncio.current_task()

    def _handler(_sig: int, _frame: object) -> None:
        if flag[0]:
            if task and not task.done():
                task.cancel()
        else:
            flag[0] = True

    prev = signal.signal(signal.SIGINT, _handler)
    try:
        yield flag
    finally:
        signal.signal(signal.SIGINT, prev)


if TYPE_CHECKING:
    from promptpotter.models.scoring_context import QueryRunner, ScoringContext
    from promptpotter.models.search_point import JobSearchPoint
    from promptpotter.services.campaign.escalation import EscalationSignal
    from promptpotter.services.project_store import ProjectStore

logger = logging.getLogger(__name__)

__all__ = ["QueryLoopResult", "score_search_point"]


@dataclass
class QueryLoopResult:
    """Return value from ``_run_query_loop()``.

    Data always flows through return values; interrupts are signalled
    via ``completed`` and ``stop_reason``; escalation via
    ``escalation_signal``.
    """

    results: list
    completed: bool = True
    stop_reason: str | None = None  # "graceful" | "force" | "escalation" | None
    escalation_signal: EscalationSignal | None = None


def _fill_remaining_errors(
    results: list[QueryResult],
    dataset: list[dict[str, Any]],
    start_idx: int,
    reason: str,
) -> None:
    """Append error results for all eval queries from start_idx onward."""
    for remaining_qd in dataset[start_idx:]:
        results.append(
            _error_result(
                remaining_qd["query"],
                remaining_qd.get("ground_truth", ""),
                reason,
            )
        )


def _materialize_cached_result(item: QueryResult) -> dict:
    """Stamp a prior-cache item as cached with zeroed timing."""
    r: dict = {**item, "cached": True}
    pd = r.get("pipeline_data")
    if isinstance(pd, dict):
        r["pipeline_data"] = {**pd, "total_time": 0.0}
    return r


def _check_error_abort(
    result: QueryResult,
    was_cached: bool,
    consecutive_errors: int,
    max_consecutive_errors: int,
) -> tuple[int, str | None]:
    """Update error counter and check abort conditions.

    Returns ``(new_consecutive_errors, abort_reason_or_None)``.

    Cached results pass through without affecting the counter.  Only
    non-cached successful results reset the counter to zero.
    """
    if was_cached:
        return consecutive_errors, None
    if not is_error_result(result):
        return 0, None
    cat = error_category(result.get("error"))
    if cat in {ErrorCategory.CLIENT, ErrorCategory.PIPELINE}:
        return consecutive_errors, f"skipped_after_{cat or 'pipeline'}_error"
    consecutive_errors += 1
    if consecutive_errors >= max_consecutive_errors:
        return consecutive_errors, "skipped_after_consecutive_errors"
    return consecutive_errors, None


def _log_scoring_summary(
    n_prior: int,
    n_reused: int,
    n_total: int,
    n_retried: int,
    n_probed: int,
    n_switched: int,
) -> None:
    """Log cache reuse and stale data protocol stats."""
    if n_prior:
        logger.info(
            "Prior-result cache: %d/%d queries reused from previous runs",
            n_prior,
            n_total,
        )
    if n_reused:
        logger.info(
            "Per-node cache reuse: %d/%d queries had partial/full upstream cache",
            n_reused,
            n_total,
        )
    if n_retried or n_probed or n_switched:
        logger.info(
            "Stale data protocol: %d rerun, %d samplescan, %d sampleswitch",
            n_retried,
            n_probed,
            n_switched,
        )


def _run_degradation_checks(
    checks: list | None,
    results: list[QueryResult],
    candidate_idx: int,
    n_total_candidates: int,
) -> EscalationSignal | None:
    """Run escalation checks and return the first triggered signal, or None."""
    if not checks:
        return None
    for check in checks:
        if not check.enabled:
            continue
        sig = check.evaluate(results, candidate_idx, n_total_candidates)
        if sig:
            return sig
    return None


async def _run_query_loop(
    search_point: JobSearchPoint,
    dataset: list,
    backend_client: QueryRunner,
    *,
    prior_results: dict[str, QueryResult] | None = None,
    on_result: Callable[[QueryResult, int, int], None] | None = None,
    ctx: ScoringContext | None = None,
    degradation_checks: list | None = None,
    candidate_idx: int = 0,
    n_total_candidates: int = 1,
    save_run: Callable[..., None] | None = None,
) -> QueryLoopResult:
    """Evaluate dataset queries, reusing prior results where available.

    For each query, checks ``prior_results`` first (from prior dataset_runs
    with matching sp_hash), then falls back to ``measure_sample()`` which
    handles per-node intermediate cache internally.

    Returns:
        QueryLoopResult with results, completion status, stop reason,
        and escalation_signal if an escalation check triggered.
    """
    _prior = prior_results or {}

    # Unpack ctx with defaults for test compatibility
    pipeline_schema = ctx.pipeline_schema if ctx else None
    max_consecutive_errors = ctx.max_consecutive_errors if ctx else 3

    _stale_protocol = ctx.stale_data_load_protocol if ctx else None
    _search_memory = ctx.search_memory if ctx else None
    _stale_observations = ctx.stale_data_observations if ctx else None

    _scorer = ctx.scorer if ctx else None

    _intermediate_cache = None
    _backend_id = ctx.backend_id if ctx else ""
    if ctx and ctx.store:
        _intermediate_cache = ctx.store.intermediate_cache

    results: list[QueryResult] = []
    consecutive_errors = 0
    n_prior = n_reused = n_retried = n_probed = n_switched = 0

    with _graceful_interrupt() as interrupt:
        try:
            for i, qd in enumerate(dataset):
                if interrupt[0]:
                    logger.debug("Graceful stop after query %d/%d.", len(results), len(dataset))
                    break

                query = qd["query"]

                # Prior-result cache: reuse from previous dataset_runs
                if query in _prior:
                    n_prior += 1
                    cached_r = _materialize_cached_result(_prior[query])
                    results.append(cached_r)
                    if on_result is not None:
                        on_result(cached_r, i, len(dataset))
                    continue

                result = await measure_sample(
                    qd,
                    backend_client,
                    pipeline_params=search_point.pipeline_params,
                    pipeline_schema=pipeline_schema,
                    intermediate_cache=_intermediate_cache,
                    backend_id=_backend_id,
                    scorer=_scorer,
                )
                was_cached = bool(result.get("precomputed_through"))

                # Stale data protocol on degraded results
                if _is_degraded(result) and _stale_protocol:
                    stale_result, step_taken = await _execute_stale_data_protocol(
                        _stale_protocol,
                        qd,
                        cast(dict[str, Any], result),
                        backend_client,
                        pipeline_params=search_point.pipeline_params,
                        pipeline_schema=pipeline_schema,
                        intermediate_cache=_intermediate_cache,
                        backend_id=_backend_id,
                        search_memory=_search_memory,
                        stale_data_observations=_stale_observations,
                        scorer=_scorer,
                        stop_check=lambda: interrupt[0],
                    )
                    if step_taken == "rerun":
                        n_retried += 1
                    elif step_taken == "samplescan":
                        n_probed += 1
                    elif step_taken == "sampleswitch":
                        n_switched += 1
                    result = cast(QueryResult, stale_result)

                if was_cached:
                    n_reused += 1
                results.append(result)

                # Error abort check
                consecutive_errors, abort_reason = _check_error_abort(
                    result,
                    was_cached,
                    consecutive_errors,
                    max_consecutive_errors,
                )
                if abort_reason:
                    logger.warning(
                        "Aborting eval: %s on query %d. Marking remaining %d queries as errors.",
                        abort_reason,
                        i + 1,
                        len(dataset) - i - 1,
                    )
                    _fill_remaining_errors(results, dataset, i + 1, abort_reason)
                    break

                if on_result is not None:
                    on_result(result, i, len(dataset))

                # Incremental persistence — save after each new result
                if save_run and pipeline_schema:
                    try:
                        save_run(
                            results,
                            compute_composite_score(results, pipeline_schema),
                            partial=True,
                        )
                    except Exception:
                        logger.debug("Incremental save failed", exc_info=True)

                # Escalation checks — run after display
                esc_signal = _run_degradation_checks(
                    degradation_checks,
                    results,
                    candidate_idx,
                    n_total_candidates,
                )
                if esc_signal:
                    return QueryLoopResult(
                        results,
                        completed=False,
                        stop_reason="escalation",
                        escalation_signal=esc_signal,
                    )
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.warning(
                "Query loop force-interrupted at query %d/%d.", len(results), len(dataset)
            )
            return QueryLoopResult(results, completed=False, stop_reason="force")

    _log_scoring_summary(n_prior, n_reused, len(dataset), n_retried, n_probed, n_switched)

    if interrupt[0]:
        return QueryLoopResult(results, completed=False, stop_reason="graceful")
    return QueryLoopResult(results)


def _load_prior_results(
    store: ProjectStore,
    backend_id: str,
    sp_hash: str,
) -> dict[str, QueryResult]:
    """Build per-query result cache from prior dataset_runs with matching sp_hash.

    Loads ALL matching runs (including partial).  Individual items from
    partial runs are valid — only the run was incomplete, not the results.
    Error results are excluded — errors are transient (backend down, etc.).
    Later runs overwrite earlier ones for the same query.
    """
    cache: dict[str, QueryResult] = {}
    for entry in store.dataset_runs.find_by_sp_hash(backend_id, sp_hash):
        detail = store.dataset_runs.load_by_id(backend_id, entry["run_id"])
        if not detail:
            continue
        for item in detail.get("dataset_run_items", []):
            q = item.get("query", "")
            if q and item.get("predicted") != "ERROR":
                cache[q] = item
    return cache


def check_candidate_fully_cached(
    search_point: JobSearchPoint,
    dataset: list,
    ctx: ScoringContext,
) -> tuple[list, dict, bool]:
    """Check if a candidate has a complete cached evaluation in dataset_runs.

    Returns (results, scores, is_cached).  When *is_cached* is ``False``,
    *results* and *scores* are empty — caller should fall through to
    ``score_search_point()``.
    """
    store = ctx.store
    backend_id = ctx.backend_id
    pipeline_schema = ctx.pipeline_schema

    if not (store and backend_id and pipeline_schema):
        return [], {}, False

    prompt_nodes = pipeline_schema.prompt_node_names()
    sp_h = search_point.sp_hash(prompt_nodes)
    prior_results = _load_prior_results(store, backend_id, sp_h)

    if len(prior_results) < len(dataset):
        return [], {}, False

    if not all(qd["query"] in prior_results for qd in dataset):
        return [], {}, False

    results = [_materialize_cached_result(prior_results[qd["query"]]) for qd in dataset]

    scores = compute_composite_score(results, pipeline_schema)
    return results, scores, True


async def score_search_point(
    search_point: JobSearchPoint,
    dataset: list,
    ctx: ScoringContext,
    *,
    label: str = "Eval",
    on_result: Callable[[QueryResult, int, int], None] | None = None,
    source: str = "",
    degradation_checks: list | None = None,
    candidate_idx: int = 0,
    n_total_candidates: int = 1,
) -> tuple[list, dict, bool]:
    """Evaluate a prompt via backend with unified per-query caching.

    Cache hierarchy (single per-query decision inside the loop):
    1. Prior-result cache — reuse items from previous dataset_runs
       with the same sp_hash (including partial runs).
    2. Per-node intermediate cache — reuse upstream pipeline nodes
       (multi-step pipelines only; no-op for LLM-only).
    3. Backend call — evaluate via backend if no cache hit.

    The ``search_point`` bundles the search-space dimensions
    (pipeline_params).  Infrastructure params (backend_client,
    store, obs, ...) live on ``ctx``.

    Returns:
        Tuple of (results, scores_dict, was_cached).
    """
    # Unpack infrastructure from ctx
    backend_client = ctx.backend_client
    store = ctx.store
    backend_id = ctx.backend_id
    pipeline_schema = ctx.pipeline_schema
    prompt_nodes = pipeline_schema.prompt_node_names() if pipeline_schema else None
    obs = ctx.obs
    source = source or ctx.source

    content_hash = search_point.content_hash(dataset)
    safe_label = label.lower().replace(" ", "_")
    run_id = f"{safe_label}_{content_hash[:8]}"

    # Build per-query cache from prior dataset_runs with same sp_hash
    prior_results: dict[str, QueryResult] = {}
    if store and backend_id:
        sp_h = search_point.sp_hash(prompt_nodes)
        prior_results = _load_prior_results(store, backend_id, sp_h)

    # Prefix display name with experiment_id for discoverability
    display_name = f"{ctx.experiment_id}_{safe_label}" if ctx.experiment_id else safe_label

    def _save_run(results: list, scores: dict, *, partial: bool = False) -> None:
        """Persist a dataset run (complete or partial) to the store."""
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
            prompt_node_names=prompt_nodes,
        )
        if partial:
            run_data["partial"] = True
        store.dataset_runs.save(backend_id, run_id, run_data)

    batch = await _run_query_loop(
        search_point,
        dataset,
        backend_client,
        prior_results=prior_results,
        on_result=on_result,
        ctx=ctx,
        degradation_checks=degradation_checks,
        candidate_idx=candidate_idx,
        n_total_candidates=n_total_candidates,
        save_run=_save_run,
    )
    results = batch.results
    escalation_signal = batch.escalation_signal

    # All results from prior cache — no new work done
    all_cached = (
        batch.completed
        and len(prior_results) >= len(dataset)
        and all(qd["query"] in prior_results for qd in dataset)
    )
    if all_cached:
        assert pipeline_schema is not None, "pipeline_schema required for scoring"
        scores = compute_composite_score(results, pipeline_schema)
        return results, scores, True

    if not batch.completed and not escalation_signal:
        # Graceful or force stop — results already saved incrementally
        raise KeyboardInterrupt()

    assert pipeline_schema is not None, "pipeline_schema required for scoring"
    scores = compute_composite_score(results, pipeline_schema)
    if escalation_signal:
        scores["escalation_signal"] = escalation_signal.to_dict()

    # --- finalize: save complete run + observability ---
    _save_run(results, scores)
    if store and backend_id:
        from promptpotter.services.tracing.observability_logger import log_eval_to_obs

        log_eval_to_obs(
            store.base_dir,
            backend_id,
            run_id,
            content_hash,
            scores,
            search_point.sp_hash(prompt_nodes),
            obs,
        )

    return results, scores, False
