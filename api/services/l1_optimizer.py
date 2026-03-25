"""
Prompt optimization service.

L1 Generate and L1 Evaluate services for the optimizer pipeline, plus
improvement suggestion generation.
"""

import json
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from api.models.pipeline_schema import PipelineSchema

from api.config.optimizer_prompt_loader import load_optimizer_prompt
from api.config.settings import DISPLAY_TRUNCATE
from api.models.opt_search_point import OptSearchPoint
from api.core.llm_call import get_node_config, llm_call
from api.services.campaign.formatting import (
    format_failure_examples as _format_failure_examples,
    format_scan_analytics as _format_scan_analytics,
    format_focus_note as _format_focus_note,
    format_context_sections as _format_context_sections,
)
from api.services.llm_client import LLMClientBase
from api.services.metrics import compute_composite_score

if TYPE_CHECKING:
    from api.services.eval_context import EvalContext

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# L1 Generate — candidate prompt variant generation
# ---------------------------------------------------------------------------


async def l1_generate(
    opt_sp: OptSearchPoint,
    current_accuracy: float,
    current_results: list,
    n_variants: int,
    creativity: float,
    llm_client: LLMClientBase,
    model: str | None = None,
    scan_context: dict | None = None,
    is_probe_round: bool = False,
) -> list[dict]:
    """Generate candidate prompt variants via LLM meta-prompt.

    All optimizer context (critique, thinking_styles, task_context, plan,
    escalation_journal, warning_inventory, l2_directive) is read from the
    ``OptSearchPoint`` — no need to pass them individually.

    Returns:
        List of candidate dicts (prompt field dumps with optional
        ``__pipeline_params_override__``).
    """
    if n_variants <= 0:
        raise ValueError(f"n_variants must be >0, got {n_variants}")

    failure_examples = _format_failure_examples(
        current_results, opt_sp.warning_inventory or None, is_probe_round,
    )
    instruction_spec = (
        '  - "instruction": full prompt template text '
        "(keep template variables)\n"
    )

    meta_prompt = load_optimizer_prompt("meta_scan_aware").compile_prompt(
        n_variants=n_variants,
        accuracy_pct=f"{current_accuracy:.1%}",
        n_queries=len(current_results),
        rendered_prompt=opt_sp.render(),
        failure_examples=failure_examples,
        scan_analytics=_format_scan_analytics(scan_context),
        focus_note=_format_focus_note(opt_sp.escalation_journal or None),
        context_sections=_format_context_sections(
            opt_sp.task_context or None, opt_sp.critique_text, opt_sp.l2_directive,
            opt_sp.thinking_styles or None, opt_sp.plan or "",
            opt_sp.warning_inventory or None, opt_sp.escalation_journal or None,
            is_probe_round,
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
        variants_list = generated.get("variants", [])
    else:
        variants_list = generated

    candidates: list[dict] = []
    for v in variants_list[:n_variants]:
        instr = v.get("instruction", "")
        child = opt_sp.derive_candidate(
            **({"instruction": instr} if instr else {}),
            changes_description=v.get("changes_description", ""),
        )
        c_dict = child.prompt_field_dict()
        c_dict["id"] = child.id
        c_dict["parent_id"] = child.parent_id
        c_dict["changes_description"] = child.changes_description
        pp_override = v.get("pipeline_params_override")
        if pp_override and isinstance(pp_override, dict):
            c_dict["__pipeline_params_override__"] = pp_override
        candidates.append(c_dict)

    return candidates


# ---------------------------------------------------------------------------
# L1 Evaluate — candidate evaluation and winner selection
# ---------------------------------------------------------------------------


def _select_round_winner(
    candidates: list[OptSearchPoint],
    all_candidate_results: dict[str, list[dict]],
    current_best: dict[str, Any],
    improvement_threshold: float,
    pipeline_schema: PipelineSchema | None = None,
) -> dict[str, Any]:
    """Compare candidates and select the round winner."""
    current_acc = current_best["accuracy"]
    current_composite = current_best.get("composite", current_acc)

    # Score each candidate once, reuse for selection and display
    candidate_scores = {
        c.id: compute_composite_score(all_candidate_results[c.id], pipeline_schema)
        for c in candidates
    }

    # Find best candidate
    best_composite = current_composite
    best_acc = current_acc
    best_ps = current_best["prompt_state"]
    best_results = current_best["results"]
    best_label = current_best["label"]
    winner_idx: int | None = None

    for idx, candidate in enumerate(candidates):
        c_composite = candidate_scores[candidate.id]["composite"]
        if c_composite > best_composite:
            best_composite = c_composite
            best_acc = candidate_scores[candidate.id]["accuracy"]
            best_ps = candidate
            best_results = all_candidate_results[candidate.id]
            best_label = candidate.changes_description or candidate.id[:12]
            winner_idx = idx

    # Build comparison rows
    rows = [
        {
            "prompt": f"current_best ({current_best['label'][:30]})",
            "hit@1": f"{current_acc:.1%}",
            "composite": f"{current_composite:.4f}",
            "delta": "-",
        }
    ]
    for candidate in candidates:
        scores = candidate_scores[candidate.id]
        delta = scores["composite"] - current_composite
        rows.append({
            "prompt": (
                candidate.changes_description or candidate.id[:12]
            )[:DISPLAY_TRUNCATE],
            "hit@1": f"{scores['accuracy']:.1%}",
            "composite": f"{scores['composite']:.4f}",
            "delta": f"{delta:+.4f}",
        })

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
        "improved": best_composite > current_composite + improvement_threshold,
        "winner_idx": winner_idx,
    }


async def l1_evaluate(
    candidates: list[dict],
    eval_data: list,
    current_best: dict[str, Any],
    ctx: "EvalContext",
    *,
    model: str = "",
    temperature: float = 0.0,
    pipeline_params: dict | None = None,
    improvement_threshold: float = 0.01,
    on_candidate_eval: Callable[[int, int, dict], None] | None = None,
    on_query_eval: Callable[[int, int, int, int, dict], None] | None = None,
    escalation_checks: list | None = None,
) -> dict[str, Any]:
    """Evaluate candidates and select the round winner.

    Returns:
        Dict with keys: winner, winner_prompt_state, winner_accuracy,
        improved, next_action, candidate_scores, winner_results.
    """
    from api.services.prompt_eval import evaluate_prompt_cached

    _sp_model = model
    _sp_temperature = temperature
    _sp_pipeline_params = pipeline_params

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

    osp_candidates = [OptSearchPoint.from_prompt_fields(c) for c in clean_candidates]
    all_candidate_results: dict[str, list[dict]] = {}
    candidate_scores: list[dict] = []

    escalation_signal = None
    ctx.escalation_checks = escalation_checks
    ctx.n_total_candidates = len(osp_candidates)

    for idx, c in enumerate(osp_candidates):
        _on_result = None
        if on_query_eval:
            def _on_result(result, qi, qt, _ci=idx, _ct=len(osp_candidates)):
                on_query_eval(_ci, _ct, qi, qt, result)

        if candidate_pp[idx]:
            pp = {**(_sp_pipeline_params or {}), **candidate_pp[idx]}
        else:
            pp = _sp_pipeline_params
        sp = c.to_job_search_point(
            model=_sp_model,
            temperature=_sp_temperature,
            base_pipeline_params=pp,
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
            on_candidate_eval(idx, len(osp_candidates), scores)

        if escalation_signal:
            break

    cb = dict(current_best)
    if isinstance(cb.get("prompt_state"), dict):
        cb["prompt_state"] = OptSearchPoint.from_prompt_fields(cb["prompt_state"])

    evaluated_candidates = [
        c for c in osp_candidates
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

    winner_osp = winner_entry["prompt_state"]
    # Serialize winner as prompt field dict (compatible with CycleRoundResult.prompt_state)
    if isinstance(winner_osp, OptSearchPoint):
        winner_dict = winner_osp.prompt_field_dict()
        winner_dict["id"] = winner_osp.id
        winner_dict["parent_id"] = winner_osp.parent_id
        winner_dict["changes_description"] = winner_osp.changes_description
    elif isinstance(winner_osp, dict):
        winner_dict = winner_osp
    else:
        winner_dict = winner_osp.model_dump()
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
        "winner_prompt_state": winner_dict,
        "winner_pipeline_params": winner_pp,
        "winner_accuracy": winner_entry["accuracy"],
        "winner_composite": winner_entry.get("composite", winner_entry["accuracy"]),
        "improved": winner_entry["improved"],
        "next_action": "escalate" if escalation_signal else "generate",
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


