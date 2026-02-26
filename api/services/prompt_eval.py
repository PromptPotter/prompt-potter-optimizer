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
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable

from api.evaluators.base import EvalResult
from api.evaluators.exact_match import ExactMatchEvaluator
from api.models.prompt_state import PromptState

_evaluator = ExactMatchEvaluator({"strip": True})

if TYPE_CHECKING:
    from api.services.backend_client import BackendClient
    from api.services.project_store import ProjectStore

logger = logging.getLogger(__name__)

# SHA256 truncated to 16 hex chars (64 bits) — sufficient for content-addressed
# deduplication within a single project.  Collision probability stays negligible
# for the expected dataset sizes (<100k eval runs).
HASH_TRUNCATE = 16


def eval_content_hash(
    rendered_prompt: str,
    eval_data: list,
    model: str,
    temperature: float,
) -> str:
    """Content-addressed hash for evaluation deduplication.

    ``sha256(rendered_prompt + sorted_query_gt_pairs + model + temperature)[:16]``

    Order of eval_data queries does not affect the hash.
    """
    pairs = sorted(
        (d.get("query", ""), d.get("ground_truth", "")) for d in eval_data
    )
    blob = json.dumps(
        {"prompt": rendered_prompt, "pairs": pairs, "model": model, "temperature": temperature},
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:HASH_TRUNCATE]


def build_dataset_run_data(
    run_id: str,
    name: str,
    content_hash: str,
    prompt_state_id: str,
    rendered_prompt: str,
    model: str,
    temperature: float,
    scores: dict,
    results: list,
) -> dict:
    """Build a DatasetRun dict ready for ProjectStore.save_dataset_run()."""
    return {
        "run_id": run_id,
        "name": name,
        "content_hash": content_hash,
        "prompt_state_id": prompt_state_id,
        "rendered_prompt_hash": hashlib.sha256(
            rendered_prompt.encode(),
        ).hexdigest()[:HASH_TRUNCATE],
        "model": model,
        "temperature": temperature,
        "item_count": scores["total"],
        "scores": scores,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset_run_items": results,
    }


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
        if r.get("status") != "success":
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


def filter_eval_data(replay_results: list) -> list:
    """Filter replay results to those with entity_profile in pipeline_data.

    Args:
        replay_results: List of replay result dicts.

    Returns:
        Filtered list containing only successful results with entity_profile.
    """
    return [
        r
        for r in replay_results
        if r.get("status") == "success"
        and r.get("pipeline_data", {}).get("entity_profile")
    ]


async def backend_reranker_eval(
    query_data: dict,
    backend_client: BackendClient,
    rendered_prompt: str,
    pipeline_params: dict | None = None,
    request_delay: float = 0,
) -> dict:
    """Evaluate a reranker prompt on a single query via the backend /matches endpoint.

    Args:
        query_data: Dict with ``query`` and ``ground_truth`` keys.
        backend_client: BackendClient with ``run_match()`` method.
        rendered_prompt: Fully rendered ranking prompt to pass as ``ranking_prompt``.
        pipeline_params: Optional pipeline parameter overrides forwarded to
            the backend's ``/matches`` endpoint.
        request_delay: Seconds to sleep before the call (0 = no delay).

    Returns:
        Dict with keys: query, predicted, ground_truth, hit, score, error.
    """
    query = query_data["query"]
    ground_truth = query_data["ground_truth"]

    try:
        pp = pipeline_params or {}
        active_steps = pp.get("steps")
        skip = active_steps is not None and "llm_ranking" not in active_steps

        resp = await backend_client.run_match(
            query,
            skip_llm_ranking=skip,
            pipeline_params=pipeline_params,
            ranking_prompt=rendered_prompt if not skip else None,
        )
        data = resp.get("data", {})
        ranked = data.get("ranked_candidates", [])
        predicted = (
            ranked[0].get("candidate", "NO_RESULT") if ranked else "NO_RESULT"
        )
        eval_output = _evaluator.evaluate(ground_truth, predicted)
        return {
            "query": query,
            "predicted": predicted,
            "ground_truth": ground_truth,
            "hit": eval_output.result == EvalResult.PASS,
            "score": eval_output.score,
            "error": None,
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
        }


async def evaluate_prompt_batch(
    prompt_state: PromptState,
    eval_data: list,
    backend_client: BackendClient,
    pipeline_params: dict | None = None,
    on_result: Callable | None = None,
    request_delay: float = 0,
) -> list:
    """Evaluate a prompt on all eval_data queries via the backend.

    Args:
        prompt_state: PromptState whose render() produces the ranking prompt.
        eval_data: List of query dicts with ``query`` and ``ground_truth``.
        backend_client: BackendClient with ``run_match()`` method.
        pipeline_params: Optional pipeline parameter overrides.
        on_result: Optional callback ``(result, index, total)`` called after
            each query evaluation.
        request_delay: Seconds to sleep between backend calls (0 = no delay).

    Returns:
        List of result dicts from backend_reranker_eval.
    """
    results = []
    rendered = prompt_state.render()

    for i, qd in enumerate(eval_data):
        if request_delay > 0 and i > 0:
            await asyncio.sleep(request_delay)

        result = await backend_reranker_eval(
            qd, backend_client, rendered,
            pipeline_params=pipeline_params,
        )
        results.append(result)

        if on_result is not None:
            on_result(result, i, len(eval_data))

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


async def evaluate_prompt_cached(
    prompt_state: PromptState,
    eval_data: list,
    backend_client: "BackendClient",
    pipeline_params: dict | None = None,
    store: "ProjectStore | None" = None,
    backend_id: str = "",
    force: bool = False,
    label: str = "Eval",
    model: str = "",
    temperature: float = 0.0,
    on_result: Callable | None = None,
    dataset_name: str | None = None,
    dataset_item_map: dict[str, str] | None = None,
) -> tuple[list, dict, bool]:
    """Evaluate a prompt with deduplication, partial resume, and finalization.

    Core evaluation logic without UI output. Handles:
    - Content-hash deduplication via ProjectStore
    - Partial result resume after crash
    - Incremental writes for crash protection
    - Final run storage

    Args:
        prompt_state: PromptState to evaluate.
        eval_data: List of query dicts with ``query`` and ``ground_truth``.
        backend_client: BackendClient with ``run_match()`` method.
        pipeline_params: Optional pipeline parameter overrides.
        store: Optional ProjectStore for caching/persistence.
        backend_id: Backend identifier (required when store is provided).
        force: Skip dedup lookup and re-evaluate.
        label: Human-readable label for the run.
        model: Model identifier for content hash.
        temperature: Temperature for content hash.
        on_result: Optional callback ``(result, index, total)`` called after
            each query evaluation (for progress reporting).
        dataset_name: Optional Langfuse dataset name for linking evaluations.
        dataset_item_map: Optional ``{query: item_id}`` mapping for dataset linking.

    Returns:
        Tuple of (results, scores_dict, was_cached).
    """
    rendered = prompt_state.render()
    content_hash = eval_content_hash(rendered, eval_data, model, temperature)

    # --- dedup lookup ---
    if store and backend_id and not force:
        existing = store.dataset_runs.load_by_hash(backend_id, content_hash)
        if existing:
            results = existing["dataset_run_items"]
            scores = existing.get("scores", compute_accuracy(results))
            if on_result is not None:
                for i, r in enumerate(results):
                    on_result({**r, "cached": True}, i, len(results))
            return results, scores, True

    # --- compute run_id for incremental writes ---
    safe_label = label.lower().replace(" ", "_")
    run_id = f"{safe_label}_{content_hash[:8]}"

    # --- check for partial results (resume after crash) ---
    partial_results: list = []
    remaining_data = eval_data
    if store and backend_id:
        partial_results = store.dataset_runs.load_partial_eval(backend_id, run_id)
        if partial_results:
            # Validate partial results align with eval_data queries
            valid = True
            for i, (pr, ed) in enumerate(zip(partial_results, eval_data)):
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
                remaining_data = eval_data
            elif len(partial_results) >= len(eval_data):
                # Truncate to expected count (guards against crash-induced duplicates)
                partial_results = partial_results[:len(eval_data)]
                remaining_data = []
            else:
                remaining_data = eval_data[len(partial_results):]
        else:
            partial_results = []

    # --- replay cached partial results to callback ---
    if on_result is not None and partial_results:
        for i, r in enumerate(partial_results):
            on_result({**r, "cached": True}, i, len(eval_data))

    # --- evaluate via backend ---
    _incremental_writer = None
    if store and backend_id:
        _incremental_writer = make_incremental_writer(store, backend_id, run_id)

    def _on_result(result: dict, index: int, total: int) -> None:
        if _incremental_writer is not None:
            _incremental_writer(result, index, total)
        if on_result is not None:
            on_result(result, len(partial_results) + index, len(eval_data))

    new_results = await evaluate_prompt_batch(
        prompt_state, remaining_data, backend_client,
        pipeline_params=pipeline_params,
        on_result=_on_result,
    )

    results = partial_results + new_results
    scores = compute_accuracy(results)

    # --- finalize: save complete run, delete .partial.jsonl ---
    if store and backend_id:
        run_data = build_dataset_run_data(
            run_id, label, content_hash, prompt_state.id,
            rendered, model, temperature, scores, results,
        )
        store.dataset_runs.finalize_eval_run(backend_id, run_id, run_data)

        # --- observability: log dataset run trace ---
        try:
            from api.services.observability_logger import ObsLogger
            obs = ObsLogger(store.base_dir, backend_id)
            obs.log_dataset_run(
                run_id=run_id,
                content_hash=content_hash,
                accuracy=scores["accuracy"],
                total=scores["total"],
                hits=scores["hits"],
                model=model,
                temperature=temperature,
                prompt_state_id=prompt_state.id,
                dataset_name=dataset_name,
                dataset_item_map=dataset_item_map,
            )
        except Exception:
            logger.warning("ObsLogger.log_dataset_run failed", exc_info=True)

    return results, scores, False


async def run_baseline_eval(
    baseline: PromptState,
    eval_data: list,
    backend_client: "BackendClient",
    pipeline_params: dict | None = None,
    store: "ProjectStore | None" = None,
    backend_id: str = "",
    experiment_id: str = "",
    model: str = "",
    temperature: float = 0.0,
    on_result: Callable | None = None,
) -> tuple[list, list]:
    """Evaluate baseline prompt and build initial campaign_rounds list.

    Args:
        baseline: Baseline PromptState.
        eval_data: Evaluation data. If empty and store+experiment_id are
            provided, attempts to load from store.
        backend_client: BackendClient for evaluation.
        pipeline_params: Optional pipeline parameter overrides.
        store: Optional ProjectStore.
        backend_id: Backend identifier.
        experiment_id: Experiment to load eval data from if eval_data is empty.
        model: Model identifier for content hash.
        temperature: Temperature for content hash.
        on_result: Optional callback for progress reporting.

    Returns:
        Tuple of (campaign_rounds, baseline_results).

    Raises:
        RuntimeError: If no evaluation data is available.
    """
    if not eval_data and store and experiment_id:
        from api.services.search.eval_dataset import load_eval_dataset
        eval_data = load_eval_dataset(store, backend_id, experiment_id)

    if not eval_data:
        raise RuntimeError(
            "No evaluation data available. "
            "Generate data first (e.g. run termnorm_backend.ipynb)."
        )

    baseline_results, scores, _cached = await evaluate_prompt_cached(
        baseline, eval_data, backend_client,
        pipeline_params=pipeline_params,
        store=store, backend_id=backend_id,
        label="Baseline",
        model=model, temperature=temperature,
        on_result=on_result,
    )

    campaign_rounds = [{
        "round": 0, "label": "baseline", "prompt_state": baseline,
        "accuracy": scores["accuracy"], "hits": scores["hits"],
        "total": scores["total"], "results": baseline_results,
    }]

    return campaign_rounds, baseline_results
