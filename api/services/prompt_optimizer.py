"""
Prompt optimization service.

Generates candidate prompt variants via LLM meta-prompts, selects round
winners, generates improvement suggestions, and saves campaign results.
"""

import json
import logging
from collections.abc import Callable
from typing import Any

from api.models.prompt_state import PromptState
from api.services.llm_client import LLMClientBase
from api.services.prompt_eval import compute_accuracy

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
        f"CAMPAIGN HISTORY:\n{"\n".join(history_lines)}\n\n"
        f"CURRENT BEST PROMPT ({current_acc:.1%} accuracy):\n"
        f"---\n{current_ps.render()}\n---\n\n"
        f"CURRENT CONFIG:\n{json.dumps(campaign_config, indent=2)}\n\n"
        f"FAILURE DETAILS ({len(failures)} failures out of "
        f"{len(current_results)} queries):\n"
        f"{"\n".join(failure_detail)}\n\n"
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


async def evaluate_and_select_winner(
    candidates: list[dict],
    eval_data: list,
    current_best: dict[str, Any],
    *,
    backend_url: str,
    backend_id: str = "",
    project_root: str = "",
    improvement_threshold: float = 0.01,
    pipeline_params: dict | None = None,
    model: str | None = None,
    temperature: float = 0.0,
    do_suggestions: bool = False,
    campaign_rounds: list[dict] | None = None,
    campaign_config: dict | None = None,
    on_candidate_eval: Callable | None = None,
    on_query_eval: Callable | None = None,
    obs: Any = None,
    dataset_name: str | None = None,
    dataset_item_map: dict[str, str] | None = None,
    provider: str | None = None,
) -> dict[str, Any]:
    """Evaluate candidates, select winner, optionally generate suggestions.

    This is the core eval+select logic extracted from AnalysisEvalNode so it
    can be called directly by the feedback cycle without node overhead.

    Returns:
        Dict with keys: winner, winner_prompt_state, winner_accuracy,
        improved, next_action, suggestions, candidate_scores, winner_results.
    """
    from api.services.backend_client import BackendClient
    from api.services.llm_client import get_llm_client
    from api.services.prompt_eval import evaluate_prompt_cached

    backend_client = BackendClient(backend_url)

    store = None
    if project_root:
        from api.services.project_store import ProjectStore
        store = ProjectStore(project_root)

    ps_candidates = [PromptState(**c) for c in candidates]
    all_candidate_results: dict[str, list[dict]] = {}
    candidate_scores: list[dict] = []

    for idx, c in enumerate(ps_candidates):
        _on_result = None
        if on_query_eval:
            def _on_result(result, qi, qt, _ci=idx, _ct=len(ps_candidates)):
                on_query_eval(_ci, _ct, qi, qt, result)

        results, scores, cached = await evaluate_prompt_cached(
            c, eval_data, backend_client,
            pipeline_params=pipeline_params,
            store=store, backend_id=backend_id,
            label=f"candidate_{idx}",
            model=model or "", temperature=temperature,
            on_result=_on_result,
            dataset_name=dataset_name,
            dataset_item_map=dataset_item_map,
            obs=obs,
        )
        all_candidate_results[c.id] = results
        candidate_scores.append({
            "candidate_id": c.id,
            "accuracy": scores["accuracy"],
            "hits": scores["hits"],
            "total": scores["total"],
            "cached": cached,
        })
        if on_candidate_eval:
            on_candidate_eval(idx, len(ps_candidates), scores)

    # Assemble current_best with PromptState object for select_round_winner
    cb = dict(current_best)
    if isinstance(cb.get("prompt_state"), dict):
        cb["prompt_state"] = PromptState(**cb["prompt_state"])

    winner_entry = select_round_winner(
        ps_candidates, all_candidate_results, cb, improvement_threshold,
    )

    # Generate suggestions if requested
    suggestions: dict = {}
    if do_suggestions and campaign_rounds:
        llm_client = get_llm_client(provider)
        suggestions = await generate_suggestions(
            campaign_rounds, eval_data, campaign_config or {},
            llm_client, model=model,
        )

    next_action = suggestions.get("next_action", "generate")
    if next_action not in ("generate", "refine_context", "modify_plan", "stop"):
        next_action = "generate"

    winner_ps = winner_entry["prompt_state"]
    return {
        "winner": {
            "label": winner_entry["label"],
            "accuracy": winner_entry["accuracy"],
            "hits": winner_entry["hits"],
            "total": winner_entry["total"],
            "improved": winner_entry["improved"],
            "candidates_evaluated": winner_entry["candidates_evaluated"],
        },
        "winner_prompt_state": winner_ps.model_dump(),
        "winner_accuracy": winner_entry["accuracy"],
        "improved": winner_entry["improved"],
        "next_action": next_action,
        "suggestions": suggestions,
        "candidate_scores": candidate_scores,
        "winner_results": winner_entry.get("results", []),
    }
