"""
Prompt optimization service.

L1 Generate and L1 Evaluate services for the optimizer pipeline, plus
improvement suggestion generation.
"""

from __future__ import annotations

import copy
import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from promptpotter.config.optimizer_pipeline import llm_call
from promptpotter.config.optimizer_prompt_loader import load_optimizer_prompt
from promptpotter.models.opt_search_point import OptSearchPoint
from promptpotter.models.pipeline_schema import PipelineSchema
from promptpotter.services.campaign.formatting import (
    ContextData,
    format_context_sections,
)
from promptpotter.services.llm_client import LLMClientBase
from promptpotter.services.metrics import compute_composite_score, count_degraded_queries
from promptpotter.shared.constants import PROMPT_STRING_FIELDS
from promptpotter.shared.llm_parsing import extract_parsed_json

if TYPE_CHECKING:
    from promptpotter.models.analysis import FailureAnalysis
    from promptpotter.models.eval_context import EvalContext
    from promptpotter.services.campaign.callbacks import RunCallbacks
    from promptpotter.services.search.scan_results import ScanContext

logger = logging.getLogger(__name__)

__all__ = ["L1EvalResult", "l1_evaluate", "l1_generate"]


# ---------------------------------------------------------------------------
# L1 Evaluate result model
# ---------------------------------------------------------------------------


class L1EvalResult(BaseModel):
    """Structured return value from ``l1_evaluate()``."""

    label: str
    winner_prompt_fields: dict[str, Any]
    winner_pipeline_params: dict[str, Any] | None
    winner_accuracy: float
    winner_composite: float
    hits: int
    total: int
    improved: bool
    candidates_evaluated: int
    candidate_scores: list[dict[str, Any]]
    winner_results: list[dict[str, Any]]
    all_eval_results: list[dict[str, Any]] = Field(default_factory=list)
    escalation_signal: dict[str, Any] | None = None
    degraded_queries: int = 0

    # Populated post-eval by round_execution (critique phase)
    critique_text: str = ""
    thinking_styles: list[str] = Field(default_factory=list)


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
    scan_context: ScanContext | None = None,
    is_probe_round: bool = False,
    scan_compact: bool = False,
    failure_analysis: FailureAnalysis | None = None,
    search_memory_context: dict | None = None,
    pipeline_schema: PipelineSchema | None = None,
) -> list[dict]:
    """Generate candidate pipeline-param variants via LLM meta-prompt.

    All optimizer context (critique, thinking_styles, task_context, plan,
    escalation_journal, warning_inventory, l2_directive) is read from the
    ``OptSearchPoint`` — no need to pass them individually.

    Returns:
        List of candidate dicts (prompt field dumps with
        ``__pipeline_params_override__``).
    """
    if n_variants <= 0:
        raise ValueError(f"n_variants must be >0, got {n_variants}")

    # Build pipeline schema text so the LLM knows valid nodes/params
    schema_text = ""
    if pipeline_schema:
        npk = pipeline_schema.node_param_keys()
        if npk:
            lines = [
                "VALID PIPELINE NODES AND PARAMETERS (only use these — do not invent nodes or params):",
            ]
            for node_name, params in npk.items():
                node = pipeline_schema.get_node(node_name)
                descs = node.param_descriptions if node else {}
                if params:
                    param_parts = []
                    for p in sorted(params):
                        desc = descs.get(p)
                        param_parts.append(f"{p} ({desc})" if desc else p)
                    lines.append(f"  {node_name}: {', '.join(param_parts)}")
                else:
                    lines.append(f"  {node_name}: (no tunable params)")

            # Output schema context — teaches LLM the mutation language
            for node in pipeline_schema.nodes:
                if node.output_schema and node.output_schema.fields:
                    os = node.output_schema
                    lines.append(f"\n  CURRENT OUTPUT SCHEMA for {node.name}:")
                    lines.append(f"    Fields: {', '.join(os.fields)}")
                    for fname, fdesc in os.field_descriptions.items():
                        lines.append(f"      {fname}: {fdesc}")
                    lines.append("    MUTATION SYNTAX (use as output_schema param):")
                    lines.append('      Add:     ["+", "field_name", "array", true, "description"]')
                    lines.append('      Remove:  ["-", "field_name"]')
                    lines.append('      Replace: ["~", "old_name", "new_name", "array", true, "description"]')
                    lines.append(f'    Example: {{"{node.name}": {{"output_schema": [["+", "domain_terms", "array", true, "Domain-specific database entry names"]]}}}}')

            # Token matching mechanics — so LLM understands the causal chain
            if pipeline_schema.get_node("token_matching"):
                lines.append("\n  HOW TOKEN MATCHING USES ENTITY PROFILES:")
                lines.append("    ALL entity profile field values are tokenized ([a-zA-Z0-9]+)")
                lines.append("    and matched against database entry tokens.")
                lines.append("    Score = shared_tokens / term_tokens.")
                lines.append("    Adding fields that produce tokens matching database entries")
                lines.append("    DIRECTLY improves retrieval. Removing noisy fields reduces false matches.")

            schema_text = "\n".join(lines)
        if pipeline_schema.available_models:
            schema_text += "\n\nAVAILABLE MODELS (only use these for model overrides):\n"
            schema_text += "\n".join(f"  {m}" for m in pipeline_schema.available_models)

    _compile_vars = {
        "n_variants": str(n_variants),
        "accuracy_pct": f"{current_accuracy:.1%}",
        "n_queries": str(len(current_results)),
        "rendered_prompt": opt_sp.render(),
        "context_sections": format_context_sections(
            ContextData(
                task_context=opt_sp.task_context or None,
                critique_text=opt_sp.critique_text,
                l2_directive=opt_sp.l2_directive,
                thinking_styles=opt_sp.thinking_styles or None,
                plan=opt_sp.plan or "",
                warning_inventory=opt_sp.warning_inventory or None,
                escalation_journal=opt_sp.escalation_journal or None,
                is_probe_round=is_probe_round,
                scan_context=scan_context,
                scan_compact=scan_compact,
                failure_analysis=failure_analysis,
                search_memory_context=search_memory_context,
                pipeline_schema_text=schema_text,
            )
        ),
    }
    _template = load_optimizer_prompt("meta_scan_aware")
    meta_prompt = _template.compile_prompt(**_compile_vars)

    response = await llm_call(
        llm_client,
        messages=[{"role": "user", "content": meta_prompt}],
        node="l1_generate",
        model=model,
        temperature=creativity,
        trace_meta={
            "template_name": "meta_scan_aware",
            "template_fields": _template.prompt_field_dict(),
            "variables": _compile_vars,
        },
    )
    generated = extract_parsed_json(response)

    variants_list = generated.get("variants", []) if isinstance(generated, dict) else generated

    candidates: list[dict] = []
    for v in variants_list[:n_variants]:
        # Split pipeline_params_override: prompt scheme fields go to
        # derive_candidate (rendered into the prompt string), task_context
        # sub-fields update task_context, the rest stays as node-level
        # pipeline overrides (already nested).
        _TASK_CONTEXT_OVERRIDES = {"upstream_context", "downstream_context"}
        pp_override = v.get("pipeline_params_override") or {}
        pp_override.pop("steps", None)  # LLM must not override pipeline composition
        prompt_changes = {k: pp_override[k] for k in pp_override if k in PROMPT_STRING_FIELDS}
        tc_changes = {k: pp_override[k] for k in pp_override if k in _TASK_CONTEXT_OVERRIDES}
        node_overrides = {k: pv for k, pv in pp_override.items()
                          if k not in PROMPT_STRING_FIELDS and k not in _TASK_CONTEXT_OVERRIDES}

        # Safety net: auto-nest flat params the LLM may still emit
        if pipeline_schema:
            _flat_keys = [k for k, v in node_overrides.items() if not isinstance(v, dict)]
            for fk in _flat_keys:
                owner = pipeline_schema.node_for_param(fk)
                if owner:
                    logger.warning("l1_generate: auto-nesting flat param %r → %s", fk, owner)
                    node_overrides.setdefault(owner, {})[fk] = node_overrides.pop(fk)

            # Filter out hallucinated nodes that don't exist in the pipeline
            _valid_nodes = {n.name for n in pipeline_schema.nodes}
            _bad_nodes = [k for k in node_overrides if k not in _valid_nodes]
            for bk in _bad_nodes:
                logger.warning("l1_generate: dropping hallucinated node %r", bk)
                del node_overrides[bk]

            # Filter out invalid model overrides
            if pipeline_schema.available_models:
                _valid_models = set(pipeline_schema.available_models)
                for _nn, _np in node_overrides.items():
                    if isinstance(_np, dict) and "model" in _np and _np["model"] not in _valid_models:
                        logger.warning("l1_generate: dropping invalid model %r for %s", _np["model"], _nn)
                        del _np["model"]

        child = opt_sp.derive_candidate(
            changes_description=v.get("changes_description", ""),
            **prompt_changes,
        )
        if tc_changes:
            child.task_context = {**child.task_context, **tc_changes}
        c_dict = child.prompt_field_dict()
        c_dict["id"] = child.id
        c_dict["parent_id"] = child.parent_id
        c_dict["changes_description"] = child.changes_description
        if node_overrides:
            c_dict["__pipeline_params_override__"] = node_overrides
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
    best_ps: dict | OptSearchPoint = current_best["prompt_fields"]
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

    return {
        "label": best_label,
        "prompt_fields": best_ps,
        "accuracy": best_acc,
        "composite": best_composite,
        "hits": sum(1 for r in best_results if r["hit"]),
        "total": len(best_results),
        "results": best_results,
        "candidates_evaluated": len(candidates),
        "improved": best_composite > current_composite + improvement_threshold,
        "winner_idx": winner_idx,
    }


async def l1_evaluate(
    candidates: list[dict],
    eval_data: list,
    current_best: dict[str, Any],
    ctx: EvalContext,
    *,
    pipeline_params: dict | None = None,
    active_steps: tuple[str, ...] = (),
    improvement_threshold: float = 0.01,
    callbacks: RunCallbacks | None = None,
    escalation_checks: list | None = None,
) -> L1EvalResult:
    """Evaluate candidates and select the round winner."""
    from promptpotter.services.eval_gateway import eval_search_point

    _sp_pipeline_params = pipeline_params

    candidate_pp: list[dict | None] = []
    osp_candidates: list[OptSearchPoint] = []
    for c in candidates:
        if isinstance(c, dict):
            candidate_pp.append(c.get("__pipeline_params_override__"))
            osp_candidates.append(OptSearchPoint.from_prompt_fields(
                {k: v for k, v in c.items() if k != "__pipeline_params_override__"},
            ))
        else:
            candidate_pp.append(None)
            osp_candidates.append(
                c if isinstance(c, OptSearchPoint)
                else OptSearchPoint.from_prompt_fields(c.model_dump()),
            )
    all_candidate_results: dict[str, list[dict]] = {}
    candidate_scores: list[dict] = []

    escalation_signal = None
    ctx.escalation_checks = escalation_checks
    ctx.n_total_candidates = len(osp_candidates)

    for idx, osp_c in enumerate(osp_candidates):
        _on_result = None
        if callbacks and callbacks.on_query_eval:
            _n_cand = len(osp_candidates)

            def _on_result(result, qi, qt, _ci=idx, _ct=_n_cand):
                callbacks.on_query_eval(_ci, _ct, qi, qt, result)

        _cpp = candidate_pp[idx]
        if _cpp:
            # Deep merge: preserve existing node dicts while applying overrides
            pp: dict | None = copy.deepcopy(_sp_pipeline_params or {})
            for k, v in _cpp.items():
                if isinstance(v, dict) and isinstance(pp.get(k), dict):
                    pp[k] = {**pp[k], **v}
                else:
                    pp[k] = v
            # Safety net: drop overrides for nodes not in active steps
            if active_steps:
                _active = set(active_steps)
                for k in list(pp):
                    if k != "steps" and isinstance(pp[k], dict) and k not in _active:
                        logger.warning("Dropping LLM override for excluded node %r", k)
                        del pp[k]
        else:
            pp = _sp_pipeline_params
        _pn = ctx.pipeline_schema.prompt_node_names()[0] if ctx.pipeline_schema and ctx.pipeline_schema.prompt_node_names() else ""
        sp = osp_c.to_job_search_point(
            base_pipeline_params=pp,
            active_steps=active_steps,
            prompt_node=_pn,
        )
        ctx.candidate_idx = idx
        results, scores, cached = await eval_search_point(
            sp,
            eval_data,
            ctx,
            label=f"candidate_{idx}",
            on_result=_on_result,
        )

        escalation_signal = scores.pop("escalation_signal", None)

        aborted = bool(escalation_signal) and len(results) < len(eval_data)
        all_candidate_results[osp_c.id] = results
        candidate_scores.append(
            {
                "candidate_id": osp_c.id,
                "changes_description": osp_c.changes_description or "",
                "pipeline_params_override": _cpp,
                "accuracy": scores["accuracy"],
                "composite": scores.get("composite", scores["accuracy"]),
                "hits": scores["hits"],
                "total": scores["total"],
                "cached": cached,
                "escalation_aborted": aborted,
                "eval_queries": len(results),
                "expected_queries": len(eval_data),
            }
        )
        if callbacks and callbacks.on_candidate_eval:
            scores["escalation_aborted"] = aborted
            scores["eval_queries"] = len(results)
            scores["expected_queries"] = len(eval_data)
            callbacks.on_candidate_eval(idx, len(osp_candidates), scores)

        if escalation_signal:
            break

    cb = dict(current_best)
    if isinstance(cb.get("prompt_fields"), dict):
        cb["prompt_fields"] = OptSearchPoint.from_prompt_fields(cb["prompt_fields"])

    evaluated_candidates = [
        c
        for c in osp_candidates
        if c.id in all_candidate_results
        and not any(
            cs.get("escalation_aborted") and cs["candidate_id"] == c.id for cs in candidate_scores
        )
    ]
    winner_entry = _select_round_winner(
        evaluated_candidates,
        all_candidate_results,
        cb,
        improvement_threshold,
        pipeline_schema=ctx.pipeline_schema,
    )

    w_idx = winner_entry["winner_idx"]
    _w_cpp = candidate_pp[w_idx] if w_idx is not None else None
    winner_pp: dict | None
    if w_idx is not None and _w_cpp:
        winner_pp = copy.deepcopy(_sp_pipeline_params or {})
        for k, v in _w_cpp.items():
            if isinstance(v, dict) and isinstance(winner_pp.get(k), dict):
                winner_pp[k] = {**winner_pp[k], **v}
            else:
                winner_pp[k] = v
    else:
        winner_pp = _sp_pipeline_params

    winner_osp = winner_entry["prompt_fields"]
    # Serialize winner as prompt field dict (compatible with RoundResult.prompt_fields)
    if isinstance(winner_osp, OptSearchPoint):
        winner_dict = winner_osp.prompt_field_dict()
        winner_dict["id"] = winner_osp.id
        winner_dict["parent_id"] = winner_osp.parent_id
        winner_dict["changes_description"] = winner_osp.changes_description
    elif isinstance(winner_osp, dict):
        winner_dict = winner_osp
    else:
        raise TypeError(f"Unexpected winner type: {type(winner_osp)}")
    return L1EvalResult(
        label=winner_entry["label"],
        winner_prompt_fields=winner_dict,
        winner_pipeline_params=winner_pp,
        winner_accuracy=winner_entry["accuracy"],
        winner_composite=winner_entry.get("composite", winner_entry["accuracy"]),
        hits=winner_entry["hits"],
        total=winner_entry["total"],
        improved=winner_entry["improved"],
        candidates_evaluated=winner_entry["candidates_evaluated"],
        candidate_scores=candidate_scores,
        winner_results=winner_entry.get("results", []),
        all_eval_results=[r for results in all_candidate_results.values() for r in results],
        escalation_signal=escalation_signal,
        degraded_queries=count_degraded_queries(winner_entry.get("results", [])),
    )
