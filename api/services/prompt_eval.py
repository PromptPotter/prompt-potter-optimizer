"""
Prompt evaluation service.

Extracts baseline prompts from experiment data, filters evaluation datasets,
and evaluates reranker prompts against cached pipeline data using LLMClientBase.
"""
import asyncio
import hashlib
import json
import random
from datetime import datetime, timezone
from typing import Callable, Optional

from api.models.prompt_state import PromptState
from api.services.llm_client import LLMClientBase


def eval_cache_key(
    rendered_prompt: str,
    eval_data: list,
    model: str,
    temperature: float,
) -> str:
    """Content-addressed cache key for evaluation results.

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


def _render_reranker_input(
    prompt_template: str,
    query_data: dict,
) -> tuple:
    """Render a reranker prompt for a single query.

    Returns:
        Tuple of (full_prompt, query, ground_truth).
    """
    pipeline = query_data["pipeline_data"]
    entity_profile = pipeline["entity_profile"]
    candidates = pipeline.get("token_matched_candidates", [])
    ground_truth = query_data["ground_truth"]
    query = query_data["query"]

    core_concept = entity_profile.get("core_concept", "")
    entity_profile_json = json.dumps(entity_profile, indent=2)

    available = list(candidates[:20])
    sample_size = min(len(available), 20)
    sampled = random.sample(available, sample_size) if available else []
    matches = "\n".join(
        f"- {term}" if isinstance(term, str) else f"- {term[0]}"
        for term in sampled
    )

    rendered = prompt_template.replace("{{core_concept}}", str(core_concept))
    rendered = rendered.replace("{{entity_profile_json}}", entity_profile_json)
    rendered = rendered.replace("{{matches}}", matches)

    full_prompt = (
        f"{rendered}\n\n"
        "IMPORTANT: Return a valid JSON response matching this exact structure:\n"
        "{\n"
        '  "profile_summary": "Brief 1-2 sentence summary of the profile",\n'
        '  "core_concept_description": '
        '"What the core concept fundamentally is",\n'
        '  "ranked_candidates": [\n'
        "    {\n"
        '      "candidate": "exact candidate string",\n'
        '      "core_concept_score": 0.0,\n'
        '      "spec_score": 0.0,\n'
        '      "evaluation_reasoning": '
        '"Brief explanation without quotes or backslashes",\n'
        '      "key_match_factors": ["factor1", "factor2"],\n'
        '      "spec_gaps": ["gap1", "gap2"]\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "Ensure all strings are properly escaped and "
        "avoid complex punctuation in reasoning."
    )

    return full_prompt, query, ground_truth


async def local_reranker_eval(
    prompt_template: str,
    query_data: dict,
    llm_client: LLMClientBase,
    model: Optional[str] = None,
    temperature: float = 0.1,
    max_tokens: int = 4096,
) -> dict:
    """Evaluate a reranker prompt on a single query using cached pipeline data.

    Args:
        prompt_template: Prompt with ``{{core_concept}}``, ``{{entity_profile_json}}``,
            ``{{matches}}`` placeholders.
        query_data: Result dict with pipeline_data.entity_profile and
            pipeline_data.token_matched_candidates.
        llm_client: LLM client implementing LLMClientBase.
        model: Model identifier (uses client default if None).
        temperature: Sampling temperature.
        max_tokens: Maximum response tokens.

    Returns:
        Dict with keys: query, predicted, ground_truth, hit, confidence, error.
    """
    full_prompt, query, ground_truth = _render_reranker_input(
        prompt_template, query_data
    )

    try:
        response = await llm_client.chat(
            messages=[{"role": "user", "content": full_prompt}],
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            output_format="json",
        )
        parsed = response.parsed or json.loads(response.content)

        ranked = parsed.get("ranked_candidates", [])
        top = ranked[0] if ranked else {}
        predicted = top.get("candidate", "NO_RESULT")
        confidence = top.get(
            "relevance_score", top.get("core_concept_score", 0)
        )

        return {
            "query": query,
            "predicted": predicted,
            "ground_truth": ground_truth,
            "hit": predicted == ground_truth,
            "confidence": confidence,
            "error": None,
        }
    except Exception as e:
        return {
            "query": query,
            "predicted": "ERROR",
            "ground_truth": ground_truth,
            "hit": False,
            "confidence": 0,
            "error": str(e),
        }


async def evaluate_prompt_batch(
    prompt_state: PromptState,
    eval_data: list,
    llm_client: LLMClientBase,
    model: Optional[str] = None,
    temperature: float = 0.1,
    max_tokens: int = 4096,
    on_result: Optional[Callable] = None,
    request_delay: float = 0,
) -> list:
    """Evaluate a prompt on all eval_data queries.

    Args:
        prompt_state: PromptState whose render() produces the template.
        eval_data: List of query dicts with pipeline_data.
        llm_client: LLM client implementing LLMClientBase.
        model: Model identifier (uses client default if None).
        temperature: Sampling temperature.
        max_tokens: Maximum response tokens.
        on_result: Optional callback ``(result, index, total)`` called after
            each query evaluation.
        request_delay: Seconds to sleep between LLM calls (0 = no delay).
            Useful for staying under API rate limits during grid search.

    Returns:
        List of result dicts from local_reranker_eval.
    """
    results = []
    rendered = prompt_state.render()

    for i, qd in enumerate(eval_data):
        if request_delay > 0 and i > 0:
            await asyncio.sleep(request_delay)

        result = await local_reranker_eval(
            rendered, qd, llm_client,
            model=model, temperature=temperature, max_tokens=max_tokens,
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
