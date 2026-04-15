"""L1 scoring — evaluate candidates against dataset, select round winner."""

from __future__ import annotations

import copy
import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from promptpotter.application.scoring.metrics import compute_composite_score, count_degraded_queries
from promptpotter.domain.analysis import EscalationTarget, RuntimeFailure, ValidationFailure
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.scoring import QueryResult

if TYPE_CHECKING:
    from promptpotter.application.campaign.callbacks import RunCallbacks
    from promptpotter.domain.scoring import ScoringEnv

logger = logging.getLogger(__name__)

__all__ = ["L1ScoringResult", "l1_score"]


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
    all_candidate_results: dict[str, list[QueryResult]] = Field(default_factory=dict)
    escalation_signal: dict[str, Any] | None = None
    degraded_queries: int = 0

    # Populated post-eval by round_execution (critique phase)
    critique_text: str = ""
    thinking_styles: list[str] = Field(default_factory=list)


def _select_round_winner(
    candidates: list[OptSearchPoint],
    all_candidate_results: dict[str, list[QueryResult]],
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
        c.id: compute_composite_score(all_candidate_results[c.id], pipeline_schema)
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
    _meta_keys = {"__pipeline_params_override__", "__validation_failures__"}
    for c in candidates:
        override = c.get("__pipeline_params_override__")
        overrides.append(override)
        osp = OptSearchPoint.from_prompt_fields(
            {k: v for k, v in c.items() if k not in _meta_keys},
        )
        # Re-attach parse-time validation failures so the scorer can short-
        # circuit invalid candidates. Failures are produced by validate_overrides()
        # in nodes/generate.py and ride through the candidate dict so they survive
        # the prompt_field round trip.
        raw_failures = c.get("__validation_failures__") or []
        if raw_failures:
            osp.memory.validation_failures = [
                ValidationFailure(**f) if isinstance(f, dict) else f for f in raw_failures
            ]
        osp_list.append(osp)
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
    invalid: bool = False,
    validation_failures: list[dict] | None = None,
    runtime_failures: list[dict] | None = None,
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
        "invalid": invalid,
        **({"validation_failures": validation_failures} if validation_failures else {}),
        **({"runtime_failures": runtime_failures} if runtime_failures else {}),
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
    callbacks: RunCallbacks,
    elimination_n_min: int = 4,
    elimination_alpha: float = 0.2,
) -> tuple[dict[str, list[QueryResult]], list[dict], dict | None]:
    """Evaluate each candidate against the dataset.

    Returns (all_candidate_results, candidate_scores, escalation_signal).
    """
    from promptpotter.application.optimization.elimination import EliminationCheck
    from promptpotter.application.scoring.search_point_scorer import score_search_point

    all_candidate_results: dict[str, list[QueryResult]] = {}
    candidate_scores: list[dict] = []
    escalation_signal: dict | None = None
    n_candidates = len(osp_candidates)

    elim_check = EliminationCheck(
        n_min=elimination_n_min,
        alpha=elimination_alpha,
        n_queries=len(dataset),
    )

    for idx, osp_c in enumerate(osp_candidates):

        def _on_result(result, qi, qt, _ci=idx, _ct=n_candidates):
            callbacks.on_sample_scored(_ci, _ct, qi, qt, result)

        # Synthetic-0 early exit: a SearchPoint whose L1 parse-time validation
        # produced ValidationFailures is structurally invalid. We do not run it
        # through the backend at all — we synthesize a 0-accuracy report and
        # let the existing accuracy comparator deprioritize it. See
        # docs/architecture/optimization.md.
        if osp_c.memory.validation_failures:
            failures = osp_c.memory.validation_failures
            logger.warning(
                "Candidate %d/%d invalid (%d validation failure(s)) — skipping backend",
                idx + 1,
                n_candidates,
                len(failures),
            )
            results: list[QueryResult] = []
            scores: dict[str, Any] = {
                "accuracy": 0.0,
                "composite": 0.0,
                "hits": 0,
                "total": 0,
                "errors": 0,
                "invalid": True,
                "validation_failures": [vf.to_dict() for vf in failures],
            }
            all_candidate_results[osp_c.id] = results
            report = _build_score_report(
                osp_c,
                candidate_overrides[idx],
                scores,
                results,
                dataset,
                invalid=True,
                validation_failures=[vf.to_dict() for vf in failures],
            )
            candidate_scores.append(report)
            callbacks.on_candidate_scored(idx, n_candidates, report)
            continue

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
            callbacks.on_candidate_scored(idx, n_candidates, report)
            continue

        escalation_signal = scores.pop("escalation_signal", None)
        elimination_stopped = (
            escalation_signal is not None
            and escalation_signal["target"] == EscalationTarget.ELIMINATE_CANDIDATE
        )

        aborted = bool(escalation_signal) and len(results) < len(dataset)
        all_candidate_results[osp_c.id] = results

        # Register fully-completed candidates as priors for future elimination
        if len(results) == len(dataset) and not aborted:
            elim_check.register_completed([r["score"] for r in results])

        # Self-healing: convert a degradation elimination signal into a
        # RuntimeFailure attached to THIS candidate's memory. Mirrors the
        # ValidationFailure rail — the failure is a property of the
        # candidate that produced it, not a round-level event. L2 reads it
        # from candidate_scores next round and produces a directive that
        # names the disallowed value range.
        runtime_failures_dicts: list[dict] | None = None
        if (
            elimination_stopped
            and escalation_signal is not None
            and escalation_signal["check_name"] == "degradation"
        ):
            cr = escalation_signal["check_result"]
            dominant = cr.get("dominant_warning", "unknown:unknown")
            problem_node = dominant.split(":")[0] if ":" in dominant else ""
            observed_node_cfg = (merged_pp[idx] or {}).get(problem_node, {}) or {}
            rf = RuntimeFailure(
                source="degradation_check",
                dominant_warning=dominant,
                warning_types=dict(cr.get("warning_types") or {}),
                degraded_rate=float(cr.get("degraded_rate", 0.0)),
                degraded_count=int(cr.get("degraded_count", 0)),
                total_evaluated=int(cr.get("total_evaluated", len(results))),
                observed_config=dict(observed_node_cfg),
            )
            osp_c.memory.runtime_failures = [*osp_c.memory.runtime_failures, rf]
            runtime_failures_dicts = [rf.to_dict()]
            logger.info(
                "Candidate %d/%d eliminated — RuntimeFailure attached (%s, rate=%.0f%%, config=%s)",
                idx + 1,
                n_candidates,
                dominant,
                rf.degraded_rate * 100,
                observed_node_cfg,
            )

        report = _build_score_report(
            osp_c,
            candidate_overrides[idx],
            scores,
            results,
            dataset,
            aborted=aborted,
            elimination_stopped=elimination_stopped,
            runtime_failures=runtime_failures_dicts,
        )
        candidate_scores.append(report)
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
    callbacks: RunCallbacks,
    degradation_checks: list | None = None,
    elimination_n_min: int = 4,
    elimination_alpha: float = 0.2,
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
