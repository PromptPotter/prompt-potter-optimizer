"""
Prompt optimization service.

Generates candidate prompt variants via LLM meta-prompts, selects round
winners, generates improvement suggestions, and saves campaign results.
"""
import json
from datetime import datetime, timezone
from typing import List, Optional

from api.models.prompt_state import PromptState
from api.services.llm_client import LLMClientBase
from api.services.project_store import ProjectStore


async def generate_candidates(
    current_ps: PromptState,
    current_accuracy: float,
    current_results: list,
    n_variants: int,
    creativity: float,
    llm_client: LLMClientBase,
    model: Optional[str] = None,
) -> List[PromptState]:
    """Generate candidate prompt variants via LLM meta-prompt.

    Args:
        current_ps: Current best PromptState.
        current_accuracy: Current accuracy (0.0-1.0).
        current_results: List of result dicts from evaluation.
        n_variants: Number of variants to generate.
        creativity: Temperature for the meta-prompt LLM call.
        llm_client: LLM client implementing LLMClientBase.
        model: Model identifier (uses client default if None).

    Returns:
        List of derived PromptState candidates.
    """
    failures = [r for r in current_results if not r["hit"] and not r.get("error")]
    failure_examples = "\n".join(
        f"  Query: {r['query'][:60]}  |  "
        f"Predicted: {r['predicted'][:40]}  |  "
        f"GT: {r['ground_truth'][:40]}"
        for r in failures[:15]
    )

    rendered_prompt = current_ps.render()

    meta_prompt = (
        f"You are a prompt engineering expert. Generate {n_variants} improved "
        "variants\nof a candidate-ranking prompt used in a terminology "
        "normalization pipeline.\n\n"
        f"CURRENT PROMPT ({current_accuracy:.1%} accuracy on "
        f"{len(current_results)} queries):\n"
        f"---\n{rendered_prompt}\n---\n\n"
        f"FAILURE EXAMPLES (predicted != ground_truth):\n{failure_examples}\n\n"
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
        "Return a JSON object with key \"variants\" containing an array of "
        "objects:\n"
        "  - \"variant_name\": short identifier\n"
        "  - \"changes_description\": 1-2 sentence description of what "
        "changed and why\n"
        "  - \"prompt_text\": full prompt template text"
    )

    response = await llm_client.chat(
        messages=[{"role": "user", "content": meta_prompt}],
        model=model,
        temperature=creativity,
        max_tokens=16000,
        output_format="json",
    )
    generated = response.parsed or json.loads(response.content)

    if isinstance(generated, dict):
        variants_list = generated.get("variants", generated.get("prompts", []))
    else:
        variants_list = generated

    candidates = []
    for v in variants_list[:n_variants]:
        ps = current_ps.derive(
            instruction=v["prompt_text"],
            changes_description=v.get(
                "changes_description", v.get("variant_name", "")
            ),
        )
        candidates.append(ps)

    return candidates


def select_round_winner(
    candidates: list,
    all_candidate_results: dict,
    current_best: dict,
    improvement_threshold: float,
) -> dict:
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
        c_acc = (
            sum(1 for r in c_results if r["hit"]) / len(c_results)
            if c_results
            else 0
        )
        if c_acc > best_acc:
            best_acc = c_acc
            best_ps = candidate
            best_results = c_results
            best_label = candidate.changes_description or candidate.id[:12]

    # Build comparison rows for display
    rows = [
        {
            "prompt": f"current_best ({current_best['label'][:30]})",
            "hit@1": f"{current_acc:.1%}",
            "delta": "-",
        }
    ]
    for candidate in candidates:
        c_results = all_candidate_results[candidate.id]
        c_acc = (
            sum(1 for r in c_results if r["hit"]) / len(c_results)
            if c_results
            else 0
        )
        delta = c_acc - current_acc
        rows.append({
            "prompt": (candidate.changes_description or candidate.id[:12])[:40],
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
    campaign_rounds: list,
    eval_data: list,
    campaign_config: dict,
    llm_client: LLMClientBase,
    model: Optional[str] = None,
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
    for r in failures[:20]:
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
            f"  Query: {r['query'][:60]}\n"
            f"    Predicted: {r['predicted'][:50]}\n"
            f"    Ground truth: {r['ground_truth'][:50]}\n"
            f"    GT in candidates: {gt_in_candidates}\n"
            f"    Top candidates: {candidate_names[:5]}\n"
            f"    Core concept: {profile.get('core_concept', '?')}\n"
        )

    history_lines = []
    for rd in campaign_rounds:
        history_lines.append(
            f"  Round {rd['round']}: {rd['label'][:40]} -> {rd['accuracy']:.1%}"
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
    """Find best round, save to store. Returns save_data dict.

    Args:
        campaign_rounds: List of round dicts from the campaign.
        campaign_config: Campaign configuration dict.
        store: ProjectStore instance.
        backend_id: Backend identifier.

    Returns:
        Dict with winner data including accuracy, improvement, and file path.
    """
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
