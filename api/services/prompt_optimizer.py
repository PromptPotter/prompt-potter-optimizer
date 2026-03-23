"""
Prompt optimization service.

L1 Generate and L1 Evaluate services for the optimizer pipeline, plus
improvement suggestion generation.
"""

import json
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from api.config.optimizer_prompt_loader import load_optimizer_prompt
from api.services.constants import DISPLAY_TRUNCATE
from api.models.prompt_state import PromptState
from api.core.llm_call import get_node_config, llm_call
from api.services.campaign.critique_stats import summarize_warning_inventory
from api.services.llm_client import LLMClientBase
from api.services.prompt_eval import compute_composite_score

if TYPE_CHECKING:
    from api.services.prompt_eval import EvalContext

logger = logging.getLogger(__name__)

MAX_FAILURES_GENERATE = 15


# ---------------------------------------------------------------------------
# Formatting helpers for l1_generate template variables
# ---------------------------------------------------------------------------


def _format_failure_examples(
    current_results: list,
    warning_inventory: dict | None,
    is_probe_round: bool,
) -> str:
    """Format failure examples with optional warning annotations."""
    failures = [r for r in current_results if not r["hit"] and not r.get("error")]
    lines = []
    for r in failures[:MAX_FAILURES_GENERATE]:
        line = (
            f"  Query: {r['query'][:DISPLAY_TRUNCATE]}  |  "
            f"Predicted: {r['predicted'][:DISPLAY_TRUNCATE]}  |  "
            f"GT: {r['ground_truth'][:DISPLAY_TRUNCATE]}"
        )
        if warning_inventory:
            entry = warning_inventory.get(r["query"])
            if entry and entry.get("warnings"):
                top_warn = max(entry["warnings"], key=entry["warnings"].get)
                wcount = entry["warnings"][top_warn]
                seen = entry.get("rounds_seen", 0)
                threshold = 1 if is_probe_round else 2
                if wcount >= threshold:
                    line += f"  [{top_warn} {wcount}/{seen} rounds]"
        lines.append(line)
    return "\n".join(lines)


def _format_scan_analytics(scan_context: dict | None) -> str:
    """Format scan analytics section (empty when no scan data)."""
    if not scan_context:
        return "(no scan data available)"
    return (
        f"### Variant leaderboard (ranked by accuracy)\n"
        f"{scan_context['leaderboard_text']}\n\n"
        f"### Axis sensitivity (most impactful parameters)\n"
        f"{scan_context['sensitivity_text']}\n\n"
        f"### Query difficulty\n"
        f"{scan_context['difficulty_text']}\n\n"
        f"### Tested values per axis\n"
        f"{scan_context['tested_values']}"
    )


def _format_focus_note(escalation_journal: list[dict] | None) -> str:
    """Format pipeline degradation note from escalation journal."""
    if not escalation_journal:
        return ""
    latest = escalation_journal[-1]
    rate = latest.get("degraded_rate", 0)
    problem_step = latest.get("problem_step", "unknown")
    lines = [
        f"PIPELINE ISSUE: {rate:.0%} of queries degrade at the "
        f"{problem_step} step.",
        "Address pipeline instability in your candidates.",
    ]
    if len(escalation_journal) > 1:
        lines.append(
            f"Previous {len(escalation_journal)} attempts have not "
            "resolved the issue.",
        )
    wtypes = latest.get("warning_types", {})
    if wtypes:
        lines.append(f"Warning breakdown: {wtypes}")
    return "\n".join(lines)


def _format_context_sections(
    task_context: dict | None,
    critique_text: str,
    l2_directive: str,
    thinking_styles: list[str] | None,
    plan: str,
    warning_inventory: dict | None,
    escalation_journal: list[dict] | None,
    is_probe_round: bool,
) -> str:
    """Build all optional context sections as a single string.

    Each non-empty section is a titled block. Returned string is empty
    when no context is available.
    """
    sections: list[str] = []

    # Task context
    if task_context:
        tc_lines = "\n".join(
            f"  {k}: {v}" for k, v in task_context.items() if v
        )
        if tc_lines:
            sections.append(
                "TASK CONTEXT (domain understanding — use to guide "
                f"your changes):\n{tc_lines}"
            )

    # Probe round — enriched with warning inventory and escalation history
    if is_probe_round:
        probe_lines = [
            "PROBE ROUND: These queries have recurring pipeline warnings. "
            "Generate candidates that specifically address pipeline "
            "robustness for the affected steps.",
        ]
        if warning_inventory:
            inv_text = summarize_warning_inventory(warning_inventory)
            if inv_text:
                probe_lines.append("")
                probe_lines.append(inv_text)
            step_counts: dict[str, int] = {}
            for entry in warning_inventory.values():
                for wtype in entry.get("warnings", {}):
                    step = wtype.split(":")[0] if ":" in wtype else wtype
                    step_counts[step] = step_counts.get(step, 0) + 1
            if step_counts:
                dominant_step = max(step_counts, key=step_counts.get)
                probe_lines.append(
                    f"\nDominant problem step: {dominant_step} "
                    f"({step_counts[dominant_step]} warning occurrences)"
                )
                if escalation_journal:
                    tried = [
                        ej for ej in escalation_journal
                        if ej.get("problem_step") == dominant_step
                    ]
                    if tried:
                        probe_lines.append(
                            f"Previous attempts targeting {dominant_step}:"
                        )
                        for ej in tried[-3:]:
                            wt = ej.get("warning_types", {})
                            probe_lines.append(
                                f"  - degraded_rate="
                                f"{ej.get('degraded_rate', 0):.0%}, "
                                f"warnings={wt}"
                            )
        sections.append("\n".join(probe_lines))

    # L2 directive
    if l2_directive:
        sections.append(
            "L2 DIRECTIVE (from context refinement — additional "
            f"guidance for this round):\n{l2_directive}"
        )

    # Critique
    if critique_text:
        sections.append(
            "CRITIQUE (from previous evaluation — use this to guide "
            f"your changes):\n{critique_text}"
        )

    # Thinking styles
    if thinking_styles:
        styles = "\n".join(
            f"  {i+1}. {s}" for i, s in enumerate(thinking_styles)
        )
        sections.append(
            "THINKING STYLES (consider these approaches when generating "
            f"variants):\n{styles}"
        )

    # Strategic plan
    if plan:
        sections.append(f"STRATEGIC GUIDANCE (from optimization plan):\n{plan}")

    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# L1 Generate — candidate prompt variant generation
# ---------------------------------------------------------------------------


async def l1_generate(
    current_ps: PromptState,
    current_accuracy: float,
    current_results: list,
    n_variants: int,
    creativity: float,
    llm_client: LLMClientBase,
    model: str | None = None,
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

    Single LLM call using the ``meta_scan_aware`` template with all
    context injected as template variables.

    Returns:
        List of candidate dicts (PromptState dumps with optional
        ``__pipeline_params_override__``).
    """
    if n_variants <= 0:
        raise ValueError(f"n_variants must be >0, got {n_variants}")

    failure_examples = _format_failure_examples(
        current_results, warning_inventory, is_probe_round,
    )
    instruction_spec = (
        '  - "instruction": full prompt template text '
        "(keep template variables)\n"
    )

    meta_prompt = load_optimizer_prompt("meta_scan_aware").compile(
        n_variants=n_variants,
        accuracy_pct=f"{current_accuracy:.1%}",
        n_queries=len(current_results),
        rendered_prompt=current_ps.render(),
        failure_examples=failure_examples,
        scan_analytics=_format_scan_analytics(scan_context),
        focus_note=_format_focus_note(escalation_journal),
        context_sections=_format_context_sections(
            task_context, critique_text, l2_directive,
            thinking_styles, current_ps.plan or "",
            warning_inventory, escalation_journal, is_probe_round,
        ),
        instruction_spec=instruction_spec,
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
        instr = v.get("instruction", v.get("prompt_text", ""))
        ps = current_ps.derive(
            **({"instruction": instr} if instr else {}),
            changes_description=v.get(
                "changes_description", v.get("variant_name", ""),
            ),
        )
        c_dict = ps.model_dump()
        pp_override = v.get("pipeline_params_override")
        if pp_override and isinstance(pp_override, dict):
            c_dict["__pipeline_params_override__"] = pp_override
        candidates.append(c_dict)

    return candidates


# ---------------------------------------------------------------------------
# L1 Evaluate — candidate evaluation and winner selection
# ---------------------------------------------------------------------------


def _select_round_winner(
    candidates: list[PromptState],
    all_candidate_results: dict[str, list[dict]],
    current_best: dict[str, Any],
    improvement_threshold: float,
    pipeline_schema: Any = None,
) -> dict[str, Any]:
    """Compare candidates and select the round winner."""
    current_acc = current_best["accuracy"]
    current_composite = current_best.get("composite", current_acc)
    current_ps = current_best["prompt_state"]
    current_results = current_best["results"]

    best_composite = current_composite
    best_acc = current_acc
    best_ps = current_ps
    best_results = current_results
    best_label = current_best["label"]
    winner_idx: int | None = None

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


async def l1_evaluate(
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

    Returns:
        Dict with keys: winner, winner_prompt_state, winner_accuracy,
        improved, next_action, suggestions, candidate_scores, winner_results.
    """
    from api.models.search_point import SearchPoint
    from api.services.prompt_eval import evaluate_prompt_cached

    _sp_model = ctx.model
    _sp_temperature = ctx.temperature
    _sp_pipeline_params = ctx.pipeline_params

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
        results, scores, cached = await evaluate_prompt_cached(
            sp, eval_data, ctx,
            label=f"candidate_{idx}",
            on_result=_on_result,
        )

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

    cb = dict(current_best)
    if isinstance(cb.get("prompt_state"), dict):
        cb["prompt_state"] = PromptState(**cb["prompt_state"])

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


