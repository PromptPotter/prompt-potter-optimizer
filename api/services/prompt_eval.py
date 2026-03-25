"""
Prompt evaluation service.

Extracts baseline prompts from experiment data, filters evaluation datasets,
and evaluates reranker prompts via the TermNorm backend's /matches endpoint.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import signal
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable

import httpx

from api.evaluators.exact_match import EvalResult, ExactMatchEvaluator
from api.models.hashing import HASH_TRUNCATE
from api.models.opt_search_point import OptSearchPoint
from api.config.settings import NO_RESULT

if TYPE_CHECKING:
    from api.models.pipeline_schema import (
        IntermediateMetric,
        PipelineNode,
        PipelineSchema,
    )
    from api.models.search_point import SearchPoint
    from api.services.backend_client import BackendClient
    from api.services.obs.observability_logger import ObsLogger
    from api.services.project_store import ProjectStore

_evaluator = ExactMatchEvaluator({"strip": True})

logger = logging.getLogger(__name__)


class _GracefulStop(Exception):
    """Raised when a graceful interrupt (1st Ctrl+C) stops the eval loop.

    Carries ``partial_results`` — the queries completed before the stop.
    """

    def __init__(self, results: list):
        self.partial_results = results
        super().__init__(f"Graceful stop with {len(results)} results")


def _error_category(error: str | None) -> str | None:
    """Extract error category from a ``[TAG] ...`` prefixed error string."""
    if error and error.startswith("["):
        bracket_end = error.find("]")
        if bracket_end > 0:
            return error[1:bracket_end]
    return None


def _dominant_error_category(results: list) -> str | None:
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


@dataclass
class EvalContext:
    """Infrastructure bundle shared across evaluation calls.

    Pure infrastructure — does NOT carry a SearchPoint.  The search-space
    dimensions (model, temperature, pipeline_params) live here so that
    ``l1_evaluate`` can build per-candidate SearchPoints.
    """

    backend_client: BackendClient
    store: ProjectStore | None = None
    backend_id: str = ""
    pipeline_schema: PipelineSchema | None = None
    obs: ObsLogger | None = None
    source: str = ""
    model: str = ""
    temperature: float = 0.0
    pipeline_params: dict | None = None
    experiment_id: str = ""
    # Escalation checks (passed through to evaluate_prompt_batch)
    escalation_checks: list | None = None
    candidate_idx: int = 0
    n_total_candidates: int = 1

    def __post_init__(self):
        if not self.model:
            logger.debug("EvalContext created with empty model — content hashes will use default")


def build_dataset_run_data(
    run_id: str,
    name: str,
    content_hash: str,
    search_point: "SearchPoint",
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
        "prompt_state_id": search_point.sp_hash(),
        "rendered_prompt_hash": hashlib.sha256(
            rendered_prompt.encode(),
        ).hexdigest()[:HASH_TRUNCATE],
        "model": search_point.model,
        "temperature": search_point.temperature,
        "item_count": scores["total"],
        "scores": scores,
        "source": source,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset_run_items": results,
    }
    data["sp_hash"] = search_point.sp_hash()
    if search_point.pipeline_params:
        data["pipeline_params"] = search_point.pipeline_params
    if experiment_id:
        data["experiment_id"] = experiment_id
    return data


def load_baseline_prompt(exp_data: dict) -> OptSearchPoint:
    """Extract the llm_ranking prompt from experiment data, wrap in OptSearchPoint.

    Args:
        exp_data: Synced experiment data dict with ``dependencies.prompts``.

    Returns:
        OptSearchPoint with the baseline reranker prompt as ``instruction``.

    Raises:
        RuntimeError: If no llm_ranking prompt is found.
    """
    dependencies = exp_data.get("dependencies", {})
    prompts = dependencies.get("prompts", {})

    reranker_prompt = None
    for key, prompt_info in prompts.items():
        if "llm_ranking" in key:
            reranker_prompt = prompt_info
            break

    if reranker_prompt is None:
        raise RuntimeError(
            "No llm_ranking prompt found in synced experiment data. "
            "Re-sync the experiment after TermNorm prompt registry is initialized."
        )

    return OptSearchPoint(
        instruction=reranker_prompt["template"],
        changes_description="Baseline reranker_v1 from TermNorm prompt registry",
    )


def _extract_pipeline_data(
    backend_data: dict,
    ranked_candidates: list,
    pipeline_schema: "PipelineSchema",
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
            terminated_at = pipeline_schema.infer_terminating_step(st)
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
        return f"[CLIENT] HTTP {code}: {exc} — Check pipeline configuration and request parameters."
    return f"[SERVER] HTTP {code}: {exc} — Backend may be experiencing issues."


async def backend_reranker_evaluate(
    query_data: dict,
    backend_client: BackendClient,
    rendered_prompt: str,
    pipeline_params: dict | None = None,
    pipeline_schema: "PipelineSchema | None" = None,
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
        error_msg = f"[CONNECTION] {exc} — Backend may be down or unreachable."
        logger.warning("backend_reranker_evaluate CONNECTION for %s: %s", query[:60], error_msg)
        return _error_result(query, ground_truth, error_msg)
    except (KeyboardInterrupt, asyncio.CancelledError):
        raise
    except Exception as exc:
        logger.warning("backend_reranker_evaluate failed for %s: %s", query[:60], exc)
        return _error_result(query, ground_truth, str(exc))


async def evaluate_prompt_batch(
    search_point: "SearchPoint",
    eval_data: list,
    backend_client: "BackendClient",
    on_result: Callable | None = None,
    pipeline_schema: "PipelineSchema | None" = None,
    escalation_checks: list | None = None,
    candidate_idx: int = 0,
    n_total_candidates: int = 1,
    cached_queries: dict[str, dict] | None = None,
) -> list:
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
        List of result dicts from backend_reranker_evaluate.

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

            # Per-query cache reuse — skip backend call if result exists
            cached = (
                cached_queries.get(qd["query"])
                if cached_queries else None
            )
            if cached is not None and not cached.get("error"):
                result = {**cached, "cached": True}
                n_reused += 1
            else:
                result = await backend_reranker_evaluate(
                    qd, backend_client, rendered,
                    pipeline_params=search_point.pipeline_params,
                    pipeline_schema=pipeline_schema,
                )
            results.append(result)

            if cached is not None:
                # Cached results don't count toward consecutive error tracking
                pass
            elif result.get("error"):
                cat = _error_category(result["error"])
                if cat == "CLIENT":
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
        exc = KeyboardInterrupt()
        exc.partial_results = results  # type: ignore[attr-defined]
        raise exc
    finally:
        signal.signal(signal.SIGINT, old_handler)

    if n_reused:
        logger.info(
            "Per-query reuse: %d/%d queries from prior runs",
            n_reused, len(eval_data),
        )

    # Graceful stop path — raise with partial results so caller can save
    if _stop_requested:
        raise _GracefulStop(results)

    return results


def compute_accuracy(results: list) -> dict:
    """Compute accuracy metrics from evaluation results.

    Args:
        results: List of result dicts with ``hit`` and ``error`` keys.

    Returns:
        Dict with keys: hits, total, accuracy, errors.
    """
    total = len(results)
    hits = sum(1 for r in results if r.get("hit"))
    errors = sum(1 for r in results if r.get("error"))
    accuracy = hits / total if total else 0.0
    return {"hits": hits, "total": total, "accuracy": accuracy, "errors": errors}


def _step_ran(step_name: str, result: dict) -> bool:
    """Check if a step ran for a given result (via step_timings or terminated_at)."""
    pd = result.get("pipeline_data") or {}
    if pd.get("terminated_at") == step_name:
        return True
    timings = pd.get("step_timings") or {}
    return timings.get(step_name) is not None


def _candidate_name(c) -> str:
    """Extract the display name from a candidate (may be a tuple or string)."""
    return c[0] if isinstance(c, (list, tuple)) else str(c)


def _compute_recall(step: "PipelineNode", results: list[dict]) -> float:
    """Fraction of queries where GT appears in the candidate list for *step*."""
    scoped = [r for r in results if _step_ran(step.name, r) and not r.get("error")]
    if not scoped:
        return 0.0
    found = 0
    for r in scoped:
        pd = r.get("pipeline_data") or {}
        candidates = pd.get("token_matched_candidates", [])
        gt = r.get("ground_truth", "")
        if any(_candidate_name(c) == gt for c in candidates):
            found += 1
    return found / len(scoped)


def _compute_cache_hit_rate(step: "PipelineNode", results: list[dict]) -> float:
    """Fraction of queries resolved by cache (non-null cache timing)."""
    if not results:
        return 0.0
    cache_hits = 0
    for r in results:
        if r.get("error"):
            continue
        pd = r.get("pipeline_data") or {}
        timings = pd.get("step_timings") or {}
        if timings.get(step.name) is not None:
            cache_hits += 1
    non_error = sum(1 for r in results if not r.get("error"))
    return cache_hits / non_error if non_error else 0.0


def _compute_role_metric(
    metric_def: "IntermediateMetric",
    step: "PipelineNode",
    results: list[dict],
) -> float:
    """Compute a single role-based metric value."""
    if metric_def.name in ("source_recall", "candidate_recall"):
        return _compute_recall(step, results)
    if metric_def.name == "cache_hit_rate":
        return _compute_cache_hit_rate(step, results)
    return 0.0


def derive_metrics(
    pipeline_schema: "PipelineSchema",
    results: list[dict],
    *,
    metric_weights: dict[str, float] | None = None,
    accuracy_weight: float = 0.9,
) -> dict[str, float]:
    """Compute intermediate metrics from pipeline step node_roles.

    Walks ``pipeline_schema.steps``; for each with a ``node_role`` in the
    registry, computes the corresponding metric scoped to queries where
    the step ran.

    Returns dict with per-metric values and a weighted ``composite`` score.
    """
    from api.models.pipeline_schema import ROLE_METRIC_REGISTRY

    base = compute_accuracy(results)
    accuracy = base["accuracy"]
    weights = dict(metric_weights or {})
    metric_values: dict[str, float] = {}

    # Collect steps by role (namespace when >1 step shares a role)
    role_steps: dict[str, list] = {}
    for step in pipeline_schema.steps:
        if step.node_role and step.node_role in ROLE_METRIC_REGISTRY:
            role_steps.setdefault(step.node_role, []).append(step)

    for role, steps in role_steps.items():
        metrics = ROLE_METRIC_REGISTRY.get(role, [])
        needs_namespace = len(steps) > 1
        for step in steps:
            for metric_def in metrics:
                metric_name = (
                    f"{step.name}_{metric_def.name}"
                    if needs_namespace
                    else metric_def.name
                )
                metric_values[metric_name] = _compute_role_metric(
                    metric_def, step, results,
                )

    # Composite: accuracy_weight * accuracy + distributed remaining weight
    remaining_weight = 1.0 - accuracy_weight
    weighted_sum = accuracy_weight * accuracy
    if metric_values:
        n_metrics = len(metric_values)
        for m_name, m_val in metric_values.items():
            w = weights.get(m_name, remaining_weight / n_metrics)
            weighted_sum += w * m_val

    # Count queries with pipeline degradation warnings
    degraded = sum(
        1 for r in results
        if (r.get("pipeline_data") or {}).get("diagnostics", {}).get("warnings")
    )

    return {
        **base,
        **metric_values,
        "composite": round(weighted_sum, 6),
        "degraded_queries": degraded,
    }


def compute_composite_score(
    results: list,
    pipeline_schema: "PipelineSchema | None" = None,
    *,
    accuracy_weight: float = 0.9,
) -> dict:
    """Compute composite score combining accuracy with intermediate metrics.

    When ``pipeline_schema`` is provided and has steps with ``node_role``,
    uses ``derive_metrics()`` for role-based metrics.
    Otherwise falls back to hardcoded ``token_recall``.

    Returns dict with at least: hits, total, accuracy, errors, composite,
    and optionally token_recall or other role-derived metrics.
    """
    if pipeline_schema is not None:
        has_roles = any(s.node_role for s in pipeline_schema.steps)
        if has_roles:
            return derive_metrics(
                pipeline_schema, results, accuracy_weight=accuracy_weight,
            )

    # Fallback: hardcoded token_recall
    base = compute_accuracy(results)
    accuracy = base["accuracy"]

    # token_recall: for queries that reached llm_ranking, was GT in candidates?
    n_llm = 0
    found = 0
    for r in results:
        if r.get("error"):
            continue
        pd = r.get("pipeline_data") or {}
        terminated = pd.get("terminated_at")
        if terminated != "llm_ranking":
            continue
        n_llm += 1
        gt = r.get("ground_truth", "")
        candidates = pd.get("token_matched_candidates", [])
        if any(_candidate_name(c) == gt for c in candidates):
            found += 1

    token_recall = found / n_llm if n_llm else 0.0
    recall_weight = 1.0 - accuracy_weight
    composite = accuracy_weight * accuracy + recall_weight * token_recall

    # Count queries with pipeline degradation warnings
    degraded = sum(
        1 for r in results
        if (r.get("pipeline_data") or {}).get("diagnostics", {}).get("warnings")
    )

    return {
        **base,
        "token_recall": token_recall,
        "composite": round(composite, 6),
        "degraded_queries": degraded,
    }



def _finalize_observability(
    store: "ProjectStore",
    backend_id: str,
    run_id: str,
    content_hash: str,
    scores: dict,
    model: str,
    temperature: float,
    prompt_state_id: str,
    obs: "ObsLogger | None",
) -> None:
    """Log eval run to local obs store.

    Langfuse push is handled separately via push_all_runs() to ensure
    dataset-item linking is always present.
    """
    try:
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
            prompt_state_id=prompt_state_id,
        )
    except Exception:
        logger.warning("ObsLogger.log_dataset_run failed", exc_info=True)


def _try_cached_lookup(
    store: "ProjectStore",
    backend_id: str,
    content_hash: str,
    rendered: str,
    search_point: "SearchPoint",
    eval_data: list,
    pipeline_schema: "PipelineSchema | None",
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
    search_point: "SearchPoint",
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
        cached = _try_cached_lookup(
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
        results = await evaluate_prompt_batch(
            search_point, eval_data, backend_client,
            on_result=on_result,
            pipeline_schema=pipeline_schema,
            escalation_checks=ctx.escalation_checks,
            candidate_idx=ctx.candidate_idx,
            n_total_candidates=ctx.n_total_candidates,
            cached_queries=sp_cache,
        )
    except (_GracefulStop, KeyboardInterrupt, asyncio.CancelledError) as exc:
        # Save whatever queries completed so sp-hash cache can reuse them.
        partial = (
            exc.partial_results if isinstance(exc, _GracefulStop)
            else getattr(exc, "partial_results", [])
        )
        if partial:
            _save_run(partial, compute_composite_score(partial, pipeline_schema), partial=True)
            logger.info(
                "Saved partial run (%d/%d queries) for SP %s",
                len(partial), len(eval_data), content_hash[:8],
            )
        raise KeyboardInterrupt() from None
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
