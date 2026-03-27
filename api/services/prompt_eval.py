"""
Prompt evaluation service.

Evaluates prompts via the backend's /matches endpoint,
with content-hash deduplication and alias-aware caching.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import signal
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx

from api.config.settings import NO_RESULT
from api.models.evaluator import EvalResult, ExactMatchEvaluator
from api.services.metrics import compute_composite_score
from api.shared.errors import ErrorCategory
from api.shared.hashing import HASH_TRUNCATE

if TYPE_CHECKING:
    from api.models.eval_context import EvalContext
    from api.models.pipeline_schema import PipelineSchema
    from api.models.search_point import JobSearchPoint
    from api.services.backend_client import BackendClient
    from api.services.obs.observability_logger import ObsLogger
    from api.services.project_store import ProjectStore

_evaluator = ExactMatchEvaluator({"strip": True})

logger = logging.getLogger(__name__)


@dataclass
class EvalBatchResult:
    """Return value from ``evaluate_prompt_batch()``.

    Replaces the former exception-as-data pattern (``_GracefulStop`` /
    ``_ForceStop``).  Data always flows through return values; interrupts
    are signalled via ``completed`` and ``stop_reason``.
    """

    results: list
    completed: bool = True
    stop_reason: str | None = None  # "graceful" | "force" | None


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


def _dominant_error_category(results: list) -> ErrorCategory | None:
    """Return the most common error category across errored results."""
    from collections import Counter
    cats = [_error_category(r.get("error")) for r in results if r.get("error")]
    cats = [c for c in cats if c]
    if not cats:
        return None
    return Counter(cats).most_common(1)[0][0]


def subsample_queries(
    eval_data: list[dict],
    sample_size: int,
    seed: int = 42,
) -> list[dict]:
    """Deterministic subsample of eval queries.

    Returns the full list unchanged if ``sample_size <= 0`` or the dataset
    is already small enough.
    """
    if sample_size > 0 and len(eval_data) > sample_size:
        import random
        return random.Random(seed).sample(eval_data, sample_size)
    return eval_data



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
) -> dict:
    """Build a DatasetRun dict ready for ProjectStore.save_dataset_run()."""
    rendered_prompt = search_point.render()
    data: dict = {
        "run_id": run_id,
        "name": name,
        "content_hash": content_hash,
        "prompt_fields_id": search_point.sp_hash(),
        "rendered_prompt_hash": hashlib.sha256(
            rendered_prompt.encode(),
        ).hexdigest()[:HASH_TRUNCATE],
        "model": search_point.model,
        "temperature": search_point.temperature,
        "item_count": scores["total"],
        "scores": scores,
        "source": source,
        "created_at": datetime.now(UTC).isoformat(),
        "dataset_run_items": results,
    }
    data["sp_hash"] = search_point.sp_hash()
    if search_point.pipeline_params:
        data["pipeline_params"] = search_point.pipeline_params
    if experiment_id:
        data["experiment_id"] = experiment_id
    return data



def _extract_pipeline_data(
    backend_data: dict,
    ranked_candidates: list,
    pipeline_schema: PipelineSchema,
) -> dict:
    """Extract pipeline data fields from a backend /matches response.

    Derives the key set from the ``PipelineSchema``'s observation mappings.
    """
    pd: dict = {"ranked_candidates": ranked_candidates}
    keys: set[str] = set()
    for mappings in pipeline_schema.obs_extraction_map().values():
        for m in mappings:
            keys.add(m.pipeline_key)
    # Always include infrastructure keys
    keys |= {"step_timings", "llm_provider", "total_time", "pipeline_params", "diagnostics"}
    for key in keys:
        val = backend_data.get(key)
        if val is not None:
            pd[key] = val

    # Determine terminating step: explicit from backend takes priority
    terminated_at = backend_data.get("terminated_at")
    if terminated_at is None:
        st = pd.get("step_timings")
        if st:
            terminated_at = pipeline_schema.infer_terminating_node(st)
    if terminated_at is not None:
        pd["terminated_at"] = terminated_at

    return pd


def _error_result(query: str, ground_truth: str, error_msg: str) -> dict:
    """Build a standard error result dict."""
    return {
        "query": query,
        "predicted": "ERROR",
        "ground_truth": ground_truth,
        "hit": False,
        "score": 0.0,
        "error": error_msg,
        "pipeline_data": None,
    }


def _classify_http_error(exc: httpx.HTTPStatusError) -> str:
    """Classify an HTTP error into a tagged message."""
    code = exc.response.status_code
    if 400 <= code < 500:
        return f"[{ErrorCategory.CLIENT}] HTTP {code}: {exc} — Check pipeline configuration and request parameters."
    return f"[{ErrorCategory.SERVER}] HTTP {code}: {exc} — Backend may be experiencing issues."


async def backend_reranker_evaluate(
    query_data: dict,
    backend_client: BackendClient,
    rendered_prompt: str,
    pipeline_params: dict | None = None,
    pipeline_schema: PipelineSchema | None = None,
) -> dict:
    """Evaluate a reranker prompt on a single query via the backend /matches endpoint."""
    query = query_data["query"]
    ground_truth = query_data["ground_truth"]

    if pipeline_schema is None:
        from api.models.pipeline_schema import PipelineSchema
        pipeline_schema = PipelineSchema()

    try:
        pp = pipeline_params or {}
        steps = pp.get("steps")
        include_ranking = steps is None or "llm_ranking" in steps

        resp = await backend_client.run_match(
            query,
            pipeline_params=pipeline_params,
            ranking_prompt=rendered_prompt if include_ranking else None,
        )
        data = resp.get("data", {})
        ranked = data.get("ranked_candidates", [])
        predicted = (
            ranked[0].get("candidate", NO_RESULT) if ranked else NO_RESULT
        )
        eval_output = _evaluator.evaluate(ground_truth, predicted)
        return {
            "query": query,
            "predicted": predicted,
            "ground_truth": ground_truth,
            "hit": eval_output.result == EvalResult.PASS,
            "score": eval_output.score,
            "error": None,
            "pipeline_data": _extract_pipeline_data(data, ranked, pipeline_schema),
        }
    except httpx.HTTPStatusError as exc:
        error_msg = _classify_http_error(exc)
        logger.warning("backend_reranker_evaluate for %s: %s", query[:60], error_msg)
        return _error_result(query, ground_truth, error_msg)
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        error_msg = f"[{ErrorCategory.CONNECTION}] {exc} — Backend may be down or unreachable."
        logger.warning("backend_reranker_evaluate CONNECTION for %s: %s", query[:60], error_msg)
        return _error_result(query, ground_truth, error_msg)
    except (KeyboardInterrupt, asyncio.CancelledError):
        raise
    except Exception as exc:
        logger.warning("backend_reranker_evaluate failed for %s: %s", query[:60], exc)
        return _error_result(query, ground_truth, str(exc))


async def _evaluate_single_query(
    search_point: JobSearchPoint,
    query_data: dict,
    backend_client: BackendClient,
    rendered_prompt: str,
    pipeline_schema: PipelineSchema | None,
    cached_queries: dict[str, dict] | None,
) -> tuple[dict, bool]:
    """Evaluate a single query, checking per-query cache first.

    Returns:
        Tuple of (result_dict, was_cached).  ``was_cached`` is True when
        the result came from ``cached_queries`` rather than a live backend call.
    """
    cached = (
        cached_queries.get(query_data["query"])
        if cached_queries else None
    )
    if cached is not None and not cached.get("error"):
        return {**cached, "cached": True}, True

    result = await backend_reranker_evaluate(
        query_data, backend_client, rendered_prompt,
        pipeline_params=search_point.pipeline_params,
        pipeline_schema=pipeline_schema,
    )
    return result, False


async def evaluate_prompt_batch(
    search_point: JobSearchPoint,
    eval_data: list,
    backend_client: BackendClient,
    *,
    on_result: Callable | None = None,
    pipeline_schema: PipelineSchema | None = None,
    escalation_checks: list | None = None,
    candidate_idx: int = 0,
    n_total_candidates: int = 1,
    cached_queries: dict[str, dict] | None = None,
) -> EvalBatchResult:
    """Evaluate a prompt on all eval_data queries via the backend.

    Args:
        search_point: SearchPoint whose render() produces the ranking prompt.
        eval_data: List of query dicts with ``query`` and ``ground_truth``.
        backend_client: BackendClient with ``run_match()`` method.
        on_result: Optional callback ``(result, index, total)`` called after
            each query evaluation.
        escalation_checks: Optional list of ``EscalationCheck`` instances.
            Checked after each query result — raises ``EscalationError``
            on threshold breach to abort immediately.
        cached_queries: Optional ``{query_string: result_dict}`` from prior
            runs with the same SearchPoint.  Matching queries skip the
            backend call.

    Returns:
        EvalBatchResult with results, completion status, and stop reason.

    Raises:
        EscalationError: If an escalation check triggers mid-batch.
    """
    from api.services.campaign.escalation import EscalationError

    results: list = []
    rendered = search_point.render()
    consecutive_errors = 0
    max_consecutive_errors = 3
    n_reused = 0

    # -- Graceful interrupt: 1st Ctrl+C sets flag (current query finishes),
    #    2nd Ctrl+C force-quits immediately. --
    _stop_requested = False

    def _graceful_handler(_signum: int, _frame: object) -> None:
        nonlocal _stop_requested
        if _stop_requested:
            raise KeyboardInterrupt  # 2nd Ctrl+C = force quit
        _stop_requested = True       # 1st Ctrl+C = finish current query

    old_handler = signal.signal(signal.SIGINT, _graceful_handler)
    try:
        for i, qd in enumerate(eval_data):
            if _stop_requested:
                logger.info(
                    "Graceful stop after query %d/%d.",
                    len(results), len(eval_data),
                )
                break

            result, was_cached = await _evaluate_single_query(
                search_point, qd, backend_client, rendered,
                pipeline_schema, cached_queries,
            )
            if was_cached:
                n_reused += 1
            results.append(result)

            # Cached results don't count toward consecutive error tracking
            if not was_cached and result.get("error"):
                cat = _error_category(result["error"])
                if cat is ErrorCategory.CLIENT:
                    # Client errors are deterministic — same config will
                    # fail for every query.  Abort immediately.
                    logger.warning(
                        "Aborting eval: client error (4xx) on query %d. "
                        "Marking remaining %d queries as errors.",
                        i + 1, len(eval_data) - i - 1,
                    )
                    for remaining_qd in eval_data[i + 1:]:
                        results.append(_error_result(
                            remaining_qd["query"],
                            remaining_qd.get("ground_truth", ""),
                            "skipped_after_client_error",
                        ))
                    break
                consecutive_errors += 1
                if consecutive_errors >= max_consecutive_errors:
                    logger.warning(
                        "Aborting eval: %d consecutive errors. "
                        "Marking remaining %d queries as errors.",
                        consecutive_errors, len(eval_data) - i - 1,
                    )
                    for remaining_qd in eval_data[i + 1:]:
                        results.append(_error_result(
                            remaining_qd["query"],
                            remaining_qd.get("ground_truth", ""),
                            "skipped_after_consecutive_errors",
                        ))
                    break
            else:
                consecutive_errors = 0

            if on_result is not None:
                on_result(result, i, len(eval_data))

            # Escalation checks — run after display
            if escalation_checks:
                for check in escalation_checks:
                    if not check.enabled:
                        continue
                    esc_signal = check.evaluate(
                        results, candidate_idx, n_total_candidates,
                    )
                    if esc_signal:
                        raise EscalationError(esc_signal, results)
    except (KeyboardInterrupt, asyncio.CancelledError):
        # Force quit (2nd Ctrl+C) or task cancellation
        logger.warning(
            "Eval batch force-interrupted at query %d/%d.",
            len(results), len(eval_data),
        )
        return EvalBatchResult(results, completed=False, stop_reason="force")
    finally:
        signal.signal(signal.SIGINT, old_handler)

    if n_reused:
        logger.info(
            "Per-query reuse: %d/%d queries from prior runs",
            n_reused, len(eval_data),
        )

    if _stop_requested:
        return EvalBatchResult(results, completed=False, stop_reason="graceful")

    return EvalBatchResult(results, completed=True)


def _finalize_observability(
    store: ProjectStore,
    backend_id: str,
    run_id: str,
    content_hash: str,
    scores: dict,
    model: str,
    temperature: float,
    prompt_fields_id: str,
    obs: ObsLogger | None,
) -> None:
    """Log eval run to local obs store.

    Langfuse push is handled separately via push_all_runs() to ensure
    dataset-item linking is always present.
    """
    from api.services.campaign.helpers import graceful

    with graceful("ObsLogger.log_dataset_run failed"):
        _obs = obs
        if _obs is None:
            from api.services.obs.observability_logger import ObsLogger
            _obs = ObsLogger(store.base_dir, backend_id)
        _obs.log_dataset_run(
            run_id=run_id,
            content_hash=content_hash,
            accuracy=scores["accuracy"],
            total=scores["total"],
            hits=scores["hits"],
            model=model,
            temperature=temperature,
            prompt_fields_id=prompt_fields_id,
        )


def _lookup_cached_or_aliased_result(
    store: ProjectStore,
    backend_id: str,
    content_hash: str,
    rendered: str,
    search_point: JobSearchPoint,
    eval_data: list,
    pipeline_schema: PipelineSchema | None,
    on_result: Callable | None,
) -> tuple[list, dict, bool] | None:
    """Check hash dedup and alias groups for a cached result.

    Returns (results, scores, True) on cache hit, or None on miss.
    """
    def _use_cached(run: dict) -> tuple[list, dict, bool] | None:
        results = run["dataset_run_items"]
        if results and all(r.get("error") for r in results):
            return None  # all-error — let per-query cache retry
        scores = compute_composite_score(results, pipeline_schema)
        if on_result is not None:
            for i, r in enumerate(results):
                on_result({**r, "cached": True}, i, len(results))
        return results, scores, True

    # Direct content-hash lookup
    existing = store.dataset_runs.load_by_hash(backend_id, content_hash)
    if existing and not existing.get("partial"):
        return _use_cached(existing)

    # Alias-group fallback (semantically equivalent prompt forms)
    rp_hash = hashlib.sha256(rendered.encode()).hexdigest()[:HASH_TRUNCATE]
    alias_match = store.dataset_runs.load_by_alias(
        backend_id, rp_hash, search_point.model, search_point.temperature,
        search_point.pipeline_params, len(eval_data),
    )
    if alias_match:
        return _use_cached(alias_match)

    return None


async def evaluate_prompt_cached(
    search_point: JobSearchPoint,
    eval_data: list,
    ctx: EvalContext,
    *,
    force: bool = False,
    label: str = "Eval",
    on_result: Callable | None = None,
    source: str = "",
) -> tuple[list, dict, bool]:
    """Evaluate a prompt with deduplication and finalization.

    Core evaluation logic without UI output. Handles:
    - Content-hash deduplication via ProjectStore
    - Alias-aware fallback via prompt alias groups
    - Final run storage

    The ``search_point`` bundles the search-space dimensions
    (model, temperature, pipeline_params).  Infrastructure
    params (backend_client, store, obs, …) live on ``ctx``.

    Returns:
        Tuple of (results, scores_dict, was_cached).
    """
    # Extract search-space params from SearchPoint
    model = search_point.model
    temperature = search_point.temperature

    # Unpack infrastructure from ctx
    backend_client = ctx.backend_client
    store = ctx.store
    backend_id = ctx.backend_id
    pipeline_schema = ctx.pipeline_schema
    obs = ctx.obs
    source = source or ctx.source

    rendered = search_point.render()
    content_hash = search_point.content_hash(eval_data)

    # --- dedup lookup (content hash + alias group fallback) ---
    if store and backend_id and not force:
        cached = _lookup_cached_or_aliased_result(
            store, backend_id, content_hash, rendered,
            search_point, eval_data, pipeline_schema, on_result,
        )
        if cached is not None:
            # Run escalation checks on cached results too — the per-query
            # check in evaluate_prompt_batch only fires on live calls.
            if ctx.escalation_checks:
                results, scores, was_cached = cached
                for check in ctx.escalation_checks:
                    if not check.enabled:
                        continue
                    signal = check.evaluate(
                        results, ctx.candidate_idx, ctx.n_total_candidates,
                    )
                    if signal:
                        scores["escalation_signal"] = signal.to_dict()
                        break
                return results, scores, was_cached
            return cached

    # --- evaluate via backend ---
    safe_label = label.lower().replace(" ", "_")
    run_id = f"{safe_label}_{content_hash[:8]}"
    # Prefix display name with experiment_id for discoverability
    display_name = (
        f"{ctx.experiment_id}_{safe_label}" if ctx.experiment_id
        else safe_label
    )

    from api.services.campaign.escalation import EscalationError as _EscalationError

    # SP-hash query cache: find individual query results from prior runs
    # with the same SearchPoint (prompt + model + temp + pipeline_params).
    # Bridges different sample sizes automatically.
    sp_cache: dict[str, dict] | None = None
    if store and backend_id:
        sp_h = search_point.sp_hash()
        sp_cache = store.dataset_runs.find_cached_queries(backend_id, sp_h)
        if sp_cache:
            logger.info(
                "SP cache: %d cached queries for %d eval queries",
                len(sp_cache), len(eval_data),
            )

    def _save_run(results: list, scores: dict, *, partial: bool = False) -> None:
        """Persist a dataset run (complete or partial) to the store."""
        if not (store and backend_id):
            return
        run_data = build_dataset_run_data(
            run_id, display_name, content_hash, search_point,
            scores, results, source=source,
            experiment_id=ctx.experiment_id,
        )
        if partial:
            run_data["partial"] = True
        store.dataset_runs.save(backend_id, run_id, run_data)

    escalation_signal = None
    try:
        batch = await evaluate_prompt_batch(
            search_point, eval_data, backend_client,
            on_result=on_result,
            pipeline_schema=pipeline_schema,
            escalation_checks=ctx.escalation_checks,
            candidate_idx=ctx.candidate_idx,
            n_total_candidates=ctx.n_total_candidates,
            cached_queries=sp_cache,
        )
        results = batch.results
        if not batch.completed:
            # Graceful or force stop — save partial results then re-raise
            if results:
                _save_run(results, compute_composite_score(results, pipeline_schema), partial=True)
                logger.info(
                    "Saved partial run (%d/%d queries) for SP %s",
                    len(results), len(eval_data), content_hash[:8],
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
        _finalize_observability(
            store, backend_id, run_id, content_hash, scores,
            model, temperature, search_point.sp_hash(), obs,
        )

    return results, scores, False
