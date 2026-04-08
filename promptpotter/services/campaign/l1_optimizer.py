"""
Prompt optimization service.

L1 Generate and L1 Evaluate services for the optimizer pipeline, plus
improvement suggestion generation.
"""

from __future__ import annotations

import copy
import logging
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel, Field

from promptpotter.config.optimizer_pipeline import llm_call
from promptpotter.config.optimizer_prompt_loader import load_optimizer_prompt
from promptpotter.models.opt_search_point import OptSearchPoint
from promptpotter.models.pipeline_schema import PipelineSchema
from promptpotter.models.query_result import QueryResult
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
    from promptpotter.services.campaign.state import RunCallbacks
    from promptpotter.services.search.scan_results import ScanContext

logger = logging.getLogger(__name__)

__all__ = ["L1EvalResult", "l1_evaluate", "l1_generate"]


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
    winner_results: list[QueryResult]
    all_candidate_results: dict[str, list[dict]] = Field(default_factory=dict)
    escalation_signal: dict[str, Any] | None = None
    degraded_queries: int = 0

    # Populated post-eval by round_execution (critique phase)
    critique_text: str = ""
    thinking_styles: list[str] = Field(default_factory=list)


async def l1_generate(
    opt_sp: OptSearchPoint,
    current_accuracy: float,
    current_results: list[dict],
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
                    lines.append(
                        '      Replace: ["~", "old_name", "new_name", "array", true, "description"]'
                    )
                    lines.append(
                        f'    Example: {{"{node.name}": {{"output_schema": [["+", "domain_terms", "array", true, "Domain-specific database entry names"]]}}}}'
                    )

            # Token matching mechanics — so LLM understands the causal chain
            if pipeline_schema.get_node("token_matching"):
                lines.append("\n  HOW TOKEN MATCHING USES ENTITY PROFILES:")
                lines.append("    ALL entity profile field values are tokenized ([a-zA-Z0-9]+)")
                lines.append("    and matched against database entry tokens.")
                lines.append("    Score = shared_tokens / term_tokens.")
                lines.append("    Adding fields that produce tokens matching database entries")
                lines.append(
                    "    DIRECTLY improves retrieval. Removing noisy fields reduces false matches."
                )

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
        node_overrides = {
            k: pv
            for k, pv in pp_override.items()
            if k not in PROMPT_STRING_FIELDS and k not in _TASK_CONTEXT_OVERRIDES
        }

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
                    if (
                        isinstance(_np, dict)
                        and "model" in _np
                        and _np["model"] not in _valid_models
                    ):
                        logger.warning(
                            "l1_generate: dropping invalid model %r for %s", _np["model"], _nn
                        )
                        del _np["model"]

        child = opt_sp.derive_candidate(
            changes_description=v.get("changes_description", ""),
            **prompt_changes,
        )
        if tc_changes:
            child.task_context = child.task_context.merge(tc_changes)
        c_dict = child.prompt_field_dict()
        c_dict["id"] = child.id
        c_dict["parent_id"] = child.parent_id
        c_dict["changes_description"] = child.changes_description
        if node_overrides:
            c_dict["__pipeline_params_override__"] = node_overrides
        candidates.append(c_dict)

    return candidates


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

    assert pipeline_schema is not None, "_select_round_winner requires pipeline_schema"

    # Score each candidate once, reuse for selection and display
    candidate_scores = {
        c.id: compute_composite_score(
            cast(list[QueryResult], all_candidate_results[c.id]), pipeline_schema
        )
        for c in candidates
    }

    # Find best candidate
    best_composite = current_composite
    best_acc = current_acc
    best_ps: OptSearchPoint = current_best["prompt_fields"]
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


def _merge_pipeline_params(
    base: dict | None,
    overrides: dict | None,
    schema: PipelineSchema | None,
) -> dict | None:
    """Deep-merge candidate pipeline_params overrides into the base params.

    Drops overrides for nodes not in the active pipeline steps.
    """
    if not overrides:
        return base
    merged: dict = copy.deepcopy(base or {})
    for k, v in overrides.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = {**merged[k], **v}
        else:
            merged[k] = v
    if schema:
        _active = set(schema.active_steps)
        for k in list(merged):
            if k != "steps" and isinstance(merged[k], dict) and k not in _active:
                logger.warning("Dropping LLM override for excluded node %r", k)
                del merged[k]
    return merged


def _parse_candidates(
    candidates: list[dict],
    pipeline_params: dict | None,
    schema: PipelineSchema | None,
) -> tuple[list[OptSearchPoint], list[dict | None], list[dict | None]]:
    """Normalize raw candidate dicts into OptSearchPoints with merged params.

    Returns (osp_candidates, merged_pipeline_params, raw_overrides).
    """
    overrides: list[dict | None] = []
    osp_list: list[OptSearchPoint] = []
    merged: list[dict | None] = []
    for c in candidates:
        override = c.get("__pipeline_params_override__")
        overrides.append(override)
        osp_list.append(
            OptSearchPoint.from_prompt_fields(
                {k: v for k, v in c.items() if k != "__pipeline_params_override__"},
            )
        )
        merged.append(_merge_pipeline_params(pipeline_params, override, schema))
    return osp_list, merged, overrides


async def _run_candidate_evals(
    osp_candidates: list[OptSearchPoint],
    merged_pp: list[dict | None],
    candidate_overrides: list[dict | None],
    dataset: list,
    ctx: EvalContext,
    *,
    degradation_checks: list | None = None,
    callbacks: RunCallbacks | None = None,
) -> tuple[dict[str, list[dict]], list[dict], dict | None]:
    """Evaluate each candidate against the dataset.

    Returns (all_candidate_results, candidate_scores, escalation_signal).
    """
    from promptpotter.services.eval_gateway import eval_search_point

    all_candidate_results: dict[str, list[dict]] = {}
    candidate_scores: list[dict] = []
    escalation_signal = None
    n_candidates = len(osp_candidates)

    for idx, osp_c in enumerate(osp_candidates):
        _on_result = None
        if callbacks and callbacks.on_query_eval:

            def _on_result(result, qi, qt, _ci=idx, _ct=n_candidates):
                callbacks.on_query_eval(_ci, _ct, qi, qt, result)

        sp = osp_c.to_job_search_point(
            base_pipeline_params=merged_pp[idx],
            schema=ctx.pipeline_schema,
        )
        results, scores, _cached = await eval_search_point(
            sp,
            dataset,
            ctx,
            label=f"candidate_{idx}",
            on_result=_on_result,
            degradation_checks=degradation_checks,
            candidate_idx=idx,
            n_total_candidates=n_candidates,
        )

        escalation_signal = scores.pop("escalation_signal", None)

        aborted = bool(escalation_signal) and len(results) < len(dataset)
        all_candidate_results[osp_c.id] = results
        candidate_scores.append(
            {
                "candidate_id": osp_c.id,
                "changes_description": osp_c.changes_description or "",
                "pipeline_params_override": candidate_overrides[idx],
                "accuracy": scores["accuracy"],
                "composite": scores.get("composite", scores["accuracy"]),
                "hits": scores["hits"],
                "total": scores["total"],
                "escalation_aborted": aborted,
                "eval_queries": len(results),
                "expected_queries": len(dataset),
            }
        )
        if callbacks and callbacks.on_candidate_eval:
            scores["escalation_aborted"] = aborted
            scores["eval_queries"] = len(results)
            scores["expected_queries"] = len(dataset)
            callbacks.on_candidate_eval(idx, n_candidates, scores)

        if escalation_signal:
            break

    return all_candidate_results, candidate_scores, escalation_signal


async def l1_evaluate(
    candidates: list[dict],
    dataset: list,
    current_best: dict[str, Any],
    ctx: EvalContext,
    *,
    pipeline_params: dict | None = None,
    improvement_threshold: float = 0.01,
    callbacks: RunCallbacks | None = None,
    degradation_checks: list | None = None,
) -> L1EvalResult:
    """Evaluate candidates and select the round winner."""
    # Normalize current_best prompt_fields to OptSearchPoint once at entry
    cb = dict(current_best)
    if isinstance(cb.get("prompt_fields"), dict):
        cb["prompt_fields"] = OptSearchPoint.from_prompt_fields(cb["prompt_fields"])

    # Parse → Evaluate → Select → Package
    osp_candidates, merged_pp, overrides = _parse_candidates(
        candidates,
        pipeline_params,
        ctx.pipeline_schema,
    )
    all_candidate_results, candidate_scores, escalation_signal = await _run_candidate_evals(
        osp_candidates,
        merged_pp,
        overrides,
        dataset,
        ctx,
        degradation_checks=degradation_checks,
        callbacks=callbacks,
    )

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

    # Reuse pre-computed merged params for winner (no re-merge needed)
    w_idx = winner_entry["winner_idx"]
    winner_pp = merged_pp[w_idx] if w_idx is not None else pipeline_params

    winner_osp: OptSearchPoint = winner_entry["prompt_fields"]
    winner_dict = winner_osp.prompt_field_dict()
    winner_dict["id"] = winner_osp.id
    winner_dict["parent_id"] = winner_osp.parent_id
    winner_dict["changes_description"] = winner_osp.changes_description
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
        all_candidate_results=dict(all_candidate_results),
        escalation_signal=escalation_signal,
        degraded_queries=count_degraded_queries(winner_entry.get("results", [])),
    )
