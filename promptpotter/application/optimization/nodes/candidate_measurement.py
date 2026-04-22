"""Candidate measurement — dispatches each candidate over three exit paths (validation-skip / cache-hit / scored). See ``docs/architecture/optimization.md`` for the self-healing contract."""

from __future__ import annotations

import copy
import logging
from typing import TYPE_CHECKING, Any

from promptpotter.domain.analysis import (
    EscalationSignal,
    EscalationTarget,
    RuntimeFailure,
)
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.scoring import QueryResult
from promptpotter.infrastructure.tracing.events import CandidateScored
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.application.campaign.callbacks import RunListener
    from promptpotter.application.optimization.cycle import Cycle

logger = logging.getLogger(__name__)

__all__ = ["parse_candidates", "score_candidates"]


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


def parse_candidates(
    candidates: list[dict],
    pipeline_params: dict | None,
    schema: PipelineSchema | None,
) -> tuple[list[OptSearchPoint], list[dict | None], list[dict | None]]:
    """Normalize raw candidates → OptSearchPoints + merged pp; attaches validation failures."""
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
    """Build unified candidate score report dict."""
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
) -> tuple[list[QueryResult], dict]:
    """Synthetic-0 short-circuit for invalid candidates — no backend call."""
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
) -> dict:
    """Full-run cache hit — register with elim_check and build replay report."""
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
    round_num: int,
) -> tuple[dict, EscalationSignal | None]:
    """Build report for a scored candidate; attach RuntimeFailure on elimination (Rail 2)."""
    elimination_stopped = (
        signal is not None and signal.target == EscalationTarget.ELIMINATE_CANDIDATE
    )
    scoring_error_abort = signal is not None and signal.check_name == "scoring_error_abort"
    aborted = bool(signal) and (scoring_error_abort or len(results) < len(dataset))

    # Aborted candidates must NOT seed priors — their scores are synthetic 0s.
    if len(results) == len(dataset) and not aborted:
        elim_check.register_completed([r.get("score", 0.0) for r in results], candidate_id=osp_c.id)

    new_rf: RuntimeFailure | None = None
    candidate_label = osp_c.changes_description or ""
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
            first_seen_round=round_num,
            candidate_label=candidate_label,
        )
    elif scoring_error_abort and signal is not None:
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
            first_seen_round=round_num,
            candidate_label=candidate_label,
        )
    if new_rf is not None:
        osp_c.memory.runtime_failures = [*osp_c.memory.runtime_failures, new_rf]

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


def _record_elimination_cut(
    signal: EscalationSignal,
    osp_c: OptSearchPoint,
    elim_check: Any,
    priors_at_test: list[str],
    candidate_scores: list[dict],
    report: dict,
    decisions: list[dict] | None,
    round_num: int,
    n_results: int,
) -> None:
    """Decorate report + append elimination_cut decision for divergence replay."""
    from promptpotter.application.campaign.decisions import record_decision

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
                "queries_evaluated": int(cr.get("queries_evaluated", n_results)),
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


async def score_candidates(
    cycle: Cycle,
    osp_candidates: list[OptSearchPoint],
    merged_pp: list[dict | None],
    candidate_overrides: list[dict | None],
    dataset: list,
    *,
    degradation_checks: list | None = None,
    callbacks: RunListener,
    elimination_n_min: int = 4,
    elimination_alpha: float = 0.2,
    round_num: int = 0,
    decisions: list[dict] | None = None,
) -> tuple[dict[str, list[QueryResult]], list[dict], EscalationSignal | None]:
    """Evaluate each candidate; dispatch over three exit paths (validation/cache/scored)."""
    from promptpotter.application.optimization.elimination import EliminationCheck
    from promptpotter.application.scoring.search_point_scorer import score_search_point

    session = cycle.session
    search_memory = cycle.search_memory
    obs_campaign_id = session.obs_campaign_id

    all_candidate_results: dict[str, list[QueryResult]] = {}
    candidate_scores: list[dict] = []
    escalation_signal: EscalationSignal | None = None
    n_candidates = len(osp_candidates)
    obs = session.obs

    elim_check = EliminationCheck(
        n_min=elimination_n_min,
        alpha=elimination_alpha,
        n_queries=len(dataset),
    )

    def _fire(idx: int, report: dict) -> None:
        candidate_scores.append(report)
        callbacks.on_candidate_scored(idx, n_candidates, report)
        if obs:
            with graceful("CandidateScored emit failed"):
                obs.emit_write_point(
                    CandidateScored,
                    campaign_id=obs_campaign_id,
                    round_num=round_num,
                    candidate_idx=idx,
                    report=report,
                )

    for idx, osp_c in enumerate(osp_candidates):
        override = candidate_overrides[idx]
        callbacks.on_candidate_started(idx, n_candidates, osp_c.changes_description or "", override)

        # Path 1 — validation-skip synthetic-0.
        if osp_c.memory.validation_failures:
            results, report = _handle_validation_skip(osp_c, override, dataset)
            all_candidate_results[osp_c.id] = results
            _fire(idx, report)
            continue

        sp = osp_c.to_job_search_point(
            base_pipeline_params=merged_pp[idx],
            schema=session.pipeline_schema,
        )

        all_checks = list(degradation_checks or [])
        if elim_check.enabled:
            all_checks.append(elim_check)

        def _on_result(r, qi, qt, _ci=idx):
            callbacks.on_sample_scored(_ci, n_candidates, qi, qt, r)

        def _on_start(qtxt, qi, qt, _ci=idx):
            callbacks.on_sample_started(_ci, n_candidates, qi, qt, qtxt)

        results, scores, was_cached, signal = await score_search_point(
            sp,
            dataset,
            session,
            label=f"candidate_{idx}",
            on_result=_on_result,
            on_start=_on_start,
            degradation_checks=all_checks or None,
            candidate_idx=idx,
            n_total_candidates=n_candidates,
            search_memory=search_memory,
        )
        all_candidate_results[osp_c.id] = results

        # Path 2 — full-run cache replay
        if was_cached:
            _fire(idx, _handle_cache_hit(osp_c, override, results, scores, dataset, elim_check))
            continue

        # Path 3 — scored. Snapshot priors BEFORE helper registers this candidate.
        priors_at_test = elim_check.prior_ids_snapshot()
        report, residual = _handle_scored_candidate(
            osp_c, override, results, scores, signal, merged_pp[idx], dataset, elim_check, round_num
        )
        if (
            signal is not None
            and signal.target == EscalationTarget.ELIMINATE_CANDIDATE
            and signal.check_name == elim_check.name
        ):
            _record_elimination_cut(
                signal,
                osp_c,
                elim_check,
                priors_at_test,
                candidate_scores,
                report,
                decisions,
                round_num,
                len(results),
            )
        _fire(idx, report)

        if residual is not None:
            escalation_signal = residual
            break  # true degradation — abort remaining candidates

    return all_candidate_results, candidate_scores, escalation_signal
