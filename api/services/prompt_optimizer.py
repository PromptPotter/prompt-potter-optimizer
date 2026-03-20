"""
Prompt optimization service.

Generates candidate prompt variants via LLM meta-prompts, selects round
winners, generates improvement suggestions, and saves campaign results.
"""

import json
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from api.config.optimizer_prompt_loader import load_optimizer_prompt
from api.models.prompt_state import PromptState
from api.core.llm_call import get_node_config, llm_call
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

    return load_optimizer_prompt("meta_constrained").compile(
        n_variants=n_variants,
        accuracy_pct=f"{current_accuracy:.1%}",
        n_queries=len(current_results),
        rendered_prompt=rendered_prompt,
        failure_examples=failure_examples,
        library_desc=library_desc,
        response_schema=response_schema,
    )


def _build_freeform_meta_prompt(
    n_variants: int,
    rendered_prompt: str,
    current_accuracy: float,
    current_results: list,
    failure_examples: str,
) -> str:
    """Build meta-prompt for open-form prompt generation."""
    return load_optimizer_prompt("meta_freeform").compile(
        n_variants=n_variants,
        accuracy_pct=f"{current_accuracy:.1%}",
        n_queries=len(current_results),
        rendered_prompt=rendered_prompt,
        failure_examples=failure_examples,
    )


def _build_scan_aware_meta_prompt(
    n_variants: int,
    rendered_prompt: str,
    current_accuracy: float,
    current_results: list,
    failure_examples: str,
    scan_context: dict,
    escalation_journal: list[dict] | None = None,
) -> str:
    """Build meta-prompt enriched with scan analytics for pipeline_param optimization."""
    # Always allow instruction modification — scan results are guidance, not a gate
    instruction_spec = (
        "  - \"instruction\": full prompt template text "
        "(keep template variables)\n"
    )
    focus_note = ""

    # Inject degradation data so L1 addresses pipeline instability
    if escalation_journal:
        latest = escalation_journal[-1]
        rate = latest.get("degraded_rate", 0)
        problem_step = latest.get("problem_step", "unknown")
        lines = [
            f"PIPELINE ISSUE: {rate:.0%} of queries degrade at the {problem_step} step.",
            "Address pipeline instability in your candidates.",
        ]
        if len(escalation_journal) > 1:
            lines.append(
                f"Previous {len(escalation_journal)} attempts have not resolved the issue.",
            )
        wtypes = latest.get("warning_types", {})
        if wtypes:
            lines.append(f"Warning breakdown: {wtypes}")
        lines.append("")
        focus_note = "\n".join(lines) + "\n" + focus_note

    return load_optimizer_prompt("meta_scan_aware").compile(
        n_variants=n_variants,
        accuracy_pct=f"{current_accuracy:.1%}",
        n_queries=len(current_results),
        rendered_prompt=rendered_prompt,
        failure_examples=failure_examples,
        leaderboard_text=scan_context["leaderboard_text"],
        sensitivity_text=scan_context["sensitivity_text"],
        difficulty_text=scan_context["difficulty_text"],
        tested_values=scan_context["tested_values"],
        focus_note=focus_note,
        instruction_spec=instruction_spec,
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
    escalation_journal: list[dict] | None = None,
    task_context: dict | None = None,
    warning_inventory: dict | None = None,
    l2_directive: str = "",
    is_probe_round: bool = False,
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
        List of candidate dicts.
    """
    if n_variants <= 0:
        raise ValueError(f"n_variants must be >0, got {n_variants}")

    failures = [r for r in current_results if not r["hit"] and not r.get("error")]
    _fe_lines = []
    for r in failures[:MAX_FAILURES_GENERATE]:
        line = (
            f"  Query: {r['query'][:DISPLAY_TRUNCATE]}  |  "
            f"Predicted: {r['predicted'][:DISPLAY_TRUNCATE]}  |  "
            f"GT: {r['ground_truth'][:DISPLAY_TRUNCATE]}"
        )
        # Annotate with recurring warning history if available
        if warning_inventory:
            entry = warning_inventory.get(r["query"])
            if entry and entry.get("warnings"):
                top_warn = max(entry["warnings"], key=entry["warnings"].get)
                wcount = entry["warnings"][top_warn]
                seen = entry.get("rounds_seen", 0)
                if wcount >= 2:
                    line += f"  [{top_warn} {wcount}/{seen} rounds]"
        _fe_lines.append(line)
    failure_examples = "\n".join(_fe_lines)

    rendered_prompt = current_ps.render()

    if scan_context:
        meta_prompt = _build_scan_aware_meta_prompt(
            n_variants, rendered_prompt, current_accuracy,
            current_results, failure_examples, scan_context,
            escalation_journal=escalation_journal,
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

    # Append structured domain context
    if task_context:
        tc_lines = "\n".join(f"  {k}: {v}" for k, v in task_context.items() if v)
        if tc_lines:
            meta_prompt += (
                f"\n\nTASK CONTEXT (domain understanding — use to guide your changes):\n"
                f"{tc_lines}\n"
            )

    # Probe round note (same prompt, additional injected string)
    if is_probe_round:
        meta_prompt += (
            "\n\nPROBE ROUND: These queries have recurring pipeline warnings. "
            "Generate candidates that specifically address pipeline robustness "
            "for the affected steps.\n"
        )

    # L2 directive (primary guidance from context refinement)
    if l2_directive:
        meta_prompt += (
            "\n\nL2 DIRECTIVE (from context refinement — additional guidance for this round):\n"
            f"{l2_directive}\n"
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

    response = await llm_call(
        llm_client,
        messages=[{"role": "user", "content": meta_prompt}],
        config=get_node_config("l1_generate"),
        model=model,
        temperature=creativity,
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
    pipeline_schema: Any = None,
) -> dict[str, Any]:
    """Compare candidates and select the round winner.

    Args:
        candidates: List of candidate PromptState objects.
        all_candidate_results: Dict mapping candidate.id -> list of result dicts.
        current_best: Dict with keys: accuracy, prompt_state, results, label.
        improvement_threshold: Minimum accuracy improvement to accept a new winner.
        pipeline_schema: Pipeline schema for role-aware composite scoring.

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
        c_scores = compute_composite_score(c_results, pipeline_schema)
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
        c_scores = compute_composite_score(c_results, pipeline_schema)
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

    suggestion_prompt = load_optimizer_prompt("suggestions").compile(
        history_lines="\n".join(history_lines),
        accuracy_pct=f"{current_acc:.1%}",
        rendered_prompt=current_ps.render(),
        campaign_config=json.dumps(campaign_config, indent=2),
        n_failures=len(failures),
        n_queries=len(current_results),
        failure_detail="\n".join(failure_detail),
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
    escalation_checks: list | None = None,
) -> dict[str, Any]:
    """Evaluate candidates and select the round winner.

    This is the core eval+select logic extracted from L1EvaluateNode so it
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

    escalation_signal = None
    ctx.escalation_checks = escalation_checks
    ctx.n_total_candidates = len(ps_candidates)

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
        ctx.candidate_idx = idx
        results, scores, cached = evaluate_prompt_cached(
            sp, eval_data, ctx,
            label=f"candidate_{idx}",
            on_result=_on_result,
        )

        # Check for escalation signal from mid-batch abort
        escalation_signal = scores.pop("escalation_signal", None)

        aborted = bool(escalation_signal) and len(results) < len(eval_data)
        all_candidate_results[c.id] = results
        candidate_scores.append({
            "candidate_id": c.id,
            "accuracy": scores["accuracy"],
            "composite": scores.get("composite", scores["accuracy"]),
            "hits": scores["hits"],
            "total": scores["total"],
            "cached": cached,
            "escalation_aborted": aborted,
            "eval_queries": len(results),
            "expected_queries": len(eval_data),
        })
        if on_candidate_eval:
            scores["escalation_aborted"] = aborted
            scores["eval_queries"] = len(results)
            scores["expected_queries"] = len(eval_data)
            on_candidate_eval(idx, len(ps_candidates), scores)

        if escalation_signal:
            break

    # Assemble current_best with PromptState object for select_round_winner
    cb = dict(current_best)
    if isinstance(cb.get("prompt_state"), dict):
        cb["prompt_state"] = PromptState(**cb["prompt_state"])

    # Only pass non-aborted candidates that were fully evaluated
    evaluated_candidates = [
        c for c in ps_candidates
        if c.id in all_candidate_results
        and not any(
            cs.get("escalation_aborted") and cs["candidate_id"] == c.id
            for cs in candidate_scores
        )
    ]
    winner_entry = _select_round_winner(
        evaluated_candidates, all_candidate_results, cb, improvement_threshold,
        pipeline_schema=ctx.pipeline_schema,
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
        "next_action": "escalate" if escalation_signal else "generate",
        "suggestions": {},
        "candidate_scores": candidate_scores,
        "winner_results": winner_entry.get("results", []),
        "all_eval_results": [
            r for results in all_candidate_results.values()
            for r in results
        ],
        "escalation_signal": escalation_signal,
        "degraded_queries": sum(
            1 for r in winner_entry.get("results", [])
            if (r.get("pipeline_data") or {}).get("diagnostics", {}).get("warnings")
        ),
    }
