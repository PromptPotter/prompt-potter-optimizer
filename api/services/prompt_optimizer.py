"""
Prompt optimization service.

Generates candidate prompt variants via LLM meta-prompts, selects round
winners, generates improvement suggestions, and saves campaign results.
"""

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

from api.models.prompt_state import PromptState
from api.services.llm_client import LLMClientBase
from api.services.project_store import ProjectStore
from api.services.prompt_eval import compute_accuracy, evaluate_prompt_cached

if TYPE_CHECKING:
    from api.services.backend_client import BackendClient

logger = logging.getLogger(__name__)

MAX_FAILURES_GENERATE = 15
MAX_FAILURES_SUGGEST = 20
DISPLAY_TRUNCATE = 60


async def generate_candidates(
    current_ps: PromptState,
    current_accuracy: float,
    current_results: list,
    n_variants: int,
    creativity: float,
    llm_client: LLMClientBase,
    model: str | None = None,
    variant_library: dict | None = None,
) -> list[PromptState]:
    """Generate candidate prompt variants via LLM meta-prompt.

    Args:
        current_ps: Current best PromptState.
        current_accuracy: Current accuracy (0.0-1.0).
        current_results: List of result dicts from evaluation.
        n_variants: Number of variants to generate (must be >0).
        creativity: Temperature for the meta-prompt LLM call.
        llm_client: LLM client implementing LLMClientBase.
        model: Model identifier (uses client default if None).
        variant_library: When provided, constrains non-instruction fields
            to the library values. The LLM selects by index for those
            fields and writes ``instruction`` freely.

    Returns:
        List of derived PromptState candidates.
    """
    if n_variants <= 0:
        raise ValueError(f"n_variants must be >0, got {n_variants}")

    failures = [r for r in current_results if not r["hit"] and not r.get("error")]
    failure_examples = "\n".join(
        f"  Query: {r['query'][:DISPLAY_TRUNCATE]}  |  "
        f"Predicted: {r['predicted'][:DISPLAY_TRUNCATE]}  |  "
        f"GT: {r['ground_truth'][:DISPLAY_TRUNCATE]}"
        for r in failures[:MAX_FAILURES_GENERATE]
    )

    rendered_prompt = current_ps.render()

    # Build constrained or free-form meta-prompt
    if variant_library:
        prompt_fields = variant_library.get("prompt_fields", {})
        constrained_fields = {
            k: v for k, v in prompt_fields.items()
            if k != "instruction" and len(v) > 1
        }

        library_desc = "VARIANT LIBRARY (select by index):\n"
        for field, options in constrained_fields.items():
            library_desc += f"  {field}:\n"
            for i, opt in enumerate(options):
                label = opt[:60] if opt else "(empty)"
                library_desc += f"    [{i}] {label}\n"

        response_schema = (
            "Return a JSON object with key \"variants\" containing an array "
            f"of {n_variants} objects, each with:\n"
            "  - \"variant_name\": short identifier\n"
            "  - \"changes_description\": 1-2 sentence description\n"
            "  - \"instruction\": full prompt template text "
            "(modify freely, keep template variables)\n"
        )
        for field in constrained_fields:
            response_schema += (
                f"  - \"{field}_idx\": integer index into the {field} options\n"
            )

        meta_prompt = (
            f"You are a prompt engineering expert. Generate {n_variants} "
            "improved variants\nof a candidate-ranking prompt used in a "
            "terminology normalization pipeline.\n\n"
            f"CURRENT PROMPT ({current_accuracy:.1%} accuracy on "
            f"{len(current_results)} queries):\n"
            f"---\n{rendered_prompt}\n---\n\n"
            f"FAILURE EXAMPLES (predicted != ground_truth):\n"
            f"{failure_examples}\n\n"
            f"{library_desc}\n"
            "The prompt uses template variables (double-brace syntax):\n"
            "  {{core_concept}} -- core concept from entity profile\n"
            "  {{entity_profile_json}} -- full JSON entity profile\n"
            "  {{matches}} -- newline-separated candidate list\n\n"
            "RULES:\n"
            "- Modify 'instruction' freely (keep template variables)\n"
            "- For other fields, SELECT from the provided options by index\n\n"
            f"{response_schema}"
        )
    else:
        meta_prompt = (
            f"You are a prompt engineering expert. Generate {n_variants} "
            "improved variants\nof a candidate-ranking prompt used in a "
            "terminology normalization pipeline.\n\n"
            f"CURRENT PROMPT ({current_accuracy:.1%} accuracy on "
            f"{len(current_results)} queries):\n"
            f"---\n{rendered_prompt}\n---\n\n"
            f"FAILURE EXAMPLES (predicted != ground_truth):\n"
            f"{failure_examples}\n\n"
            "The prompt uses template variables (double-brace syntax):\n"
            "  {{core_concept}} -- core concept from entity profile\n"
            "  {{entity_profile_json}} -- full JSON entity profile from web "
            "research\n"
            "  {{matches}} -- newline-separated list of \"- candidate_term\" "
            "from token matching\n\n"
            "For each variant:\n"
            "1. Analyze WHY the current prompt fails on the examples above\n"
            "2. Make targeted changes to improve ranking accuracy "
            "(get correct candidate to rank #1)\n"
            "3. Keep the same template variables and JSON output format\n\n"
            "Return a JSON object with key \"variants\" containing an array "
            "of objects:\n"
            "  - \"variant_name\": short identifier\n"
            "  - \"changes_description\": 1-2 sentence description of what "
            "changed and why\n"
            "  - \"prompt_text\": full prompt template text"
        )

    response = await llm_client.chat(
        messages=[{"role": "user", "content": meta_prompt}],
        model=model,
        temperature=creativity,
        max_tokens=8192,
        output_format="json",
    )
    generated = response.parsed or json.loads(response.content)

    if isinstance(generated, dict):
        variants_list = generated.get("variants", generated.get("prompts", []))
    else:
        variants_list = generated

    candidates = []
    for v in variants_list[:n_variants]:
        if variant_library:
            # Map indices back to library values
            changes: dict[str, Any] = {
                "instruction": v.get("instruction", v.get("prompt_text", "")),
            }
            prompt_fields = variant_library.get("prompt_fields", {})
            for field, options in prompt_fields.items():
                if field == "instruction" or len(options) <= 1:
                    continue
                idx_key = f"{field}_idx"
                if idx_key in v:
                    idx = int(v[idx_key])
                    if 0 <= idx < len(options):
                        changes[field] = options[idx]

            ps = current_ps.derive(
                **changes,
                changes_description=v.get(
                    "changes_description", v.get("variant_name", ""),
                ),
            )
        else:
            ps = current_ps.derive(
                instruction=v["prompt_text"],
                changes_description=v.get(
                    "changes_description", v.get("variant_name", ""),
                ),
            )
        candidates.append(ps)

    return candidates


def select_round_winner(
    candidates: list[PromptState],
    all_candidate_results: dict[str, list[dict]],
    current_best: dict[str, Any],
    improvement_threshold: float,
) -> dict[str, Any]:
    """Compare candidates and select the round winner.

    Args:
        candidates: List of candidate PromptState objects.
        all_candidate_results: Dict mapping candidate.id -> list of result dicts.
        current_best: Dict with keys: accuracy, prompt_state, results, label.
        improvement_threshold: Minimum accuracy improvement to accept a new winner.

    Returns:
        Dict with keys: label, prompt_state, accuracy, hits, total, results,
        candidates_evaluated, comparison_rows, improved.
    """
    current_acc = current_best["accuracy"]
    current_ps = current_best["prompt_state"]
    current_results = current_best["results"]

    best_acc = current_acc
    best_ps = current_ps
    best_results = current_results
    best_label = current_best["label"]

    for candidate in candidates:
        c_results = all_candidate_results[candidate.id]
        c_acc = compute_accuracy(c_results)["accuracy"]
        if c_acc > best_acc:
            best_acc = c_acc
            best_ps = candidate
            best_results = c_results
            best_label = candidate.changes_description or candidate.id[:12]

    rows = [
        {
            "prompt": f"current_best ({current_best['label'][:30]})",
            "hit@1": f"{current_acc:.1%}",
            "delta": "-",
        }
    ]
    for candidate in candidates:
        c_results = all_candidate_results[candidate.id]
        c_acc = compute_accuracy(c_results)["accuracy"]
        delta = c_acc - current_acc
        rows.append({
            "prompt": (
                candidate.changes_description or candidate.id[:12]
            )[:DISPLAY_TRUNCATE],
            "hit@1": f"{c_acc:.1%}",
            "delta": f"{delta:+.1%}",
        })

    improved = best_acc > current_acc + improvement_threshold

    return {
        "label": best_label,
        "prompt_state": best_ps,
        "accuracy": best_acc,
        "hits": sum(1 for r in best_results if r["hit"]),
        "total": len(best_results),
        "results": best_results,
        "candidates_evaluated": len(candidates),
        "comparison_rows": rows,
        "improved": improved,
    }


async def generate_suggestions(
    campaign_rounds: list[dict],
    eval_data: list[dict],
    campaign_config: dict[str, Any],
    llm_client: LLMClientBase,
    model: str | None = None,
) -> dict:
    """Generate improvement suggestions via LLM analysis.

    Args:
        campaign_rounds: List of round dicts from the campaign.
        eval_data: Evaluation data dicts with pipeline_data.
        campaign_config: Current campaign configuration dict.
        llm_client: LLM client implementing LLMClientBase.
        model: Model identifier (uses client default if None).

    Returns:
        Dict with keys: failure_patterns, parameter_suggestions,
        prompt_phrase_fragments, suggested_config, summary.
    """
    current_best = campaign_rounds[-1]
    current_ps = current_best["prompt_state"]
    current_results = current_best["results"]
    current_acc = current_best["accuracy"]

    failures = [r for r in current_results if not r["hit"] and not r.get("error")]
    failure_detail = []
    for r in failures[:MAX_FAILURES_SUGGEST]:
        pd_data = next(
            (
                rd.get("pipeline_data", {})
                for rd in eval_data
                if rd["query"] == r["query"]
            ),
            {},
        )
        candidates = pd_data.get("token_matched_candidates", [])
        candidate_names = [
            c[0] if isinstance(c, (list, tuple)) else str(c)
            for c in candidates[:10]
        ]
        gt_in_candidates = r["ground_truth"] in candidate_names
        profile = pd_data.get("entity_profile", {})

        failure_detail.append(
            f"  Query: {r['query'][:DISPLAY_TRUNCATE]}\n"
            f"    Predicted: {r['predicted'][:DISPLAY_TRUNCATE]}\n"
            f"    Ground truth: {r['ground_truth'][:DISPLAY_TRUNCATE]}\n"
            f"    GT in candidates: {gt_in_candidates}\n"
            f"    Top candidates: {candidate_names[:5]}\n"
            f"    Core concept: {profile.get('core_concept', '?')}\n"
        )

    history_lines = []
    for rd in campaign_rounds:
        history_lines.append(
            f"  Round {rd['round']}: {rd['label'][:DISPLAY_TRUNCATE]} -> {rd['accuracy']:.1%}"
        )

    suggestion_prompt = (
        "You are an expert optimization advisor for a terminology "
        "normalization pipeline.\n"
        "Analyze the current campaign state and provide actionable "
        "suggestions for the next round.\n\n"
        f"CAMPAIGN HISTORY:\n{chr(10).join(history_lines)}\n\n"
        f"CURRENT BEST PROMPT ({current_acc:.1%} accuracy):\n"
        f"---\n{current_ps.render()}\n---\n\n"
        f"CURRENT CONFIG:\n{json.dumps(campaign_config, indent=2)}\n\n"
        f"FAILURE DETAILS ({len(failures)} failures out of "
        f"{len(current_results)} queries):\n"
        f"{chr(10).join(failure_detail)}\n\n"
        "Provide your analysis as a JSON object with these keys:\n\n"
        '1. "failure_patterns": array of objects, each with:\n'
        '   - "category": failure type (e.g., "bad_profile", '
        '"candidate_absent", "reranker_misjudged", "ambiguous_query")\n'
        '   - "count": estimated count\n'
        '   - "description": explanation\n'
        '   - "examples": array of query strings\n\n'
        '2. "parameter_suggestions": array of objects, each with:\n'
        '   - "parameter": the pipeline_params key to change\n'
        '   - "current_value": current value\n'
        '   - "suggested_value": new value\n'
        '   - "rationale": why this change helps\n\n'
        '3. "prompt_phrase_fragments": array of objects, each with:\n'
        '   - "action": one of "add_to_instruction", '
        '"modify_thinking_style", "add_few_shot", '
        '"modify_answer_format", "modify_persona"\n'
        '   - "text": the exact text snippet to add or use\n'
        '   - "rationale": why this helps with the observed failures\n\n'
        '4. "suggested_config": a complete campaign_config JSON object '
        "with your recommended changes applied\n\n"
        '5. "summary": 2-3 sentence overview of what to try next'
    )

    response = await llm_client.chat(
        messages=[{"role": "user", "content": suggestion_prompt}],
        model=model,
        temperature=0,
        max_tokens=8000,
        output_format="json",
    )
    return response.parsed or json.loads(response.content)


def save_campaign_winner(
    campaign_rounds: list,
    campaign_config: dict,
    store: ProjectStore,
    backend_id: str,
) -> dict:
    """Find best round, save to store. Returns save_data dict."""
    winner = campaign_rounds[-1]["prompt_state"]
    winner_acc = campaign_rounds[-1]["accuracy"]

    for rd in campaign_rounds:
        if rd["accuracy"] > winner_acc:
            winner = rd["prompt_state"]
            winner_acc = rd["accuracy"]

    save_data = {
        "winner": winner.model_dump(),
        "accuracy": winner_acc,
        "campaign_rounds": len(campaign_rounds),
        "baseline_accuracy": campaign_rounds[0]["accuracy"],
        "improvement": winner_acc - campaign_rounds[0]["accuracy"],
        "config": campaign_config,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }

    filename = f"optimization/campaign_winner_{winner.id[:12]}.json"
    store.save_sync(backend_id, filename, save_data)

    return {
        **save_data,
        "winner_id": winner.id,
        "filename": filename,
        "backend_id": backend_id,
    }


async def run_manual_round(
    campaign_rounds: list,
    eval_data: list,
    backend_client: "BackendClient",
    llm_client: LLMClientBase,
    *,
    model: str = "",
    temperature: float = 0.0,
    n_variants: int = 5,
    creativity: float = 0.7,
    improvement_threshold: float = 0.01,
    pipeline_params: dict | None = None,
    store: "ProjectStore | None" = None,
    backend_id: str = "",
    queries_per_eval: int = 0,
    seed: int = 42,
    variant_library: dict | None = None,
    on_candidate_evaluated: Callable | None = None,
) -> dict:
    """Run a single optimization round (generate, evaluate, select).

    Args:
        campaign_rounds: Existing rounds list (modified in place).
        eval_data: Full evaluation dataset.
        backend_client: BackendClient for evaluation.
        llm_client: LLM client for candidate generation.
        model: Model identifier.
        temperature: Temperature for content hash.
        n_variants: Number of candidate variants to generate.
        creativity: Temperature for meta-prompt LLM call.
        improvement_threshold: Minimum accuracy improvement to accept.
        pipeline_params: Optional pipeline parameter overrides.
        store: Optional ProjectStore for caching.
        backend_id: Backend identifier.
        queries_per_eval: Subsample size (0 = use all).
        seed: Random seed for subsampling.
        variant_library: Optional variant library for constrained generation.
        on_candidate_evaluated: Optional callback
            ``(candidate, idx, results, scores, cached)`` after each eval.

    Returns:
        Round entry dict (also appended to campaign_rounds).
    """
    import random as _random

    current_best = campaign_rounds[-1]
    round_num = len(campaign_rounds)

    if queries_per_eval > 0 and len(eval_data) > queries_per_eval:
        rng = _random.Random(seed)
        round_eval_data = rng.sample(eval_data, queries_per_eval)
    else:
        round_eval_data = eval_data

    candidates = await generate_candidates(
        current_best["prompt_state"],
        current_best["accuracy"],
        current_best["results"],
        n_variants, creativity, llm_client,
        model=model,
        variant_library=variant_library,
    )

    all_candidate_results: dict[str, list] = {}
    for idx, c in enumerate(candidates):
        results, scores, cached = await evaluate_prompt_cached(
            c, round_eval_data, backend_client,
            pipeline_params=pipeline_params,
            store=store, backend_id=backend_id,
            label=f"Candidate {idx + 1}",
            model=model, temperature=temperature,
        )
        all_candidate_results[c.id] = results
        if on_candidate_evaluated:
            on_candidate_evaluated(c, idx, results, scores, cached)

    round_entry = select_round_winner(
        candidates, all_candidate_results, current_best,
        improvement_threshold,
    )
    round_entry["round"] = round_num
    campaign_rounds.append(round_entry)

    return round_entry


async def run_optimization_loop(
    campaign_rounds: list,
    eval_data: list,
    backend_client: "BackendClient",
    llm_client: LLMClientBase,
    *,
    model: str = "",
    temperature: float = 0.0,
    n_variants: int = 5,
    creativity: float = 0.7,
    improvement_threshold: float = 0.01,
    pipeline_params: dict | None = None,
    store: "ProjectStore | None" = None,
    backend_id: str = "",
    max_rounds: int = 10,
    patience: int = 3,
    queries_per_eval: int = 0,
    seed: int = 42,
    variant_library: dict | None = None,
    on_round_complete: Callable | None = None,
) -> list:
    """Run optimization rounds until patience exhausted or max_rounds reached.

    Args:
        campaign_rounds: Existing rounds list (modified in place and returned).
        eval_data: Full evaluation dataset.
        backend_client: BackendClient for evaluation.
        llm_client: LLM client for candidate generation.
        model: Model identifier.
        temperature: Temperature for content hash.
        n_variants: Number of candidate variants per round.
        creativity: Temperature for meta-prompt LLM call.
        improvement_threshold: Minimum accuracy improvement to accept.
        pipeline_params: Optional pipeline parameter overrides.
        store: Optional ProjectStore for caching.
        backend_id: Backend identifier.
        max_rounds: Hard cap on optimization rounds.
        patience: Rounds without improvement before auto-stop.
        queries_per_eval: Subsample size (0 = use all).
        seed: Random seed for subsampling.
        variant_library: Optional variant library for constrained generation.
        on_round_complete: Optional callback
            ``(round_entry, round_num, improved, rounds_without_improvement)``
            after each round.

    Returns:
        Updated campaign_rounds list.
    """
    import random as _random

    if queries_per_eval > 0 and len(eval_data) > queries_per_eval:
        rng = _random.Random(seed)
        round_eval_data = rng.sample(eval_data, queries_per_eval)
    else:
        round_eval_data = eval_data

    rounds_without_improvement = 0

    for _ in range(max_rounds):
        current_best = campaign_rounds[-1]
        round_num = len(campaign_rounds)

        candidates = await generate_candidates(
            current_best["prompt_state"],
            current_best["accuracy"],
            current_best["results"],
            n_variants, creativity, llm_client,
            model=model,
            variant_library=variant_library,
        )

        all_candidate_results: dict[str, list] = {}
        for idx, c in enumerate(candidates):
            results, scores, cached = await evaluate_prompt_cached(
                c, round_eval_data, backend_client,
                pipeline_params=pipeline_params,
                store=store, backend_id=backend_id,
                label=f"Candidate {idx + 1}",
                model=model, temperature=temperature,
            )
            all_candidate_results[c.id] = results

        round_entry = select_round_winner(
            candidates, all_candidate_results, current_best,
            improvement_threshold,
        )
        round_entry["round"] = round_num
        campaign_rounds.append(round_entry)

        improved = (
            round_entry["accuracy"] > current_best["accuracy"] + improvement_threshold
        )
        if improved:
            rounds_without_improvement = 0
        else:
            rounds_without_improvement += 1

        if on_round_complete:
            on_round_complete(
                round_entry, round_num, improved, rounds_without_improvement,
            )

        if rounds_without_improvement >= patience:
            logger.info(
                "Stopping: no improvement for %d consecutive rounds.", patience,
            )
            break

    return campaign_rounds
