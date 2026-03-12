"""
Prompt evaluation service.

Extracts baseline prompts from experiment data, filters evaluation datasets,
and evaluates reranker prompts via the TermNorm backend's /matches endpoint.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable

from api.evaluators.base import EvalResult
from api.evaluators.exact_match import ExactMatchEvaluator
from api.models.prompt_state import PromptState
from api.services.constants import NO_RESULT

if TYPE_CHECKING:
    from api.models.pipeline_schema import PipelineSchema
    from api.models.search_point import SearchPoint
    from api.services.backend_client import BackendClient
    from api.services.obs.observability_logger import ObsLogger
    from api.services.project_store import ProjectStore

_evaluator = ExactMatchEvaluator({"strip": True})

logger = logging.getLogger(__name__)

# SHA256 truncated to 16 hex chars (64 bits) — sufficient for content-addressed
# deduplication within a single project.  Collision probability stays negligible
# for the expected dataset sizes (<100k eval runs).
HASH_TRUNCATE = 16


@dataclass
class EvalContext:
    """Groups infrastructure parameters shared across evaluation calls.

    The ``search_point`` field bundles prompt_state + model + temperature +
    pipeline_params (the search-space dimensions).  Infrastructure fields
    (backend_client, store, obs, …) stay at this level.
    """

    search_point: SearchPoint
    backend_client: BackendClient
    store: ProjectStore | None = None
    backend_id: str = ""
    pipeline_schema: PipelineSchema | None = None
    obs: ObsLogger | None = None
    dataset_name: str | None = None
    dataset_item_map: dict[str, str] | None = field(default=None)
    source: str = ""


def eval_content_hash(
    rendered_prompt: str,
    eval_data: list,
    model: str,
    temperature: float,
    pipeline_params: dict | None = None,
) -> str:
    """Content-addressed hash for evaluation deduplication.

    ``sha256(rendered_prompt + sorted_query_gt_pairs + model + temperature
    + pipeline_params)[:16]``

    Order of eval_data queries does not affect the hash.
    ``pipeline_params`` is included when non-empty so that different
    pipeline configurations produce distinct hashes.
    """
    pairs = sorted(
        (d.get("query", ""), d.get("ground_truth", "")) for d in eval_data
    )
    blob_dict: dict = {
        "prompt": rendered_prompt, "pairs": pairs,
        "model": model, "temperature": temperature,
    }
    if pipeline_params:
        blob_dict["pipeline_params"] = pipeline_params
    blob = json.dumps(blob_dict, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:HASH_TRUNCATE]


def build_dataset_run_data(
    run_id: str,
    name: str,
    content_hash: str,
    search_point: "SearchPoint",
    scores: dict,
    results: list,
    *,
    source: str = "",
) -> dict:
    """Build a DatasetRun dict ready for ProjectStore.save_dataset_run()."""
    rendered_prompt = search_point.render()
    data: dict = {
        "run_id": run_id,
        "name": name,
        "content_hash": content_hash,
        "prompt_state_id": search_point.prompt_state.id,
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
    if search_point.pipeline_params:
        data["pipeline_params"] = search_point.pipeline_params
    return data


def make_incremental_writer(
    store: ProjectStore, backend_id: str, run_id: str,
) -> Callable[[dict, int, int], None]:
    """Return an on_result callback that appends each eval item to a .partial.jsonl."""

    def writer(result: dict, index: int, total: int) -> None:
        store.dataset_runs.append_eval_item(backend_id, run_id, result)

    return writer


def analyze_candidate_coverage(replay_results: list) -> dict:
    """Analyze ground truth presence in candidate lists.

    Returns dict with keys: rows (list of dicts), covered, total, coverage_pct,
    rank_distribution (dict), viable (bool).
    """
    rows = []
    for r in replay_results:
        if r.get("error"):
            continue
        pd_data = r.get("pipeline_data", {})
        candidates = pd_data.get("token_matched_candidates", [])
        gt = r["ground_truth"]

        candidate_names = []
        for c in candidates:
            if isinstance(c, (list, tuple)):
                candidate_names.append(c[0])
            else:
                candidate_names.append(str(c))

        gt_rank = None
        for i, name in enumerate(candidate_names):
            if name == gt:
                gt_rank = i + 1
                break

        rows.append({
            "query": r["query"][:50],
            "ground_truth": gt[:40],
            "in_candidates": gt_rank is not None,
            "gt_rank": gt_rank,
            "num_candidates": len(candidate_names),
        })

    total = len(rows)
    covered = sum(1 for r in rows if r["in_candidates"])
    coverage_pct = covered / total * 100 if total else 0

    # Rank distribution
    found_ranks = [r["gt_rank"] for r in rows if r["gt_rank"] is not None]
    rank_distribution = {}
    if found_ranks:
        rank_distribution = {
            "rank_1": sum(1 for r in found_ranks if r == 1),
            "rank_2_5": sum(1 for r in found_ranks if 2 <= r <= 5),
            "rank_6_10": sum(1 for r in found_ranks if 6 <= r <= 10),
            "rank_11_20": sum(1 for r in found_ranks if 11 <= r <= 20),
            "rank_gt_20": sum(1 for r in found_ranks if r > 20),
            "mean_rank": sum(found_ranks) / len(found_ranks),
            "median_rank": statistics.median(found_ranks),
        }

    return {
        "rows": rows,
        "covered": covered,
        "total": total,
        "coverage_pct": coverage_pct,
        "rank_distribution": rank_distribution,
        "viable": coverage_pct > 50,
    }


def extract_baseline_prompt(exp_data: dict) -> PromptState:
    """Extract the llm_ranking prompt from experiment data, wrap in PromptState.

    Args:
        exp_data: Synced experiment data dict with ``dependencies.prompts``.

    Returns:
        PromptState with the baseline reranker prompt as ``instruction``.

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

    return PromptState(
        instruction=reranker_prompt["template"],
        parameters={
            "family": reranker_prompt.get("family", "llm_ranking"),
            "version": reranker_prompt.get("version"),
            "template_variables": reranker_prompt.get("template_variables", []),
        },
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
    keys |= {"step_timings", "llm_provider", "total_time", "pipeline_params"}
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


async def backend_reranker_eval(
    query_data: dict,
    backend_client: BackendClient,
    rendered_prompt: str,
    pipeline_params: dict | None = None,
    pipeline_schema: "PipelineSchema | None" = None,
) -> dict:
    """Evaluate a reranker prompt on a single query via the backend /matches endpoint.

    Args:
        query_data: Dict with ``query`` and ``ground_truth`` keys.
        backend_client: BackendClient with ``run_match()`` method.
        rendered_prompt: Fully rendered ranking prompt to pass as ``ranking_prompt``.
        pipeline_params: Optional pipeline parameter overrides forwarded to
            the backend's ``/matches`` endpoint.

    Returns:
        Dict with keys: query, predicted, ground_truth, hit, score, error.
    """
    query = query_data["query"]
    ground_truth = query_data["ground_truth"]

    if pipeline_schema is None:
        from api.services.pipeline_discovery import TERMNORM_DEFAULT_SCHEMA
        pipeline_schema = TERMNORM_DEFAULT_SCHEMA

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
    except Exception as exc:
        logger.warning("backend_reranker_eval failed for %s: %s", query[:60], exc)
        return {
            "query": query,
            "predicted": "ERROR",
            "ground_truth": ground_truth,
            "hit": False,
            "score": 0.0,
            "error": str(exc),
            "pipeline_data": None,
        }


async def evaluate_prompt_batch(
    search_point: "SearchPoint",
    eval_data: list,
    backend_client: "BackendClient",
    on_result: Callable | None = None,
    pipeline_schema: "PipelineSchema | None" = None,
) -> list:
    """Evaluate a prompt on all eval_data queries via the backend.

    Args:
        search_point: SearchPoint whose render() produces the ranking prompt.
        eval_data: List of query dicts with ``query`` and ``ground_truth``.
        backend_client: BackendClient with ``run_match()`` method.
        on_result: Optional callback ``(result, index, total)`` called after
            each query evaluation.

    Returns:
        List of result dicts from backend_reranker_eval.
    """
    results = []
    rendered = search_point.render()
    consecutive_errors = 0
    max_consecutive_errors = 3

    try:
        for i, qd in enumerate(eval_data):
            result = await backend_reranker_eval(
                qd, backend_client, rendered,
                pipeline_params=search_point.pipeline_params,
                pipeline_schema=pipeline_schema,
            )
            results.append(result)

            if result.get("error"):
                consecutive_errors += 1
                if consecutive_errors >= max_consecutive_errors:
                    logger.warning(
                        "Aborting eval: %d consecutive errors. "
                        "Marking remaining %d queries as errors.",
                        consecutive_errors, len(eval_data) - i - 1,
                    )
                    for remaining_qd in eval_data[i + 1:]:
                        results.append({
                            "query": remaining_qd["query"],
                            "predicted": "ERROR",
                            "ground_truth": remaining_qd.get("ground_truth", ""),
                            "hit": False,
                            "score": 0.0,
                            "error": "skipped_after_consecutive_errors",
                            "pipeline_data": None,
                        })
                    break
            else:
                consecutive_errors = 0

            if on_result is not None:
                on_result(result, i, len(eval_data))
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.warning(
            "Eval batch interrupted at query %d/%d. "
            "Partial results saved via incremental writer.",
            len(results), len(eval_data),
        )
        raise

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


def compute_composite_score(
    results: list,
    pipeline_schema: "PipelineSchema | None" = None,
    *,
    accuracy_weight: float = 0.9,
) -> dict:
    """Compute composite score combining accuracy with intermediate metrics.

    When ``pipeline_schema`` is provided and has steps with ``node_role``,
    delegates to ``pipeline_schema.derive_metrics()`` for role-based metrics.
    Otherwise falls back to hardcoded ``token_recall``.

    Returns dict with at least: hits, total, accuracy, errors, composite,
    and optionally token_recall or other role-derived metrics.
    """
    if pipeline_schema is not None:
        has_roles = any(s.node_role for s in pipeline_schema.steps)
        if has_roles:
            return pipeline_schema.derive_metrics(
                results, accuracy_weight=accuracy_weight,
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
        for c in candidates:
            name = c[0] if isinstance(c, (list, tuple)) else str(c)
            if name == gt:
                found += 1
                break

    token_recall = found / n_llm if n_llm else 0.0
    recall_weight = 1.0 - accuracy_weight
    composite = accuracy_weight * accuracy + recall_weight * token_recall

    return {
        **base,
        "token_recall": token_recall,
        "composite": round(composite, 6),
    }



def _resolve_partial_resume(
    eval_data: list,
    run_id: str,
    *,
    store: "ProjectStore | None",
    backend_id: str,
) -> tuple[list, list]:
    """Check for .partial.jsonl crash-recovery file.

    Returns (partial_results, remaining_data).
    """
    partial_results: list = []
    remaining_data = eval_data
    if not (store and backend_id):
        return partial_results, remaining_data

    partial_results = store.dataset_runs.load_partial_eval(backend_id, run_id)
    if partial_results:
        # Detect stale partials: if the first N results are all errors,
        # the partial was written during a broken session and is unreliable.
        leading_errors = 0
        for pr in partial_results:
            if pr.get("error"):
                leading_errors += 1
            else:
                break
        if leading_errors >= 5:
            logger.warning(
                "Partial eval %s: %d leading consecutive errors "
                "— discarding stale partial file",
                run_id, leading_errors,
            )
            partial_path = (
                store.dataset_runs._runs_dir(backend_id)
                / f"{run_id}.partial.jsonl"
            )
            if partial_path.exists():
                partial_path.unlink()
            partial_results = []

    if partial_results:
        valid = True
        for i, (pr, ed) in enumerate(zip(partial_results, remaining_data)):
            if pr.get("query") != ed.get("query"):
                logger.warning(
                    "Partial eval %s: query mismatch at index %d "
                    "(%r != %r) — discarding stale partials",
                    run_id, i, pr.get("query", "")[:40], ed.get("query", "")[:40],
                )
                valid = False
                break

        if not valid:
            partial_results = []
        elif len(partial_results) >= len(remaining_data):
            partial_results = partial_results[:len(remaining_data)]
            remaining_data = []
        else:
            remaining_data = remaining_data[len(partial_results):]

    return partial_results, remaining_data


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


async def evaluate_prompt_cached(
    search_point: "SearchPoint",
    eval_data: list,
    backend_client: "BackendClient | None" = None,
    *,
    store: "ProjectStore | None" = None,
    backend_id: str = "",
    force: bool = False,
    label: str = "Eval",
    on_result: Callable | None = None,
    obs: "ObsLogger | None" = None,
    source: str = "",
    pipeline_schema: "PipelineSchema | None" = None,
    dataset_name: str | None = None,
    dataset_item_map: dict[str, str] | None = None,
    ctx: EvalContext | None = None,
) -> tuple[list, dict, bool]:
    """Evaluate a prompt with deduplication, partial resume, and finalization.

    Core evaluation logic without UI output. Handles:
    - Content-hash deduplication via ProjectStore
    - Alias-aware fallback via prompt alias groups
    - Partial result resume after crash
    - Incremental writes for crash protection
    - Final run storage

    The ``search_point`` bundles the search-space dimensions
    (prompt_state, model, temperature, pipeline_params).  Infrastructure
    params can be passed individually **or** grouped in an ``EvalContext``
    via the ``ctx`` keyword.

    Returns:
        Tuple of (results, scores_dict, was_cached).
    """
    # Extract search-space params from SearchPoint
    prompt_state = search_point.prompt_state
    model = search_point.model
    temperature = search_point.temperature

    # Resolve infrastructure from EvalContext when provided
    if ctx is not None:
        backend_client = backend_client or ctx.backend_client
        store = store or ctx.store
        backend_id = backend_id or ctx.backend_id
        pipeline_schema = pipeline_schema or ctx.pipeline_schema
        obs = obs or ctx.obs
        dataset_name = dataset_name or ctx.dataset_name
        dataset_item_map = dataset_item_map or ctx.dataset_item_map
        source = source or ctx.source

    if backend_client is None:
        raise ValueError("backend_client is required (pass directly or via ctx)")

    rendered = prompt_state.render()
    content_hash = search_point.content_hash(eval_data)

    # --- dedup lookup ---
    if store and backend_id and not force:
        existing = store.dataset_runs.load_by_hash(backend_id, content_hash)
        if existing:
            results = existing["dataset_run_items"]
            scores = compute_composite_score(results, pipeline_schema)
            if on_result is not None:
                for i, r in enumerate(results):
                    on_result({**r, "cached": True}, i, len(results))
            return results, scores, True

        # --- alias fallback (find cached run via prompt alias groups) ---
        rp_hash = hashlib.sha256(rendered.encode()).hexdigest()[:HASH_TRUNCATE]
        alias_match = store.dataset_runs.load_by_alias(
            backend_id, rp_hash, model, temperature,
            search_point.pipeline_params, len(eval_data),
        )
        if alias_match:
            results = alias_match["dataset_run_items"]
            scores = compute_composite_score(results, pipeline_schema)
            if on_result is not None:
                for i, r in enumerate(results):
                    on_result({**r, "cached": True}, i, len(results))
            return results, scores, True

    # --- resolve partial resume (crash recovery) ---
    safe_label = label.lower().replace(" ", "_")
    run_id = f"{safe_label}_{content_hash[:8]}"

    partial_results, remaining_data = _resolve_partial_resume(
        eval_data, run_id, store=store, backend_id=backend_id,
    )

    if partial_results:
        logger.info(
            "Resuming %s: %d cached results, %d remaining",
            run_id, len(partial_results), len(remaining_data),
        )

    # --- replay cached partial results to callback ---
    callback_offset = 0
    if on_result is not None and partial_results:
        for i, r in enumerate(partial_results):
            on_result({**r, "cached": True}, callback_offset + i, len(eval_data))

    # --- evaluate remaining via backend ---
    _incremental_writer = None
    if store and backend_id:
        _incremental_writer = make_incremental_writer(store, backend_id, run_id)

    _cb_offset = callback_offset + len(partial_results)

    def _on_result(result: dict, index: int, total: int) -> None:
        if _incremental_writer is not None:
            _incremental_writer(result, index, total)
        if on_result is not None:
            on_result(result, _cb_offset + index, len(eval_data))

    try:
        new_results = await evaluate_prompt_batch(
            search_point, remaining_data, backend_client,
            on_result=_on_result,
            pipeline_schema=pipeline_schema,
        )
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.warning(
            "evaluate_prompt_cached interrupted for %s. "
            "Partial results saved to %s.partial.jsonl — will resume on next run.",
            run_id, run_id,
        )
        raise

    results = partial_results + new_results
    scores = compute_composite_score(results, pipeline_schema)

    # --- finalize: save complete run + observability ---
    if store and backend_id:
        run_data = build_dataset_run_data(
            run_id, label, content_hash, search_point,
            scores, results, source=source,
        )
        store.dataset_runs.finalize_eval_run(backend_id, run_id, run_data)

        _finalize_observability(
            store, backend_id, run_id, content_hash, scores,
            model, temperature, prompt_state.id, obs,
        )

    return results, scores, False
