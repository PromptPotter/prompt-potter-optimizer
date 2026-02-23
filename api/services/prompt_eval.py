"""
Prompt evaluation service.

Extracts baseline prompts from experiment data, filters evaluation datasets,
and evaluates reranker prompts via the TermNorm backend's /matches endpoint.
"""
import asyncio
import hashlib
import json
from datetime import datetime, timezone
from typing import Callable, Optional

from api.models.prompt_state import PromptState


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
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


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
        "rendered_prompt_hash": hashlib.sha256(rendered_prompt.encode()).hexdigest()[:16],
        "model": model,
        "temperature": temperature,
        "item_count": scores["total"],
        "scores": scores,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset_run_items": results,
    }


def make_incremental_writer(store, backend_id: str, run_id: str):
    """Return an on_result callback that appends each eval item to a .partial.jsonl."""

    def writer(result, index, total):
        store.append_eval_item(backend_id, run_id, result)

    return writer


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
    backend_client,
    rendered_prompt: str,
    pipeline_params: Optional[dict] = None,
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
        Dict with keys: query, predicted, ground_truth, hit, error.
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
        return {
            "query": query,
            "predicted": predicted,
            "ground_truth": ground_truth,
            "hit": predicted == ground_truth,
            "error": None,
        }
    except Exception as exc:
        return {
            "query": query,
            "predicted": "ERROR",
            "ground_truth": ground_truth,
            "hit": False,
            "error": str(exc),
        }


async def evaluate_prompt_batch(
    prompt_state: PromptState,
    eval_data: list,
    backend_client,
    pipeline_params: Optional[dict] = None,
    on_result: Optional[Callable] = None,
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
