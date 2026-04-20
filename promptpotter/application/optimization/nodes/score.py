"""L1 scoring — evaluate candidates against dataset, select round winner."""

from __future__ import annotations

import copy
import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from promptpotter.application.scoring.metrics import compute_composite_score, count_degraded_queries
from promptpotter.domain.analysis import (
    EscalationSignal,
    EscalationTarget,
    RuntimeFailure,
)
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.scoring import QueryResult
from promptpotter.infrastructure.tracing.events import CandidateScored, QueryScored
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.application.campaign.callbacks import RunListener
    from promptpotter.domain.scoring import ScoringEnv

logger = logging.getLogger(__name__)

__all__ = ["L1ScoringResult", "l1_score"]


class L1ScoringResult(BaseModel):
    """Structured return value from ``l1_score()``."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    label: str
    winner_osp: OptSearchPoint
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
    escalation_signal: EscalationSignal | None = None
    degraded_queries: int = 0
    # Named evaluator values for the winner — populated from the registry.
    winner_evaluators: dict[str, float] = Field(default_factory=dict)
    # Decision records produced during this L1 scoring pass (round_winner
    # and any elimination cuts fired). Flow through to RoundResult.decisions.
    decisions: list[dict] = Field(default_factory=list)

    # Populated post-eval by round_execution (critique phase)
    critique_text: str = ""
    thinking_styles: list[str] = Field(default_factory=list)


def _select_round_winner(
    candidates: list[OptSearchPoint],
    all_candidate_results: dict[str, list[QueryResult]],
    current_best: dict[str, Any],
    improvement_threshold: float,
    pipeline_schema: PipelineSchema | None = None,
    round_scorer: Any = None,
) -> dict[str, Any]:
    """Compare candidates and select the round winner.

    Winner selection is driven by ``accuracy`` (the user's performance
    scoring function). ``composite`` is a recorded multi-signal diagnostic
    — see ``compute_pipeline_metrics`` — but it does not gate the loop.
    """
    current_acc = current_best["accuracy"]
    current_composite = current_best.get("composite", current_acc)

    assert pipeline_schema is not None, "_select_round_winner requires pipeline_schema"

    # Score each candidate once, reuse for selection and display. Thread
    # the candidate's OptSearchPoint so the composite can fold in its
    # OptSP-layer signals (runtime/validation failures), and the
    # round_scorer so composite uses the dataset's configured formula.
    candidate_scores = {
        c.id: compute_composite_score(
            all_candidate_results[c.id],
            pipeline_schema,
            opt_sp=c,
            round_scorer=round_scorer,
        )
        for c in candidates
    }

    # Find best candidate — compare on accuracy, keep composite for display.
    best_composite = current_composite
    best_acc = current_acc
    best_ps: OptSearchPoint = current_best["prompt_fields"]
    best_results = current_best["results"]
    best_label = current_best["label"]
    best_evaluators: dict[str, float] = current_best.get("evaluators") or {}
    winner_idx: int | None = None

    for idx, candidate in enumerate(candidates):
        c_scores = candidate_scores[candidate.id]
        c_acc = c_scores["accuracy"]
        if c_acc > best_acc:
            best_acc = c_acc
            best_composite = c_scores["composite"]
            best_ps = candidate
            best_results = all_candidate_results[candidate.id]
            best_label = candidate.changes_description or candidate.id[:12]
            best_evaluators = dict(c_scores.get("evaluators") or {})
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
        "improved": best_acc > current_acc + improvement_threshold,
        "winner_idx": winner_idx,
        "evaluators": best_evaluators,
    }


def _merge_pipeline_params(
    base: dict | None,
    overrides: dict | None,
    schema: PipelineSchema | None,
) -> dict | None:
    """Deep-merge ``overrides`` into ``base``; drop overrides for nodes outside active steps."""
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

    Validates node_overrides against allowed-value sets (one producer of
    truth — no ``__validation_failures__`` round-trip through the wire
    dict). Failures attach to ``osp.memory.validation_failures`` and drive
    the synthetic-0 short-circuit in ``_score_candidates``.

    Returns (osp_candidates, merged_pipeline_params, raw_overrides).
    """
    from promptpotter.application.optimization.nodes.generate import validate_overrides

    overrides: list[dict | None] = []
    osp_list: list[OptSearchPoint] = []
    merged: list[dict | None] = []
    for c in candidates:
        override = c.get("__pipeline_params_override__")
        overrides.append(override)
        osp = OptSearchPoint.from_prompt_fields(
            {k: v for k, v in c.items() if k != "__pipeline_params_override__"},
        )
        if schema and override:
            failures = validate_overrides(override, schema)
            if failures:
                osp.memory.validation_failures = failures
                for vf in failures:
                    logger.warning(
                        "candidate %s: validation failure on %s — proposed %r not in allowed %r",
                        osp.id[:8],
                        vf.axis,
                        vf.value,
                        vf.allowed,
                    )
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
    elimination_context: dict | None = None,
    resumed_from_cache: bool = False,
    invalid: bool = False,
    new_runtime_failure: RuntimeFailure | None = None,
) -> dict:
    """Build unified candidate score report dict.

    Validation failures are read from ``osp.memory.validation_failures`` —
    the single source of truth. ``new_runtime_failure`` is this-round-only
    (the outer round accumulates across rounds). ``elimination_context``
    is a display-only sidecar populated from ``signal.check_result`` when
    the Wilcoxon elimination triggers; the authoritative archive lives in
    the trial JSON ``elimination_cut`` decision record.
    """
    vfs = osp.memory.validation_failures
    return {
        "candidate_id": osp.id,
        "changes_description": osp.changes_description or "",
        "pipeline_params_override": override,
        "accuracy": scores["accuracy"],
        "composite": scores.get("composite", scores["accuracy"]),
        "hits": scores["hits"],
        "total": scores["total"],
        "evaluators": dict(scores.get("evaluators") or {}),
        "escalation_aborted": aborted,
        "elimination_stopped": elimination_stopped,
        "scored_queries": len(results),
        "expected_queries": len(dataset),
        "invalid": invalid,
        **({"validation_failures": [vf.to_dict() for vf in vfs]} if vfs else {}),
        **({"runtime_failures": [new_runtime_failure.to_dict()]} if new_runtime_failure else {}),
        **({"resumed_from_cache": True} if resumed_from_cache else {}),
        **({"elimination_context": elimination_context} if elimination_context else {}),
    }


def _handle_validation_skip(
    osp_c: OptSearchPoint,
    override: dict | None,
    dataset: list,
    idx: int,
    n_candidates: int,
) -> tuple[list[QueryResult], dict]:
    """Synthetic-0 short-circuit for structurally invalid candidates.

    A SearchPoint whose L1 parse-time validation produced ValidationFailures
    does not run through the backend. We synthesize a 0-accuracy report and
    let the existing accuracy comparator deprioritize it. See
    docs/architecture/optimization.md.
    """
    logger.warning(
        "Candidate %d/%d invalid (%d validation failure(s)) — skipping backend",
        idx + 1,
        n_candidates,
        len(osp_c.memory.validation_failures),
    )
    results: list[QueryResult] = []
    scores: dict[str, Any] = {
        "accuracy": 0.0,
        "composite": 0.0,
        "hits": 0,
        "total": 0,
        "errors": 0,
        "invalid": True,
    }
    report = _build_score_report(osp_c, override, scores, results, dataset, invalid=True)
    return results, report


def _handle_cache_hit(
    osp_c: OptSearchPoint,
    override: dict | None,
    results: list[QueryResult],
    scores: dict,
    dataset: list,
    elim_check: Any,
    idx: int,
    n_candidates: int,
) -> dict:
    """Full-run cache hit — register with elim_check and build replay report."""
    logger.info(
        "Candidate %d/%d: full-run cache hit (%d queries) — skipped",
        idx + 1,
        n_candidates,
        len(results),
    )
    elim_check.register_completed([r.get("score", 0.0) for r in results], candidate_id=osp_c.id)
    return _build_score_report(osp_c, override, scores, results, dataset, resumed_from_cache=True)


def _handle_scored_candidate(
    osp_c: OptSearchPoint,
    override: dict | None,
    results: list[QueryResult],
    scores: dict,
    signal: EscalationSignal | None,
    merged_pp_i: dict | None,
    dataset: list,
    elim_check: Any,
    idx: int,
    n_candidates: int,
) -> tuple[dict, EscalationSignal | None]:
    """Build report for a fully-scored candidate; attach RuntimeFailure on elimination.

    Returns ``(report, residual_signal)``. Residual is ``None`` when
    elimination was consumed (outer loop should continue); the signal itself
    when true degradation should abort remaining candidates.

    Self-healing (Rail 2): a degradation-elimination signal is converted to a
    RuntimeFailure and appended in-place to ``osp_c.memory.runtime_failures``.
    The failure is a property of the candidate that produced it, not a
    round-level event. L2 reads it from candidate_scores next round.
    """
    elimination_stopped = (
        signal is not None and signal.target == EscalationTarget.ELIMINATE_CANDIDATE
    )
    scoring_error_abort = signal is not None and signal.check_name == "scoring_error_abort"
    aborted = bool(signal) and (scoring_error_abort or len(results) < len(dataset))

    # Register fully-completed candidates as priors for future elimination.
    # Error rows carry no ``score`` (see ``rescore_results``); treat as 0.0,
    # matching the ``_compute_accuracy`` convention. Scoring-error aborts are
    # padded to full length but must NOT seed priors — their per-query scores
    # are synthetic 0s from errors, not a real distribution.
    if len(results) == len(dataset) and not aborted:
        elim_check.register_completed([r.get("score", 0.0) for r in results], candidate_id=osp_c.id)

    new_rf: RuntimeFailure | None = None
    if elimination_stopped and signal is not None and signal.check_name == "degradation":
        cr = signal.check_result
        dominant = cr.get("dominant_warning", "unknown:unknown")
        problem_node = dominant.split(":")[0] if ":" in dominant else ""
        observed_node_cfg = (merged_pp_i or {}).get(problem_node, {}) or {}
        new_rf = RuntimeFailure(
            source="degradation_check",
            dominant_warning=dominant,
            warning_types=dict(cr.get("warning_types") or {}),
            degraded_rate=float(cr.get("degraded_rate", 0.0)),
            degraded_count=int(cr.get("degraded_count", 0)),
            total_evaluated=int(cr.get("total_evaluated", len(results))),
            observed_config=dict(observed_node_cfg),
        )
        osp_c.memory.runtime_failures = [*osp_c.memory.runtime_failures, new_rf]
        logger.info(
            "Candidate %d/%d eliminated — RuntimeFailure attached (%s, rate=%.0f%%, config=%s)",
            idx + 1,
            n_candidates,
            dominant,
            new_rf.degraded_rate * 100,
            observed_node_cfg,
        )
    elif scoring_error_abort and signal is not None:
        # Rail 2: a consecutive-error abort (e.g. backend rejected params →
        # 502 cascade) attaches to the candidate that produced it, not the
        # round. L2 reads this next round to avoid proposing the bad config.
        cr = signal.check_result
        degraded_count = int(cr.get("degraded_count", 0))
        total_evaluated = int(cr.get("total_evaluated", len(results)))
        new_rf = RuntimeFailure(
            source="scoring_error_abort",
            dominant_warning=str(cr.get("dominant_warning") or "scoring_error"),
            warning_types=dict(cr.get("warning_types") or {}),
            degraded_rate=(degraded_count / total_evaluated) if total_evaluated else 0.0,
            degraded_count=degraded_count,
            total_evaluated=total_evaluated,
            observed_config=dict(merged_pp_i or {}),
        )
        osp_c.memory.runtime_failures = [*osp_c.memory.runtime_failures, new_rf]
        logger.info(
            "Candidate %d/%d scoring-error abort — RuntimeFailure attached (%s, %d/%d errors)",
            idx + 1,
            n_candidates,
            new_rf.dominant_warning,
            new_rf.degraded_count,
            new_rf.total_evaluated,
        )

    elim_ctx: dict | None = None
    if elimination_stopped and signal is not None and signal.check_name == "elimination":
        cr = signal.check_result
        elim_ctx = {
            "triggered_p": float(cr.get("triggered_p", 1.0)),
            "triggered_by_prior_idx": int(cr.get("triggered_by_prior", -1)),
            "queries_evaluated": int(cr.get("queries_evaluated", len(results))),
            "total_queries": int(cr.get("total_queries", len(dataset))),
            "n_priors": int(cr.get("n_priors", 0)),
        }

    report = _build_score_report(
        osp_c,
        override,
        scores,
        results,
        dataset,
        aborted=aborted,
        elimination_stopped=elimination_stopped,
        elimination_context=elim_ctx,
        new_runtime_failure=new_rf,
    )
    residual = None if (elimination_stopped or not signal) else signal
    return report, residual


async def _score_candidates(
    osp_candidates: list[OptSearchPoint],
    merged_pp: list[dict | None],
    candidate_overrides: list[dict | None],
    dataset: list,
    ctx: ScoringEnv,
    *,
    degradation_checks: list | None = None,
    callbacks: RunListener,
    elimination_n_min: int = 4,
    elimination_alpha: float = 0.2,
    obs_campaign_id: str = "",
    round_num: int = 0,
    decisions: list[dict] | None = None,
) -> tuple[dict[str, list[QueryResult]], list[dict], EscalationSignal | None]:
    """Evaluate each candidate against the dataset.

    Flat dispatch over three exit paths — validation-skip, cache-hit,
    scored — each extracted into its own ``_handle_*`` helper.

    ``decisions`` (when provided) accumulates ``elimination_cut`` decision
    records — one per eliminated candidate — for divergence replay.

    Returns ``(all_candidate_results, candidate_scores, escalation_signal)``.
    """
    from promptpotter.application.campaign.decisions import record_decision
    from promptpotter.application.optimization.elimination import EliminationCheck
    from promptpotter.application.scoring.search_point_scorer import score_search_point

    all_candidate_results: dict[str, list[QueryResult]] = {}
    candidate_scores: list[dict] = []
    escalation_signal: EscalationSignal | None = None
    n_candidates = len(osp_candidates)

    elim_check = EliminationCheck(
        n_min=elimination_n_min,
        alpha=elimination_alpha,
        n_queries=len(dataset),
    )

    obs = ctx.obs

    def _emit_candidate_scored(c_idx: int, c_report: dict) -> None:
        if obs:
            with graceful("CandidateScored emit failed"):
                obs.emit_write_point(
                    CandidateScored,
                    campaign_id=obs_campaign_id,
                    round_num=round_num,
                    candidate_idx=c_idx,
                    report=c_report,
                )

    def _fire_candidate_callbacks(c_idx: int, c_report: dict) -> None:
        candidate_scores.append(c_report)
        callbacks.on_candidate_scored(c_idx, n_candidates, c_report)
        _emit_candidate_scored(c_idx, c_report)

    for idx, osp_c in enumerate(osp_candidates):

        def _on_start(query_text, qi, qt, _ci=idx, _ct=n_candidates):
            callbacks.on_sample_started(_ci, _ct, qi, qt, query_text)

        def _on_result(result, qi, qt, _ci=idx, _ct=n_candidates):
            callbacks.on_sample_scored(_ci, _ct, qi, qt, result)
            if obs:
                with graceful("QueryScored emit failed"):
                    obs.emit_write_point(
                        QueryScored,
                        campaign_id=obs_campaign_id,
                        round_num=round_num,
                        candidate_idx=_ci,
                        query_idx=qi,
                        hit=bool(result.get("hit", False)),
                        score=float(result.get("score", 0.0)),
                    )

        override = candidate_overrides[idx]

        # Pre-scoring header — one line naming the mutation under test.
        # Fires for all three paths (validation-skip / cache-hit / scored)
        # so the display shows what was tested even when no backend call ran.
        callbacks.on_candidate_started(idx, n_candidates, osp_c.changes_description or "", override)

        # Path 1 — validation-skip synthetic-0 (zero backend calls)
        if osp_c.memory.validation_failures:
            results, report = _handle_validation_skip(osp_c, override, dataset, idx, n_candidates)
            all_candidate_results[osp_c.id] = results
            _fire_candidate_callbacks(idx, report)
            continue

        sp = osp_c.to_job_search_point(
            base_pipeline_params=merged_pp[idx],
            schema=ctx.pipeline_schema,
        )

        all_checks = list(degradation_checks or [])
        if elim_check.enabled:
            all_checks.append(elim_check)

        results, scores, was_cached, signal = await score_search_point(
            sp,
            dataset,
            ctx,
            label=f"candidate_{idx}",
            on_result=_on_result,
            on_start=_on_start,
            degradation_checks=all_checks or None,
            candidate_idx=idx,
            n_total_candidates=n_candidates,
        )
        all_candidate_results[osp_c.id] = results

        # Path 2 — full-run cache replay
        if was_cached:
            report = _handle_cache_hit(
                osp_c, override, results, scores, dataset, elim_check, idx, n_candidates
            )
            _fire_candidate_callbacks(idx, report)
            continue

        # Path 3 — scored (may be eliminated or aborted)
        # Snapshot priors BEFORE the helper registers this candidate so
        # the decision record points to the exact priors the t-test saw.
        priors_at_test = elim_check.prior_ids_snapshot()
        report, residual = _handle_scored_candidate(
            osp_c,
            override,
            results,
            scores,
            signal,
            merged_pp[idx],
            dataset,
            elim_check,
            idx,
            n_candidates,
        )
        if (
            signal is not None
            and signal.target == EscalationTarget.ELIMINATE_CANDIDATE
            and signal.check_name == elim_check.name
        ):
            cr = signal.check_result
            trigger_idx = int(cr.get("triggered_by_prior", -1))
            if 0 <= trigger_idx < len(priors_at_test):
                prior_id = priors_at_test[trigger_idx]
                prior_label = next(
                    (
                        f"C{i + 1}"
                        for i, r in enumerate(candidate_scores)
                        if r.get("candidate_id") == prior_id
                    ),
                    None,
                )
                if prior_label and isinstance(report.get("elimination_context"), dict):
                    report["elimination_context"]["triggered_by_prior_label"] = prior_label

            if decisions is not None:
                record_decision(
                    decisions,
                    "elimination_cut",
                    {
                        "candidate_id": osp_c.id,
                        "prior_candidate_ids": priors_at_test,
                        "queries_evaluated": int(cr.get("queries_evaluated", len(results))),
                        "alpha": float(elim_check.alpha),
                        "n_min": int(elim_check.n_min),
                        "round_num": round_num,
                    },
                    True,
                    data={
                        "triggered_p": float(cr.get("triggered_p", 0.0)),
                        "triggered_by_prior": trigger_idx,
                    },
                )
        _fire_candidate_callbacks(idx, report)

        if residual is not None:
            escalation_signal = residual
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
    callbacks: RunListener,
    degradation_checks: list | None = None,
    elimination_n_min: int = 4,
    elimination_alpha: float = 0.2,
    obs_campaign_id: str = "",
    round_num: int = 0,
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
    decisions: list[dict] = []
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
        obs_campaign_id=obs_campaign_id,
        round_num=round_num,
        decisions=decisions,
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
        round_scorer=ctx.round_scorer,
    )

    # Record the winner-selection decision for divergence replay. Pointers
    # only — replayer re-derives the beat-threshold from the prior trial's
    # rescored winner results (or the rescored baseline for round 0), and
    # per-candidate accuracies from the rescored ``all_candidate_results``.
    # The recorded threshold lives in ``data=`` as a forensic anchor only.
    # ``decisions`` may already contain elimination_cut records from
    # ``_score_candidates``.
    from promptpotter.application.campaign.decisions import record_decision

    w_idx = winner_entry["winner_idx"]
    winner_id = evaluated_candidates[w_idx].id if w_idx is not None and evaluated_candidates else ""
    record_decision(
        decisions,
        "round_winner",
        {
            "candidate_ids": [c.id for c in evaluated_candidates],
            "round_num": round_num,
        },
        winner_id,
        data={"current_best_accuracy_at_record": cb["accuracy"]},
    )

    # Reuse pre-computed merged params for winner (no re-merge needed)
    winner_pp = merged_pp[w_idx] if w_idx is not None else pipeline_params

    winner_osp: OptSearchPoint = winner_entry["prompt_fields"]
    winner_dict = winner_osp.prompt_field_dict()
    winner_dict["id"] = winner_osp.id
    winner_dict["parent_id"] = winner_osp.parent_id
    winner_dict["changes_description"] = winner_osp.changes_description
    return L1ScoringResult(
        label=winner_entry["label"],
        winner_osp=winner_osp,
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
        winner_evaluators=dict(winner_entry.get("evaluators") or {}),
        decisions=decisions,
    )
