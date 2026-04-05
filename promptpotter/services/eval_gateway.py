"""Eval gateway — batch orchestration, archival, and scoring.

Drives the query loop over ``eval_query_via_backend()``, handles stale
data protocol, error tracking, escalation checks, dataset run archival,
and observability logging.  Single-query evaluation lives in
``eval_query``.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from promptpotter.services.eval_query import (
    _error_result,
    eval_query_via_backend,
)
from promptpotter.services.metrics import compute_composite_score
from promptpotter.services.stale_data import (
    execute_stale_data_protocol as _execute_stale_data_protocol,
)
from promptpotter.services.stale_data import is_degraded as _is_degraded
from promptpotter.shared.errors import ErrorCategory, is_error_result
from promptpotter.shared.hashing import HASH_TRUNCATE
from promptpotter.shared.signals import graceful_interrupt

if TYPE_CHECKING:
    from promptpotter.models.eval_context import EvalContext
    from promptpotter.models.search_point import JobSearchPoint
    from promptpotter.services.backend_client import BackendClient
    from promptpotter.services.obs.observability_logger import ObsLogger
    from promptpotter.services.project_store import ProjectStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class EvalBatchResult:
    """Return value from ``_run_eval_batch()``.

    Replaces the former exception-as-data pattern (``_GracefulStop`` /
    ``_ForceStop``).  Data always flows through return values; interrupts
    are signalled via ``completed`` and ``stop_reason``.
    """

    results: list
    completed: bool = True
    stop_reason: str | None = None  # "graceful" | "force" | None
    retried_degraded: int = 0
    probed_degraded: int = 0
    switched_samples: int = 0


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _error_category(error: str | None) -> ErrorCategory | None:
    """Extract error category from a ``[TAG] ...`` prefixed error string."""
    if error and error.startswith("["):
        bracket_end = error.find("]")
        if bracket_end > 0:
            tag = error[1:bracket_end]
            try:
                return ErrorCategory(tag)
            except ValueError:
                return None
    return None


def _most_common_error_category(results: list) -> ErrorCategory | None:
    """Return the most common error category across errored results."""
    cats = [_error_category(r.get("error")) for r in results if is_error_result(r)]
    cats = [c for c in cats if c]
    if not cats:
        return None
    return Counter(cats).most_common(1)[0][0]


def subsample_dataset(
    dataset: list[dict],
    sample_size: int,
    seed: int = 42,
) -> list[dict]:
    """Deterministic subsample of eval queries.

    Returns the full list unchanged if ``sample_size <= 0`` or the dataset
    is already small enough.
    """
    if sample_size > 0 and len(dataset) > sample_size:
        import random

        return random.Random(seed).sample(dataset, sample_size)
    return dataset


def build_dataset_run_data(
    run_id: str,
    name: str,
    content_hash: str,
    search_point: JobSearchPoint,
    scores: dict,
    results: list,
    *,
    source: str = "",
    experiment_id: str = "",
    prompt_node_names: list[str] | None = None,
) -> dict:
    """Build a DatasetRun dict ready for ProjectStore.save_dataset_run()."""
    rendered_prompt = search_point.render()
    sp_h = search_point.sp_hash(prompt_node_names)
    data: dict = {
        "run_id": run_id,
        "name": name,
        "content_hash": content_hash,
        "prompt_fields_id": sp_h,
        "rendered_prompt_hash": hashlib.sha256(
            rendered_prompt.encode(),
        ).hexdigest()[:HASH_TRUNCATE],
        "item_count": scores["total"],
        "scores": scores,
        "source": source,
        "created_at": datetime.now(UTC).isoformat(),
        "dataset_run_items": [
            {k: v for k, v in r.items() if k != "precomputed_through"}
            for r in results
        ],
    }
    data["sp_hash"] = sp_h
    if search_point.pipeline_params:
        data["pipeline_params"] = search_point.pipeline_params
    if experiment_id:
        data["experiment_id"] = experiment_id
    return data


def _fill_remaining_errors(
    results: list[dict],
    dataset: list[dict],
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


# ---------------------------------------------------------------------------
# Extracted helpers from _run_eval_batch
# ---------------------------------------------------------------------------


def _check_error_abort(
    result: dict,
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
    cat = _error_category(result.get("error"))
    if cat in {ErrorCategory.CLIENT, ErrorCategory.PIPELINE}:
        return consecutive_errors, f"skipped_after_{cat or 'pipeline'}_error"
    consecutive_errors += 1
    if consecutive_errors >= max_consecutive_errors:
        return consecutive_errors, "skipped_after_consecutive_errors"
    return consecutive_errors, None


def _log_batch_summary(
    n_reused: int,
    n_total: int,
    n_retried: int,
    n_probed: int,
    n_switched: int,
) -> None:
    """Log cache reuse and stale data protocol stats."""
    if n_reused:
        logger.info(
            "Per-node cache reuse: %d/%d queries had partial/full upstream cache",
            n_reused, n_total,
        )
    if n_retried or n_probed or n_switched:
        logger.info(
            "Stale data protocol: %d rerun, %d samplescan, %d sampleswitch",
            n_retried, n_probed, n_switched,
        )


def _run_escalation_checks(
    checks: list | None,
    results: list[dict],
    candidate_idx: int,
    n_total_candidates: int,
) -> object | None:
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


# ---------------------------------------------------------------------------
# Batch evaluation
# ---------------------------------------------------------------------------


async def _run_eval_batch(
    search_point: JobSearchPoint,
    dataset: list,
    backend_client: BackendClient,
    *,
    on_result: Callable[[dict, int, int], None] | None = None,
    ctx: EvalContext | None = None,
) -> EvalBatchResult:
    """Evaluate a prompt on all dataset queries via the backend.

    Per-node caching is handled inside ``eval_query_via_backend()`` via
    the intermediate cache.  This function drives the query loop, stale
    data protocol, error tracking, and escalation checks.

    Returns:
        EvalBatchResult with results, completion status, and stop reason.

    Raises:
        EscalationError: If an escalation check triggers mid-batch.
    """
    from promptpotter.shared.errors import EscalationError

    # Unpack ctx with defaults for test compatibility
    pipeline_schema = ctx.pipeline_schema if ctx else None
    escalation_checks = ctx.escalation_checks if ctx else None
    candidate_idx = ctx.candidate_idx if ctx else 0
    n_total_candidates = ctx.n_total_candidates if ctx else 1
    max_consecutive_errors = ctx.max_consecutive_errors if ctx else 3

    _stale_protocol = ctx.stale_data_load_protocol if ctx else None
    _search_memory = ctx.search_memory if ctx else None
    _stale_observations = ctx.stale_data_observations if ctx else None

    _intermediate_cache = None
    _backend_id = ctx.backend_id if ctx else ""
    if ctx and ctx.store:
        _intermediate_cache = ctx.store.intermediate_cache

    results: list = []
    consecutive_errors = 0
    n_reused = n_retried = n_probed = n_switched = 0

    with graceful_interrupt() as interrupt:
        try:
            for i, qd in enumerate(dataset):
                if interrupt.stop_requested:
                    logger.debug("Graceful stop after query %d/%d.", len(results), len(dataset))
                    break

                result = await eval_query_via_backend(
                    qd, backend_client,
                    pipeline_params=search_point.pipeline_params,
                    pipeline_schema=pipeline_schema,
                    intermediate_cache=_intermediate_cache,
                    backend_id=_backend_id,
                )
                was_cached = bool(result.get("precomputed_through"))

                # Stale data protocol on degraded results
                if _is_degraded(result) and _stale_protocol:
                    result, step_taken = await _execute_stale_data_protocol(
                        _stale_protocol, qd, result, backend_client,
                        pipeline_params=search_point.pipeline_params,
                        pipeline_schema=pipeline_schema,
                        intermediate_cache=_intermediate_cache,
                        backend_id=_backend_id,
                        search_memory=_search_memory,
                        stale_data_observations=_stale_observations,
                        stop_check=lambda: interrupt.stop_requested,
                    )
                    if step_taken == "rerun":
                        n_retried += 1
                    elif step_taken == "samplescan":
                        n_probed += 1
                    elif step_taken == "sampleswitch":
                        n_switched += 1

                if was_cached:
                    n_reused += 1
                results.append(result)

                # Error abort check
                consecutive_errors, abort_reason = _check_error_abort(
                    result, was_cached, consecutive_errors, max_consecutive_errors,
                )
                if abort_reason:
                    logger.warning(
                        "Aborting eval: %s on query %d. Marking remaining %d queries as errors.",
                        abort_reason, i + 1, len(dataset) - i - 1,
                    )
                    _fill_remaining_errors(results, dataset, i + 1, abort_reason)
                    break

                if on_result is not None:
                    on_result(result, i, len(dataset))

                # Escalation checks — run after display
                esc_signal = _run_escalation_checks(
                    escalation_checks, results, candidate_idx, n_total_candidates,
                )
                if esc_signal:
                    raise EscalationError(esc_signal, results)
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.warning("Eval batch force-interrupted at query %d/%d.", len(results), len(dataset))
            return EvalBatchResult(
                results, completed=False, stop_reason="force",
                retried_degraded=n_retried, probed_degraded=n_probed, switched_samples=n_switched,
            )

    _log_batch_summary(n_reused, len(dataset), n_retried, n_probed, n_switched)
    stale = {"retried_degraded": n_retried, "probed_degraded": n_probed, "switched_samples": n_switched}

    if interrupt.stop_requested:
        return EvalBatchResult(results, completed=False, stop_reason="graceful", **stale)
    return EvalBatchResult(results, completed=True, **stale)


# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------


def _log_eval_to_obs(
    store: ProjectStore,
    backend_id: str,
    run_id: str,
    content_hash: str,
    scores: dict,
    prompt_fields_id: str,
    obs: ObsLogger | None,
) -> None:
    """Log eval run to local obs store.

    Langfuse push is handled separately via push_all_runs() to ensure
    dataset-item linking is always present.
    """
    from promptpotter.shared.errors import graceful

    with graceful("ObsLogger.log_dataset_run failed"):
        _obs = obs
        if _obs is None:
            from promptpotter.services.obs.observability_logger import ObsLogger

            _obs = ObsLogger(store.base_dir, backend_id)
        _obs.log_dataset_run(
            run_id=run_id,
            content_hash=content_hash,
            accuracy=scores["accuracy"],
            total=scores["total"],
            hits=scores["hits"],
            prompt_fields_id=prompt_fields_id,
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def eval_search_point(
    search_point: JobSearchPoint,
    dataset: list,
    ctx: EvalContext,
    *,
    label: str = "Eval",
    on_result: Callable[[dict, int, int], None] | None = None,
    source: str = "",
) -> tuple[list, dict, bool]:
    """Evaluate a prompt via backend with per-node caching and finalization.

    All cache reuse is handled by the per-node intermediate cache inside
    ``eval_query_via_backend()``.  This function handles run archival
    (dataset_run_store) and observability logging.

    The ``search_point`` bundles the search-space dimensions
    (pipeline_params).  Infrastructure params (backend_client,
    store, obs, …) live on ``ctx``.

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

    # --- evaluate via backend (per-node cache handles all reuse) ---
    safe_label = label.lower().replace(" ", "_")
    run_id = f"{safe_label}_{content_hash[:8]}"
    # Prefix display name with experiment_id for discoverability
    display_name = f"{ctx.experiment_id}_{safe_label}" if ctx.experiment_id else safe_label

    from promptpotter.shared.errors import EscalationError as _EscalationError

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

    escalation_signal = None
    try:
        batch = await _run_eval_batch(
            search_point,
            dataset,
            backend_client,
            on_result=on_result,
            ctx=ctx,
        )
        results = batch.results
        if not batch.completed:
            # Graceful or force stop — save partial results then re-raise
            if results:
                _save_run(results, compute_composite_score(results, pipeline_schema), partial=True)
                logger.info(
                    "Saved partial run (%d/%d queries) for SP %s",
                    len(results),
                    len(dataset),
                    content_hash[:8],
                )
            raise KeyboardInterrupt()
    except _EscalationError as e:
        results = e.partial_results
        escalation_signal = e.signal
    scores = compute_composite_score(results, pipeline_schema)
    if escalation_signal:
        scores["escalation_signal"] = escalation_signal.to_dict()

    # --- finalize: save complete run + observability ---
    _save_run(results, scores)
    if store and backend_id:
        _log_eval_to_obs(
            store,
            backend_id,
            run_id,
            content_hash,
            scores,
            search_point.sp_hash(prompt_nodes),
            obs,
        )

    return results, scores, False
