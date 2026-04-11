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

from promptpotter.models.opt_search_point import OptSearchPoint
from promptpotter.models.pipeline_schema import PipelineSchema
from promptpotter.models.query_result import QueryResult
from promptpotter.services.campaign.formatting import (
    L1PromptData,
    format_context_sections,
)
from promptpotter.services.llm_client import LLMClientBase
from promptpotter.services.metrics import compute_composite_score, count_degraded_queries
from promptpotter.services.optimizer.pipeline import llm_call
from promptpotter.services.optimizer.prompt_preparation import load_optimizer_prompt
from promptpotter.shared.constants import PROMPT_STRING_FIELDS
from promptpotter.shared.llm_parsing import extract_parsed_json

if TYPE_CHECKING:
    from promptpotter.models.analysis import FailureAnalysis
    from promptpotter.models.scoring_env import ScoringEnv
    from promptpotter.services.campaign.state import RunCallbacks
    from promptpotter.services.search.scan_results import ScanBrief

logger = logging.getLogger(__name__)

__all__ = ["L1ScoringResult", "l1_generate", "l1_score"]


class L1ScoringResult(BaseModel):
    """Structured return value from ``l1_score()``."""

    label: str
    winner_prompt_fields: dict[str, Any]
    winner_pipeline_params: dict[str, Any] | None
    winner_accuracy: float
    winner_composite: float
    hits: int
    total: int
    improved: bool
    candidates_scored: int
    candidate_scores: list[dict[str, Any]]
    winner_results: list[QueryResult]
    all_candidate_results: dict[str, list[dict]] = Field(default_factory=dict)
    escalation_signal: dict[str, Any] | None = None
    degraded_queries: int = 0

    # Populated post-eval by round_execution (critique phase)
    critique_text: str = ""
    thinking_styles: list[str] = Field(default_factory=list)


def _render_schema_text(pipeline_schema: PipelineSchema) -> str:
    """Build pipeline schema description for L1 LLM context."""
    lines: list[str] = []
    npk = pipeline_schema.node_param_keys()
    if npk:
        lines.append(
            "VALID PIPELINE NODES AND PARAMETERS (only use these — do not invent nodes or params):"
        )
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
                    f'    Example: {{"{node.name}": {{"output_schema": '
                    f'[["+", "domain_terms", "array", true, '
                    f'"Domain-specific database entry names"]]}}}}'
                )

        if pipeline_schema.get_node("token_matching"):
            lines.append("\n  HOW TOKEN MATCHING USES ENTITY PROFILES:")
            lines.append("    ALL entity profile field values are tokenized ([a-zA-Z0-9]+)")
            lines.append("    and matched against database entry tokens.")
            lines.append("    Score = shared_tokens / term_tokens.")
            lines.append("    Adding fields that produce tokens matching database entries")
            lines.append(
                "    DIRECTLY improves retrieval. Removing noisy fields reduces false matches."
            )

    text = "\n".join(lines)
    if pipeline_schema.available_models:
        text += "\n\nAVAILABLE MODELS (only use these for model overrides):\n"
        text += "\n".join(f"  {m}" for m in pipeline_schema.available_models)
    return text


async def l1_generate(
    opt_sp: OptSearchPoint,
    current_accuracy: float,
    current_results: list[dict],
    n_variants: int,
    creativity: float,
    llm_client: LLMClientBase,
    model: str | None = None,
    scan_brief: ScanBrief | None = None,
    is_probe_round: bool = False,
    scan_compact: bool = False,
    failure_analysis: FailureAnalysis | None = None,
    search_memory_digest: dict | None = None,
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

    schema_text = _render_schema_text(pipeline_schema) if pipeline_schema else ""

    _compile_vars = {
        "n_variants": str(n_variants),
        "accuracy_pct": f"{current_accuracy:.1%}",
        "n_queries": str(len(current_results)),
        "rendered_prompt": opt_sp.render(),
        "context_sections": format_context_sections(
            L1PromptData(
                task_context=opt_sp.task_context or None,
                critique_text=opt_sp.critique_text,
                l2_directive=opt_sp.l2_directive,
                thinking_styles=opt_sp.thinking_styles or None,
                plan=opt_sp.plan or "",
                warning_inventory=opt_sp.warning_inventory or None,
                escalation_journal=opt_sp.escalation_journal or None,
                is_probe_round=is_probe_round,
                scan_brief=scan_brief,
                scan_compact=scan_compact,
                failure_analysis=failure_analysis,
                search_memory_digest=search_memory_digest,
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
            _bad_nodes = [k for k in node_overrides if not pipeline_schema.has_node(k)]
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
        "candidates_scored": len(candidates),
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


def _build_score_report(
    osp: OptSearchPoint,
    override: dict | None,
    scores: dict,
    results: list,
    dataset: list,
    *,
    aborted: bool = False,
    elimination_stopped: bool = False,
    resumed_from_cache: bool = False,
) -> dict:
    """Build unified candidate score report dict."""
    return {
        "candidate_id": osp.id,
        "changes_description": osp.changes_description or "",
        "pipeline_params_override": override,
        "accuracy": scores["accuracy"],
        "composite": scores.get("composite", scores["accuracy"]),
        "hits": scores["hits"],
        "total": scores["total"],
        "escalation_aborted": aborted,
        "elimination_stopped": elimination_stopped,
        "scored_queries": len(results),
        "expected_queries": len(dataset),
        **({"resumed_from_cache": True} if resumed_from_cache else {}),
    }


async def _score_candidates(
    osp_candidates: list[OptSearchPoint],
    merged_pp: list[dict | None],
    candidate_overrides: list[dict | None],
    dataset: list,
    ctx: ScoringEnv,
    *,
    degradation_checks: list | None = None,
    callbacks: RunCallbacks | None = None,
    elimination_n_min: int = 20,
    elimination_alpha: float = 0.05,
) -> tuple[dict[str, list[dict]], list[dict], dict | None]:
    """Evaluate each candidate against the dataset.

    Returns (all_candidate_results, candidate_scores, escalation_signal).
    """
    from promptpotter.services.scoring_searchpoint import score_search_point
    from promptpotter.services.search.sequential_elimination import EliminationCheck

    all_candidate_results: dict[str, list[dict]] = {}
    candidate_scores: list[dict] = []
    escalation_signal = None
    n_candidates = len(osp_candidates)

    elim_check = EliminationCheck(
        n_min=elimination_n_min,
        alpha=elimination_alpha,
        n_queries=len(dataset),
    )

    for idx, osp_c in enumerate(osp_candidates):
        _on_result = None
        if callbacks and callbacks.on_sample_scored:

            def _on_result(result, qi, qt, _ci=idx, _ct=n_candidates):
                callbacks.on_sample_scored(_ci, _ct, qi, qt, result)

        sp = osp_c.to_job_search_point(
            base_pipeline_params=merged_pp[idx],
            schema=ctx.pipeline_schema,
        )

        # Merge degradation + elimination checks for this candidate
        all_checks = list(degradation_checks or [])
        if elim_check.enabled:
            all_checks.append(elim_check)

        results, scores, was_cached = await score_search_point(
            sp,
            dataset,
            ctx,
            label=f"candidate_{idx}",
            on_result=_on_result,
            degradation_checks=all_checks or None,
            candidate_idx=idx,
            n_total_candidates=n_candidates,
        )

        if was_cached:
            logger.info(
                "Candidate %d/%d: full-run cache hit (%d queries) — skipped",
                idx + 1,
                n_candidates,
                len(results),
            )
            all_candidate_results[osp_c.id] = results
            elim_check.register_completed([r["score"] for r in results])

            report = _build_score_report(
                osp_c,
                candidate_overrides[idx],
                scores,
                results,
                dataset,
                resumed_from_cache=True,
            )
            candidate_scores.append(report)
            if callbacks and callbacks.on_candidate_scored:
                callbacks.on_candidate_scored(idx, n_candidates, report)
            continue

        escalation_signal = scores.pop("escalation_signal", None)
        elimination_stopped = (
            bool(escalation_signal)
            and getattr(escalation_signal, "check_name", "") == "elimination"
        )

        aborted = bool(escalation_signal) and len(results) < len(dataset)
        all_candidate_results[osp_c.id] = results

        # Register fully-completed candidates as priors for future elimination
        if len(results) == len(dataset) and not aborted:
            elim_check.register_completed([r["score"] for r in results])

        report = _build_score_report(
            osp_c,
            candidate_overrides[idx],
            scores,
            results,
            dataset,
            aborted=aborted,
            elimination_stopped=elimination_stopped,
        )
        candidate_scores.append(report)
        if callbacks and callbacks.on_candidate_scored:
            callbacks.on_candidate_scored(idx, n_candidates, report)

        if escalation_signal:
            if elimination_stopped:
                escalation_signal = None  # consumed — continue to next candidate
            else:
                break  # true degradation escalation — abort remaining candidates

    return all_candidate_results, candidate_scores, escalation_signal


async def l1_score(
    candidates: list[dict],
    dataset: list,
    current_best: dict[str, Any],
    ctx: ScoringEnv,
    *,
    pipeline_params: dict | None = None,
    improvement_threshold: float = 0.01,
    callbacks: RunCallbacks | None = None,
    degradation_checks: list | None = None,
    elimination_n_min: int = 20,
    elimination_alpha: float = 0.05,
) -> L1ScoringResult:
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
    all_candidate_results, candidate_scores, escalation_signal = await _score_candidates(
        osp_candidates,
        merged_pp,
        overrides,
        dataset,
        ctx,
        degradation_checks=degradation_checks,
        callbacks=callbacks,
        elimination_n_min=elimination_n_min,
        elimination_alpha=elimination_alpha,
    )

    evaluated_candidates = [
        c
        for c in osp_candidates
        if c.id in all_candidate_results
        and not any(
            cs.get("escalation_aborted")
            and not cs.get("elimination_stopped")
            and cs["candidate_id"] == c.id
            for cs in candidate_scores
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
    return L1ScoringResult(
        label=winner_entry["label"],
        winner_prompt_fields=winner_dict,
        winner_pipeline_params=winner_pp,
        winner_accuracy=winner_entry["accuracy"],
        winner_composite=winner_entry.get("composite", winner_entry["accuracy"]),
        hits=winner_entry["hits"],
        total=winner_entry["total"],
        improved=winner_entry["improved"],
        candidates_scored=winner_entry["candidates_scored"],
        candidate_scores=candidate_scores,
        winner_results=winner_entry.get("results", []),
        all_candidate_results=dict(all_candidate_results),
        escalation_signal=escalation_signal,
        degraded_queries=count_degraded_queries(winner_entry.get("results", [])),
    )
