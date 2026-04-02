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

from api.models.evaluator import EvalResult, ExactMatchEvaluator
from api.services.metrics import compute_composite_score, count_degraded_queries
from api.shared.constants import NO_RESULT
from api.shared.errors import ErrorCategory
from api.shared.hashing import HASH_TRUNCATE

if TYPE_CHECKING:
    from api.models.eval_context import EvalContext
    from api.models.pipeline_schema import PipelineSchema
    from api.models.search_point import JobSearchPoint
    from api.services.backend_client import BackendClient
    from api.services.obs.observability_logger import ObsLogger
    from api.services.project_store import ProjectStore
    from api.services.search.search_memory import SearchMemory
    from api.services.stores.intermediate_cache import IntermediateCache

_evaluator = ExactMatchEvaluator({"strip": True})

logger = logging.getLogger(__name__)


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
    from collections import Counter

    cats = [_error_category(r.get("error")) for r in results if r.get("error")]
    cats = [c for c in cats if c]
    if not cats:
        return None
    return Counter(cats).most_common(1)[0][0]


def subsample_eval_data(
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


def _parse_backend_response(
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


def _is_degraded(result: dict) -> bool:
    """Check if a result has pipeline degradation warnings."""
    return bool((result.get("pipeline_data") or {}).get("diagnostics", {}).get("warnings"))


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


def _fill_remaining_errors(
    results: list[dict],
    eval_data: list[dict],
    start_idx: int,
    reason: str,
) -> None:
    """Append error results for all eval queries from start_idx onward."""
    for remaining_qd in eval_data[start_idx:]:
        results.append(
            _error_result(
                remaining_qd["query"],
                remaining_qd.get("ground_truth", ""),
                reason,
            )
        )


def _classify_http_error(exc: httpx.HTTPStatusError) -> str:
    """Classify an HTTP error into a tagged message."""
    code = exc.response.status_code
    if 400 <= code < 500:
        return f"[{ErrorCategory.CLIENT}] HTTP {code}: {exc} — Check pipeline configuration and request parameters."
    return f"[{ErrorCategory.SERVER}] HTTP {code}: {exc} — Backend may be experiencing issues."


async def eval_query_via_backend(
    query_data: dict,
    backend_client: BackendClient,
    pipeline_params: dict | None = None,
    pipeline_schema: PipelineSchema | None = None,
    intermediate_cache: object | None = None,
    backend_id: str = "",
    upstream_hash: str = "",
    upstream_nodes: set[str] | None = None,
) -> dict:
    """Evaluate a reranker prompt on a single query via the backend /matches endpoint."""
    query = query_data["query"]
    ground_truth = query_data["ground_truth"]

    if pipeline_schema is None:
        from api.models.pipeline_schema import PipelineSchema

        pipeline_schema = PipelineSchema()

    try:
        # Wave 4: intermediate cache lookup
        # Filter cached outputs to upstream nodes only — downstream nodes
        # may depend on the prompt or other config that changed.
        precomputed = None
        if intermediate_cache and upstream_hash:
            cached_outputs = intermediate_cache.get(backend_id, upstream_hash, query)
            if cached_outputs:
                if upstream_nodes:
                    precomputed = {k: v for k, v in cached_outputs.items() if k in upstream_nodes}
                else:
                    precomputed = cached_outputs

        resp = await backend_client.run_match(
            query,
            pipeline_params=pipeline_params,
            precomputed=precomputed,
        )
        data = resp.get("data", {})

        # Wave 4: cache populate from response node_outputs (store all,
        # not just upstream — different perturbation targets may need different subsets)
        node_outputs = data.get("node_outputs")
        if intermediate_cache and upstream_hash and node_outputs and not precomputed:
            intermediate_cache.put(backend_id, upstream_hash, query, node_outputs)

        ranked = data.get("ranked_candidates", [])
        predicted = ranked[0].get("candidate", NO_RESULT) if ranked else NO_RESULT
        eval_output = _evaluator.evaluate(ground_truth, predicted)
        result = {
            "query": query,
            "predicted": predicted,
            "ground_truth": ground_truth,
            "hit": eval_output.result == EvalResult.PASS,
            "score": eval_output.score,
            "error": None,
            "pipeline_data": _parse_backend_response(data, ranked, pipeline_schema),
        }
        if precomputed:
            result["precomputed_through"] = list(precomputed.keys())
        return result
    except httpx.HTTPStatusError as exc:
        error_msg = _classify_http_error(exc)
        logger.warning("eval_query_via_backend for %s: %s", query[:60], error_msg)
        return _error_result(query, ground_truth, error_msg)
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        error_msg = f"[{ErrorCategory.CONNECTION}] {exc} — Backend may be down or unreachable."
        logger.warning("eval_query_via_backend CONNECTION for %s: %s", query[:60], error_msg)
        return _error_result(query, ground_truth, error_msg)
    except (KeyboardInterrupt, asyncio.CancelledError):
        raise
    except Exception as exc:
        logger.warning("eval_query_via_backend failed for %s: %s", query[:60], exc)
        return _error_result(query, ground_truth, str(exc))


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


async def _execute_stale_data_protocol(
    protocol_steps: list[str],
    query_data: dict,
    cached_result: dict,
    backend_client: BackendClient,
    *,
    pipeline_params: dict | None = None,
    pipeline_schema: PipelineSchema | None = None,
    intermediate_cache: IntermediateCache | None = None,
    backend_id: str = "",
    upstream_hash: str = "",
    upstream_nodes: set[str] | None = None,
    search_memory: SearchMemory | None = None,
    stale_data_observations: dict[str, int] | None = None,
) -> tuple[dict, str]:
    """Walk the stale data load protocol ladder for a degraded cached query.

    Hyperparameters are read from the ``l1_evaluate`` node config in
    ``optimizer_pipeline.json``.  The protocol step list and all thresholds
    live on that single node — tunable via ``param_keys`` when PromptPotter
    self-optimizes.

    Returns ``(result_dict, step_taken)`` where *step_taken* is the step
    that resolved the query, or ``"exhausted"``.
    """
    from api.config.optimizer_pipeline import get_optimizer_schema

    cfg = get_optimizer_schema().get_node("l1_evaluate").current_config
    query = query_data["query"]
    result = cached_result

    for step in protocol_steps:
        if step == "rerun":
            trigger_count = cfg.get("rerun_trigger_count", 3)
            obs_count = (stale_data_observations or {}).get(query, 0)
            if obs_count < trigger_count:
                if stale_data_observations is not None:
                    stale_data_observations[query] = obs_count + 1
                return {
                    **cached_result,
                    "cached": cached_result.get("cached", False),
                    "degraded_observed": True,
                    "degraded_obs_count": obs_count + 1,
                    "degraded_obs_threshold": trigger_count,
                }, "below_threshold"

            result = await eval_query_via_backend(
                query_data,
                backend_client,
                pipeline_params=pipeline_params,
                pipeline_schema=pipeline_schema,
                intermediate_cache=intermediate_cache,
                backend_id=backend_id,
                upstream_hash=upstream_hash,
                upstream_nodes=upstream_nodes,
            )
            result["retry_of_degraded"] = True
            if not _is_degraded(result):
                if stale_data_observations is not None:
                    stale_data_observations.pop(query, None)
                return result, "rerun"

        elif step == "samplescan":
            n_candidates = cfg.get("samplescan_candidates", 3)
            resolved_threshold = cfg.get("samplescan_threshold", 0.5)

            result = await eval_query_via_backend(
                query_data,
                backend_client,
                pipeline_params=None,
                pipeline_schema=pipeline_schema,
                intermediate_cache=None,
                backend_id=backend_id,
                upstream_hash="",
                upstream_nodes=None,
            )
            result["samplescan_probe"] = True
            result["samplescan_config"] = {
                "n_candidates": n_candidates,
                "resolved_threshold": resolved_threshold,
            }
            if not _is_degraded(result):
                return result, "samplescan"

        elif step == "sampleswitch":
            min_deg_rate = cfg.get("sampleswitch_min_degradation_rate", 0.5)
            if search_memory:
                deg_rate = search_memory.query_degradation_rate(query)
                if deg_rate >= min_deg_rate:
                    result = {**cached_result, "cached": True, "switched_out": True}
                    return result, "sampleswitch"

    return {**result, "persistently_degraded": True}, "exhausted"


async def _run_eval_batch(
    search_point: JobSearchPoint,
    eval_data: list,
    backend_client: BackendClient,
    *,
    on_result: Callable[[dict, int, int], None] | None = None,
    ctx: EvalContext | None = None,
    cached_queries: dict[str, dict] | None = None,
) -> EvalBatchResult:
    """Evaluate a prompt on all eval_data queries via the backend.

    Args:
        search_point: SearchPoint whose render() produces the ranking prompt.
        eval_data: List of query dicts with ``query`` and ``ground_truth``.
        backend_client: BackendClient with ``run_match()`` method.
        on_result: Optional callback ``(result, index, total)`` called after
            each query evaluation.
        ctx: Optional EvalContext carrying pipeline_schema, escalation_checks,
            and other infrastructure.  When None (e.g. in tests), defaults
            are used (no escalation, max 3 consecutive errors).
        cached_queries: Optional ``{query_string: result_dict}`` from prior
            runs with the same SearchPoint.  Matching queries skip the
            backend call.

    Returns:
        EvalBatchResult with results, completion status, and stop reason.

    Raises:
        EscalationError: If an escalation check triggers mid-batch.
    """
    from api.services.campaign.escalation import EscalationError

    # Unpack ctx with defaults for test compatibility
    pipeline_schema = ctx.pipeline_schema if ctx else None
    escalation_checks = ctx.escalation_checks if ctx else None
    candidate_idx = ctx.candidate_idx if ctx else 0
    n_total_candidates = ctx.n_total_candidates if ctx else 1
    max_consecutive_errors = ctx.max_consecutive_errors if ctx else 3

    _stale_protocol = ctx.stale_data_load_protocol if ctx else None
    _search_memory = ctx.search_memory if ctx else None
    _stale_observations = ctx.stale_data_observations if ctx else None

    results: list = []
    consecutive_errors = 0
    n_reused = 0
    n_retried = 0
    n_probed = 0
    n_switched = 0

    # Wave 4: compute upstream hash and cache reference for batch
    # Split at the first prompt-bearing node — everything before it is
    # stable when only the prompt changes and can be cached.
    _intermediate_cache = None
    _upstream_hash = ""
    _upstream_nodes: set[str] = set()
    _backend_id = ctx.backend_id if ctx else ""
    if ctx and ctx.store and pipeline_schema:
        _intermediate_cache = ctx.store.intermediate_cache
        prompt_nodes = pipeline_schema.prompt_node_names()
        if prompt_nodes:
            _upstream_list = pipeline_schema.upstream_of(prompt_nodes[0])
        else:
            _upstream_list, _ = pipeline_schema.split_at_ranker()
        _upstream_nodes = set(_upstream_list)
        if _upstream_list:
            from api.shared.hashing import upstream_config_hash

            _upstream_hash = upstream_config_hash(
                search_point.pipeline_params, _upstream_list,
            )

    # -- Graceful interrupt: 1st Ctrl+C sets flag (current query finishes),
    #    2nd Ctrl+C force-quits immediately. --
    _stop_requested = False

    def _graceful_handler(_signum: int, _frame: object) -> None:
        nonlocal _stop_requested
        if _stop_requested:
            raise KeyboardInterrupt  # 2nd Ctrl+C = force quit
        _stop_requested = True  # 1st Ctrl+C = finish current query

    old_handler = signal.signal(signal.SIGINT, _graceful_handler)
    try:
        for i, qd in enumerate(eval_data):
            if _stop_requested:
                logger.debug(
                    "Graceful stop after query %d/%d.",
                    len(results),
                    len(eval_data),
                )
                break

            # Per-query cache check (inlined from former _evaluate_single_query)
            cached = cached_queries.get(qd["query"]) if cached_queries else None
            if cached is not None and not cached.get("error"):
                if _is_degraded(cached) and _stale_protocol:
                    result, step_taken = await _execute_stale_data_protocol(
                        _stale_protocol,
                        qd,
                        cached,
                        backend_client,
                        pipeline_params=search_point.pipeline_params,
                        pipeline_schema=pipeline_schema,
                        intermediate_cache=_intermediate_cache,
                        backend_id=_backend_id,
                        upstream_hash=_upstream_hash,
                        upstream_nodes=_upstream_nodes or None,
                        search_memory=_search_memory,
                        stale_data_observations=_stale_observations,
                    )
                    was_cached = step_taken == "below_threshold"
                    if step_taken == "rerun":
                        n_retried += 1
                    elif step_taken == "samplescan":
                        n_probed += 1
                    elif step_taken == "sampleswitch":
                        n_switched += 1
                else:
                    result = {**cached, "cached": True}
                    was_cached = True
            else:
                result = await eval_query_via_backend(
                    qd,
                    backend_client,
                    pipeline_params=search_point.pipeline_params,
                    pipeline_schema=pipeline_schema,
                    intermediate_cache=_intermediate_cache,
                    backend_id=_backend_id,
                    upstream_hash=_upstream_hash,
                    upstream_nodes=_upstream_nodes or None,
                )
                was_cached = False
                # Fire stale data protocol on fresh degraded results too
                if _is_degraded(result) and _stale_protocol:
                    result, step_taken = await _execute_stale_data_protocol(
                        _stale_protocol,
                        qd,
                        result,
                        backend_client,
                        pipeline_params=search_point.pipeline_params,
                        pipeline_schema=pipeline_schema,
                        intermediate_cache=_intermediate_cache,
                        backend_id=_backend_id,
                        upstream_hash=_upstream_hash,
                        upstream_nodes=_upstream_nodes or None,
                        search_memory=_search_memory,
                        stale_data_observations=_stale_observations,
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

            # Cached results don't count toward consecutive error tracking
            if not was_cached and result.get("error"):
                cat = _error_category(result["error"])
                if cat is ErrorCategory.CLIENT:
                    # Client errors are deterministic — same config will
                    # fail for every query.  Abort immediately.
                    logger.warning(
                        "Aborting eval: client error (4xx) on query %d. "
                        "Marking remaining %d queries as errors.",
                        i + 1,
                        len(eval_data) - i - 1,
                    )
                    _fill_remaining_errors(results, eval_data, i + 1, "skipped_after_client_error")
                    break
                consecutive_errors += 1
                if consecutive_errors >= max_consecutive_errors:
                    logger.warning(
                        "Aborting eval: %d consecutive errors. "
                        "Marking remaining %d queries as errors.",
                        consecutive_errors,
                        len(eval_data) - i - 1,
                    )
                    _fill_remaining_errors(results, eval_data, i + 1, "skipped_after_consecutive_errors")
                    break
            else:
                consecutive_errors = 0

            if on_result is not None:
                on_result(result, i, len(eval_data))

            # Escalation checks — run after display
            esc_signal = _run_escalation_checks(
                escalation_checks, results, candidate_idx, n_total_candidates,
            )
            if esc_signal:
                raise EscalationError(esc_signal, results)
    except (KeyboardInterrupt, asyncio.CancelledError):
        # Force quit (2nd Ctrl+C) or task cancellation
        logger.warning(
            "Eval batch force-interrupted at query %d/%d.",
            len(results),
            len(eval_data),
        )
        return EvalBatchResult(
            results, completed=False, stop_reason="force",
            retried_degraded=n_retried, probed_degraded=n_probed, switched_samples=n_switched,
        )
    finally:
        signal.signal(signal.SIGINT, old_handler)

    if n_reused:
        logger.info(
            "Per-query reuse: %d/%d queries from prior runs",
            n_reused,
            len(eval_data),
        )
    if n_retried or n_probed or n_switched:
        logger.info(
            "Stale data protocol: %d rerun, %d samplescan, %d sampleswitch",
            n_retried, n_probed, n_switched,
        )

    _stale_counters = {
        "retried_degraded": n_retried,
        "probed_degraded": n_probed,
        "switched_samples": n_switched,
    }

    if _stop_requested:
        return EvalBatchResult(results, completed=False, stop_reason="graceful", **_stale_counters)

    return EvalBatchResult(results, completed=True, **_stale_counters)


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
            prompt_fields_id=prompt_fields_id,
        )


async def eval_search_point(
    search_point: JobSearchPoint,
    eval_data: list,
    ctx: EvalContext,
    *,
    force: bool = False,
    label: str = "Eval",
    on_result: Callable[[dict, int, int], None] | None = None,
    source: str = "",
) -> tuple[list, dict, bool]:
    """Evaluate a prompt with deduplication and finalization.

    Core evaluation logic without UI output. Handles:
    - Content-hash deduplication via ProjectStore
    - Alias-aware fallback via prompt alias groups
    - Final run storage

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

    rendered = search_point.render()
    rp_hash = hashlib.sha256(rendered.encode()).hexdigest()[:HASH_TRUNCATE]
    content_hash = search_point.content_hash(eval_data)

    # --- dedup lookup (content hash + alias group fallback) ---
    if store and backend_id and not force:
        _cached_run = store.dataset_runs.load_by_hash(backend_id, content_hash)
        if _cached_run and _cached_run.get("partial"):
            _cached_run = None
        if not _cached_run:
            _cached_run = store.dataset_runs.load_by_alias(
                backend_id, rp_hash, search_point.pipeline_params, len(eval_data),
            )
        if _cached_run:
            results = _cached_run["dataset_run_items"]
            if not (results and all(r.get("error") for r in results)):
                degraded_count = count_degraded_queries(results)
                if degraded_count > 0:
                    logger.info(
                        "Full-run cache has %d/%d degraded queries — falling through to per-query eval",
                        degraded_count, len(results),
                    )
                    _cached_run = None
                else:
                    scores = compute_composite_score(results, pipeline_schema)
                    if on_result is not None:
                        for i, r in enumerate(results):
                            on_result({**r, "cached": True}, i, len(results))
                    esc_signal = _run_escalation_checks(
                        ctx.escalation_checks, results, ctx.candidate_idx, ctx.n_total_candidates,
                    )
                    if esc_signal:
                        scores["escalation_signal"] = esc_signal.to_dict()
                    return results, scores, True

    # --- evaluate via backend ---
    safe_label = label.lower().replace(" ", "_")
    run_id = f"{safe_label}_{content_hash[:8]}"
    # Prefix display name with experiment_id for discoverability
    display_name = f"{ctx.experiment_id}_{safe_label}" if ctx.experiment_id else safe_label

    from api.services.campaign.escalation import EscalationError as _EscalationError

    # Per-query cache: find individual query results from prior runs
    # matching on rendered prompt + pipeline_params (strict or NN).
    # Bridges different sample sizes automatically.
    sp_cache: dict[str, dict] | None = None
    if store and backend_id:
        sp_cache = store.dataset_runs.find_cached_queries(
            backend_id, rp_hash, search_point.pipeline_params,
            strict_params=ctx.strict_params,
        )
        if sp_cache:
            logger.debug(
                "Per-query cache: %d cached queries for %d eval queries",
                len(sp_cache),
                len(eval_data),
            )

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
            eval_data,
            backend_client,
            on_result=on_result,
            ctx=ctx,
            cached_queries=sp_cache,
        )
        results = batch.results
        if not batch.completed:
            # Graceful or force stop — save partial results then re-raise
            if results:
                _save_run(results, compute_composite_score(results, pipeline_schema), partial=True)
                logger.info(
                    "Saved partial run (%d/%d queries) for SP %s",
                    len(results),
                    len(eval_data),
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
