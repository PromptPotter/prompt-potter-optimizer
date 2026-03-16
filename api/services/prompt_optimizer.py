"""
Prompt optimization service.

Generates candidate prompt variants via LLM meta-prompts, selects round
winners, generates improvement suggestions, and saves campaign results.
"""

import json
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from api.models.prompt_state import PromptState
from api.services.llm_client import LLMClientBase
from api.services.prompt_eval import compute_composite_score

if TYPE_CHECKING:
    from api.services.prompt_eval import EvalContext

logger = logging.getLogger(__name__)

MAX_FAILURES_GENERATE = 15
MAX_FAILURES_SUGGEST = 20
DISPLAY_TRUNCATE = 60


def _build_constrained_meta_prompt(
    n_variants: int,
    rendered_prompt: str,
    current_accuracy: float,
    current_results: list,
    failure_examples: str,
    variant_library: dict,
) -> str:
    """Build meta-prompt that constrains non-instruction fields to library values."""
    prompt_fields = variant_library.get("prompt_fields", {})
    constrained_fields = {
        k: v for k, v in prompt_fields.items()
        if k != "instruction" and len(v) > 1
    }

    library_desc = "VARIANT LIBRARY (select by index):\n"
    for field_name, options in constrained_fields.items():
        library_desc += f"  {field_name}:\n"
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
    for field_name in constrained_fields:
        response_schema += (
            f"  - \"{field_name}_idx\": integer index into the {field_name} options\n"
        )

    return (
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


def _build_freeform_meta_prompt(
    n_variants: int,
    rendered_prompt: str,
    current_accuracy: float,
    current_results: list,
    failure_examples: str,
) -> str:
    """Build meta-prompt for open-form prompt generation."""
    return (
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


def _build_scan_aware_meta_prompt(
    n_variants: int,
    rendered_prompt: str,
    current_accuracy: float,
    current_results: list,
    failure_examples: str,
    scan_context: dict,
) -> str:
    """Build meta-prompt enriched with scan analytics for pipeline_param optimization."""
    prompt_relevant = scan_context.get("has_prompt_axes", True)

    if prompt_relevant:
        instruction_spec = (
            "  - \"instruction\": full prompt template text "
            "(keep template variables)\n"
        )
        focus_note = ""
    else:
        instruction_spec = (
            "  - \"instruction\": null  (keep unchanged — the ranking prompt "
            "is NOT active in this pipeline)\n"
        )
        focus_note = (
            "IMPORTANT: The ranking prompt is NOT active in this pipeline "
            "configuration. All improving axes are pipeline parameters. "
            "Focus entirely on pipeline_params_override — do NOT modify "
            "the instruction.\n\n"
        )

    return (
        f"You are a pipeline optimization expert. Generate {n_variants} "
        "candidate configurations\nfor a terminology normalization pipeline.\n\n"
        f"CURRENT STATE ({current_accuracy:.1%} accuracy on "
        f"{len(current_results)} queries):\n"
        f"---\n{rendered_prompt}\n---\n\n"
        f"FAILURE EXAMPLES:\n{failure_examples}\n\n"
        "## SCAN ANALYTICS (sensitivity scan results)\n\n"
        "### Variant leaderboard (ranked by accuracy)\n"
        f"{scan_context['leaderboard_text']}\n\n"
        "### Axis sensitivity (most impactful parameters)\n"
        f"{scan_context['sensitivity_text']}\n\n"
        "### Query difficulty\n"
        f"{scan_context['difficulty_text']}\n\n"
        "### Tested values per axis\n"
        f"{scan_context['tested_values']}\n\n"
        "## INSTRUCTIONS\n"
        f"{focus_note}"
        f"Generate {n_variants} candidate configurations. For each candidate:\n"
        "1. Choose a pipeline_params combination informed by the scan data above\n"
        "2. Optionally propose NEW values for sensitive axes (values not yet tested)\n"
        "3. Explain your reasoning\n\n"
        "Prioritize axes with high sensitivity and positive best_delta.\n"
        "Avoid re-testing exact value combinations from the leaderboard.\n"
        "For numeric params: explore between or beyond tested ranges.\n"
        "For string params: try semantic variations.\n\n"
        "Return a JSON object with key \"variants\" containing an array of "
        f"{n_variants} objects, each with:\n"
        "  - \"variant_name\": short identifier\n"
        "  - \"changes_description\": 1-2 sentence description\n"
        f"{instruction_spec}"
        "  - \"pipeline_params_override\": dict of param_name -> value "
        "(only include params you want to change)\n"
        "  - \"reasoning\": why this combination is promising\n"
    )


async def generate_candidates(
    current_ps: PromptState,
    current_accuracy: float,
    current_results: list,
    n_variants: int,
    creativity: float,
    llm_client: LLMClientBase,
    model: str | None = None,
    variant_library: dict | None = None,
    scan_context: dict | None = None,
    critique_text: str = "",
    thinking_styles: list[str] | None = None,
) -> list[dict]:
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
        scan_context: When provided, enriches the meta-prompt with scan
            analytics (leaderboard, axis sensitivity, tested values) and
            enables per-candidate ``pipeline_params_override`` in the
            response. Built by ``prepare_scan_context()``.
        critique_text: Structured critique from previous round's evaluation.
        thinking_styles: Sampled thinking styles for mutation guidance.

    Returns:
        List of candidate dicts. Each dict contains serialized PromptState
        fields, plus an optional ``__pipeline_params_override__`` key when
        scan_context is provided.
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

    if scan_context:
        meta_prompt = _build_scan_aware_meta_prompt(
            n_variants, rendered_prompt, current_accuracy,
            current_results, failure_examples, scan_context,
        )
    elif variant_library:
        meta_prompt = _build_constrained_meta_prompt(
            n_variants, rendered_prompt, current_accuracy,
            current_results, failure_examples, variant_library,
        )
    else:
        meta_prompt = _build_freeform_meta_prompt(
            n_variants, rendered_prompt, current_accuracy,
            current_results, failure_examples,
        )

    # Append critique from previous round's evaluation
    if critique_text:
        meta_prompt += (
            f"\n\nCRITIQUE (from previous evaluation — use this to guide your changes):\n"
            f"{critique_text}\n"
        )

    # Append thinking style guidance
    if thinking_styles:
        styles_text = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(thinking_styles))
        meta_prompt += (
            f"\n\nTHINKING STYLES (consider these approaches when generating variants):\n"
            f"{styles_text}\n"
        )

    # Append strategic plan guidance when available
    if current_ps.plan:
        meta_prompt += (
            f"\n\nSTRATEGIC GUIDANCE (from optimization plan):\n"
            f"{current_ps.plan}\n"
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

    candidates: list[dict] = []
    for v in variants_list[:n_variants]:
        if variant_library and not scan_context:
            changes: dict[str, Any] = {
                "instruction": v.get("instruction", v.get("prompt_text", "")),
            }
            prompt_fields = variant_library.get("prompt_fields", {})
            for field_name, options in prompt_fields.items():
                if field_name == "instruction" or len(options) <= 1:
                    continue
                idx_key = f"{field_name}_idx"
                if idx_key in v:
                    idx = int(v[idx_key])
                    if 0 <= idx < len(options):
                        changes[field_name] = options[idx]

            ps = current_ps.derive(
                **changes,
                changes_description=v.get(
                    "changes_description", v.get("variant_name", ""),
                ),
            )
        else:
            instr = v.get("instruction", v.get("prompt_text", ""))
            ps = current_ps.derive(
                **({"instruction": instr} if instr else {}),
                changes_description=v.get(
                    "changes_description", v.get("variant_name", ""),
                ),
            )

        c_dict = ps.model_dump()

        # Attach per-candidate pipeline_params override when scan-aware
        if scan_context:
            pp_override = v.get("pipeline_params_override")
            if pp_override and isinstance(pp_override, dict):
                c_dict["__pipeline_params_override__"] = pp_override

        candidates.append(c_dict)

    return candidates


def _select_round_winner(
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
    current_composite = current_best.get("composite", current_acc)
    current_ps = current_best["prompt_state"]
    current_results = current_best["results"]

    best_composite = current_composite
    best_acc = current_acc
    best_ps = current_ps
    best_results = current_results
    best_label = current_best["label"]
    winner_idx: int | None = None  # None = current_best is still the winner

    for idx, candidate in enumerate(candidates):
        c_results = all_candidate_results[candidate.id]
        c_scores = compute_composite_score(c_results)
        c_composite = c_scores["composite"]
        if c_composite > best_composite:
            best_composite = c_composite
            best_acc = c_scores["accuracy"]
            best_ps = candidate
            best_results = c_results
            best_label = candidate.changes_description or candidate.id[:12]
            winner_idx = idx

    rows = [
        {
            "prompt": f"current_best ({current_best['label'][:30]})",
            "hit@1": f"{current_acc:.1%}",
            "composite": f"{current_composite:.4f}",
            "delta": "-",
        }
    ]
    for candidate in candidates:
        c_results = all_candidate_results[candidate.id]
        c_scores = compute_composite_score(c_results)
        c_composite = c_scores["composite"]
        delta = c_composite - current_composite
        rows.append({
            "prompt": (
                candidate.changes_description or candidate.id[:12]
            )[:DISPLAY_TRUNCATE],
            "hit@1": f"{c_scores['accuracy']:.1%}",
            "composite": f"{c_composite:.4f}",
            "delta": f"{delta:+.4f}",
        })

    improved = best_composite > current_composite + improvement_threshold

    return {
        "label": best_label,
        "prompt_state": best_ps,
        "accuracy": best_acc,
        "composite": best_composite,
        "hits": sum(1 for r in best_results if r["hit"]),
        "total": len(best_results),
        "results": best_results,
        "candidates_evaluated": len(candidates),
        "comparison_rows": rows,
        "improved": improved,
        "winner_idx": winner_idx,
    }


async def generate_suggestions(
    campaign_rounds: list[dict],
    eval_data: list[dict],
    campaign_config: dict[str, Any],
    llm_client: LLMClientBase,
    model: str | None = None,
    suggestion_temperature: float = 0.0,
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
        temperature=suggestion_temperature,
        max_tokens=8000,
        output_format="json",
    )
    return response.parsed or json.loads(response.content)


async def evaluate_and_select_winner(
    candidates: list[dict],
    eval_data: list,
    current_best: dict[str, Any],
    ctx: "EvalContext",
    *,
    improvement_threshold: float = 0.01,
    on_candidate_eval: Callable[[int, int, dict], None] | None = None,
    on_query_eval: Callable[[int, int, int, int, dict], None] | None = None,
) -> dict[str, Any]:
    """Evaluate candidates and select the round winner.

    This is the core eval+select logic extracted from AnalysisEvalNode so it
    can be called directly by the feedback cycle without node overhead.

    Returns:
        Dict with keys: winner, winner_prompt_state, winner_accuracy,
        improved, next_action, suggestions, candidate_scores, winner_results.
    """
    from api.models.search_point import SearchPoint
    from api.services.prompt_eval import evaluate_prompt_cached

    # Extract model/temp/pp from ctx for per-candidate SearchPoints
    _sp_model = ctx.model
    _sp_temperature = ctx.temperature
    _sp_pipeline_params = ctx.pipeline_params

    # Extract per-candidate pipeline_params overrides without mutating caller's dicts
    candidate_pp: list[dict | None] = []
    clean_candidates: list[dict] = []
    for c in candidates:
        if isinstance(c, dict):
            candidate_pp.append(c.get("__pipeline_params_override__"))
            clean_candidates.append(
                {k: v for k, v in c.items() if k != "__pipeline_params_override__"},
            )
        else:
            candidate_pp.append(None)
            clean_candidates.append(c.model_dump() if hasattr(c, "model_dump") else c)

    ps_candidates = [PromptState(**c) for c in clean_candidates]
    all_candidate_results: dict[str, list[dict]] = {}
    candidate_scores: list[dict] = []

    for idx, c in enumerate(ps_candidates):
        _on_result = None
        if on_query_eval:
            def _on_result(result, qi, qt, _ci=idx, _ct=len(ps_candidates)):
                on_query_eval(_ci, _ct, qi, qt, result)

        if candidate_pp[idx]:
            pp = {**(_sp_pipeline_params or {}), **candidate_pp[idx]}
        else:
            pp = _sp_pipeline_params
        sp = SearchPoint(
            prompt_state=c,
            model=_sp_model,
            temperature=_sp_temperature,
            pipeline_params=pp,
        )
        results, scores, cached = await evaluate_prompt_cached(
            sp, eval_data, ctx,
            label=f"candidate_{idx}",
            on_result=_on_result,
        )
        all_candidate_results[c.id] = results
        candidate_scores.append({
            "candidate_id": c.id,
            "accuracy": scores["accuracy"],
            "composite": scores.get("composite", scores["accuracy"]),
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

    winner_entry = _select_round_winner(
        ps_candidates, all_candidate_results, cb, improvement_threshold,
    )

    # Resolve the winner's pipeline_params: if a candidate won, merge its
    # override with the base; if current_best won, keep the base as-is.
    w_idx = winner_entry["winner_idx"]
    if w_idx is not None and candidate_pp[w_idx]:
        winner_pp = {**(_sp_pipeline_params or {}), **candidate_pp[w_idx]}
    else:
        winner_pp = _sp_pipeline_params

    winner_ps = winner_entry["prompt_state"]
    return {
        "winner": {
            "label": winner_entry["label"],
            "accuracy": winner_entry["accuracy"],
            "composite": winner_entry.get("composite", winner_entry["accuracy"]),
            "hits": winner_entry["hits"],
            "total": winner_entry["total"],
            "improved": winner_entry["improved"],
            "candidates_evaluated": winner_entry["candidates_evaluated"],
        },
        "winner_prompt_state": winner_ps.model_dump(),
        "winner_pipeline_params": winner_pp,
        "winner_accuracy": winner_entry["accuracy"],
        "winner_composite": winner_entry.get("composite", winner_entry["accuracy"]),
        "improved": winner_entry["improved"],
        "next_action": "generate",
        "suggestions": {},
        "candidate_scores": candidate_scores,
        "winner_results": winner_entry.get("results", []),
    }
