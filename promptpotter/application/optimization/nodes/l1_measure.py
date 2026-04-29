"""Candidate measurement — dispatches each candidate over three exit paths (validation-skip / cache-hit / scored). See ``docs/developer/self-healing-internals.md`` for the self-healing contract."""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.application.optimization.cycle import Decision, record_decision
from promptpotter.application.optimization.elimination import EliminationCheck
from promptpotter.application.optimization.nodes.l1_generate import validate_overrides
from promptpotter.application.scoring.search_point_scorer import score_search_point
from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.domain.analysis import (
    EscalationSignal,
    EscalationTarget,
    RuntimeFailure,
    ValidationFailure,
)
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.results import CandidateProposal, CandidateScore
from promptpotter.domain.scoring import QueryResult
from promptpotter.infrastructure.tracing.events import CandidateScored
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.application.campaign.runner import RunListener
    from promptpotter.application.optimization.cycle import Cycle

logger = logging.getLogger(__name__)

__all__ = ["L1YieldStats", "detect_invariants", "parse_population", "score_population"]


@dataclass(frozen=True)
class L1YieldStats:
    """Round-level L1 generation quality, computed from per-candidate signatures."""

    yield_: float  # n_valid / n_proposed (1.0 when no proposals)
    n_no_op: int
    n_duplicate: int


_INVARIANT_REASONS = frozenset({"no_op_variant", "duplicate_variant"})


def detect_invariants(
    proposals: list[CandidateProposal], parent_osp: OptSearchPoint
) -> L1YieldStats:
    """Attach ``ValidationFailure`` to no-op / duplicate variants; return round-level stats.

    A candidate is a *no-op* when its mutation signature vs ``parent_osp`` is
    empty (every prompt field, task-context entry, and node-override matches
    parent). A *duplicate* signature equals one already seen earlier in the
    batch. Failures attach to ``cp.osp.validation_failures`` so the existing
    synthetic-0 path (``score_population`` Path 1) skips scoring with zero
    LLM cost; L2 ingests them next round (Rail 1) and the resulting
    ``l2_directive`` teaches L1 to diversify.

    Idempotent: pre-existing ``no_op_variant`` / ``duplicate_variant``
    entries are dropped before the pass so resume-from-disk doesn't
    accumulate duplicates.
    """
    for cp in proposals:
        cp.osp.validation_failures = [
            vf for vf in cp.osp.validation_failures if vf.reason not in _INVARIANT_REASONS
        ]
    seen: dict[tuple, int] = {}
    n_no_op = 0
    n_duplicate = 0
    parent_tc = parent_osp.task_context.to_dict()
    for i, cp in enumerate(proposals):
        child = cp.osp
        pf_delta = tuple(
            (f, getattr(child, f))
            for f in PROMPT_STRING_FIELDS
            if getattr(child, f) != getattr(parent_osp, f)
        )
        child_tc = child.task_context.to_dict()
        tc_delta = tuple(sorted((k, v) for k, v in child_tc.items() if v != parent_tc.get(k)))
        no_canon = tuple(
            sorted((n, tuple(sorted(p.items()))) for n, p in (cp.node_overrides or {}).items() if p)
        )
        sig = (pf_delta, tc_delta, no_canon)
        if not any(sig):
            cp.osp.validation_failures = [
                *cp.osp.validation_failures,
                ValidationFailure(
                    axis="variant",
                    value="(no mutation)",
                    allowed=["non-empty mutation"],
                    reason="no_op_variant",
                ),
            ]
            n_no_op += 1
            continue
        if sig in seen:
            twin = seen[sig]
            cp.osp.validation_failures = [
                *cp.osp.validation_failures,
                ValidationFailure(
                    axis="variant",
                    value=f"duplicate of C{twin + 1}",
                    allowed=["unique mutation"],
                    reason="duplicate_variant",
                ),
            ]
            n_duplicate += 1
            continue
        seen[sig] = i
    n = len(proposals)
    yield_ = (n - n_no_op - n_duplicate) / n if n else 1.0
    return L1YieldStats(yield_=yield_, n_no_op=n_no_op, n_duplicate=n_duplicate)


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


def parse_population(
    proposals: list[CandidateProposal],
    pipeline_params: dict | None,
    schema: PipelineSchema | None,
) -> tuple[list[OptSearchPoint], list[dict | None]]:
    """Project proposals → OptSearchPoints + merged pp; attaches validation failures."""
    osp_list: list[OptSearchPoint] = []
    merged: list[dict | None] = []
    for cp in proposals:
        override = cp.node_overrides or None
        osp = cp.osp
        if schema and override:
            failures = validate_overrides(override, schema)
            if failures:
                osp.validation_failures = failures
                for vf in failures:
                    logger.warning(
                        "candidate %s: validation failure on %s — proposed %r not in allowed %r",
                        osp.lineage.id[:8],
                        vf.axis,
                        vf.value,
                        vf.allowed,
                    )
        osp_list.append(osp)
        merged.append(_merge_pipeline_params(pipeline_params, override, schema))
    return osp_list, merged


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
    l1_diversity: float = 1.0,
) -> CandidateScore:
    """Build the typed candidate score report — stable shape, defaults always present."""
    evaluators = {**(scores.get("evaluators") or {}), "l1_diversity": l1_diversity}
    return CandidateScore(
        candidate_id=osp.lineage.id,
        changes_description=osp.lineage.changes_description or "",
        pipeline_params_override=override,
        accuracy=scores["accuracy"],
        composite=scores.get("composite", scores["accuracy"]),
        hits=scores["hits"],
        total=scores["total"],
        evaluators=evaluators,
        escalation_aborted=aborted,
        elimination_stopped=elimination_stopped,
        scored_queries=len(results),
        expected_queries=len(dataset),
        invalid=invalid,
        resumed_from_cache=resumed_from_cache,
        validation_failures=[vf.to_dict() for vf in osp.validation_failures],
        runtime_failures=[new_runtime_failure.to_dict()] if new_runtime_failure else [],
        elimination_context=dict(elimination_context) if elimination_context else {},
    )


_INVALID_SCORES: dict[str, Any] = {
    "accuracy": 0.0,
    "composite": 0.0,
    "hits": 0,
    "total": 0,
    "errors": 0,
    "invalid": True,
}


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
    *,
    l1_diversity: float = 1.0,
) -> tuple[CandidateScore, EscalationSignal | None]:
    """Build report for a scored candidate; attach RuntimeFailure on elimination (Rail 2)."""
    elimination_stopped = (
        signal is not None and signal.target == EscalationTarget.ELIMINATE_CANDIDATE
    )
    scoring_error_abort = signal is not None and signal.check_name == "scoring_error_abort"
    aborted = bool(signal) and (scoring_error_abort or len(results) < len(dataset))

    # Aborted candidates must NOT seed priors — their scores are synthetic 0s.
    if len(results) == len(dataset) and not aborted:
        elim_check.register_completed(
            [r.get("score", 0.0) for r in results], candidate_id=osp_c.lineage.id
        )

    new_rf: RuntimeFailure | None = None
    rf_kind = (
        "degradation_check"
        if elimination_stopped and signal is not None and signal.check_name == "degradation"
        else "scoring_error_abort"
        if scoring_error_abort
        else None
    )
    if rf_kind and signal is not None:
        cr = signal.check_result
        dc = int(cr.get("degraded_count", 0))
        te = int(cr.get("total_scored", len(results)))
        if rf_kind == "degradation_check":
            dominant = cr.get("dominant_warning", "unknown:unknown")
            node_cfg = (merged_pp_i or {}).get(dominant.split(":", 1)[0], {})
            rate = float(cr.get("degraded_rate", 0.0))
        else:
            dominant = str(cr.get("dominant_warning") or "scoring_error")
            node_cfg = merged_pp_i or {}
            rate = (dc / te) if te else 0.0
        new_rf = RuntimeFailure(
            source=rf_kind,
            dominant_warning=dominant,
            warning_types=dict(cr.get("warning_types") or {}),
            degraded_rate=rate,
            degraded_count=dc,
            total_scored=te,
            observed_config=dict(node_cfg),
            first_seen_round=round_num,
            candidate_label=osp_c.lineage.changes_description or "",
        )
        osp_c.runtime_failures = [*osp_c.runtime_failures, new_rf]

    elim_ctx: dict | None = None
    if elimination_stopped and signal is not None and signal.check_name == "elimination":
        cr = signal.check_result
        elim_ctx = {
            "triggered_p": float(cr.get("triggered_p", 1.0)),
            "triggered_by_prior_idx": int(cr.get("triggered_by_prior", -1)),
            "queries_scored": int(cr.get("queries_scored", len(results))),
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
        l1_diversity=l1_diversity,
    )
    residual = None if (elimination_stopped or not signal) else signal
    return report, residual


def _record_elimination_cut(
    signal: EscalationSignal,
    osp_c: OptSearchPoint,
    elim_check: Any,
    priors_at_test: list[str],
    candidate_scores: list[CandidateScore],
    report: CandidateScore,
    decisions: list[Decision] | None,
    round_num: int,
    n_results: int,
) -> None:
    """Decorate report + append elimination_cut decision for divergence replay."""
    cr = signal.check_result
    trigger_idx = int(cr.get("triggered_by_prior", -1))
    if 0 <= trigger_idx < len(priors_at_test):
        prior_id = priors_at_test[trigger_idx]
        prior_label = next(
            (f"C{i + 1}" for i, r in enumerate(candidate_scores) if r.candidate_id == prior_id),
            None,
        )
        if prior_label and report.elimination_context:
            report.elimination_context["triggered_by_prior_label"] = prior_label

    if decisions is not None:
        record_decision(
            decisions,
            "elimination_cut",
            {
                "candidate_id": osp_c.lineage.id,
                "prior_candidate_ids": priors_at_test,
                "queries_scored": int(cr.get("queries_scored", n_results)),
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


async def score_population(
    cycle: Cycle,
    population: list[OptSearchPoint],
    merged_pp: list[dict | None],
    proposals: list[CandidateProposal],
    dataset: list,
    *,
    degradation_checks: list | None = None,
    callbacks: RunListener,
    elimination_n_min: int = 4,
    elimination_alpha: float = 0.2,
    round_num: int = 0,
    decisions: list[Decision] | None = None,
    l1_diversity: float = 1.0,
) -> tuple[dict[str, list[QueryResult]], list[CandidateScore], EscalationSignal | None]:
    """Score each individual; dispatch over three exit paths (validation/cache/scored)."""
    session = cycle.session
    obs = session.state.obs
    n = len(population)

    all_candidate_results: dict[str, list[QueryResult]] = {}
    candidate_scores: list[CandidateScore] = []
    escalation_signal: EscalationSignal | None = None
    elim_check = EliminationCheck(
        n_min=elimination_n_min, alpha=elimination_alpha, n_queries=len(dataset)
    )

    def _fire(idx: int, report: CandidateScore) -> None:
        candidate_scores.append(report)
        callbacks.on_candidate_scored(idx, n, report.to_dict())
        if obs:
            with graceful("CandidateScored emit failed"):
                obs.emit_write_point(
                    CandidateScored,
                    campaign_id=session.state.obs_campaign_id,
                    round_num=round_num,
                    candidate_idx=idx,
                    report=report.to_dict(),
                )

    for idx, osp_c in enumerate(population):
        override = proposals[idx].node_overrides or None
        callbacks.on_candidate_started(idx, n, osp_c.lineage.changes_description or "", override)

        # Path 1 — validation-skip synthetic-0.
        if osp_c.validation_failures:
            all_candidate_results[osp_c.lineage.id] = []
            _fire(
                idx,
                _build_score_report(
                    osp_c,
                    override,
                    _INVALID_SCORES,
                    [],
                    dataset,
                    invalid=True,
                    l1_diversity=l1_diversity,
                ),
            )
            continue

        def _on_result(r, qi, qt, _ci=idx):
            callbacks.on_sample_scored(_ci, n, qi, qt, r)

        def _on_start(qtxt, qi, qt, _ci=idx):
            callbacks.on_sample_started(_ci, n, qi, qt, qtxt)

        results, scores, was_cached, signal = await score_search_point(
            osp_c.to_job_search_point(
                base_pipeline_params=merged_pp[idx], schema=session.pipeline_schema
            ),
            dataset,
            session,
            label=f"candidate_{idx}",
            on_result=_on_result,
            on_start=_on_start,
            degradation_checks=[*(degradation_checks or []), elim_check],
            candidate_idx=idx,
            n_total_candidates=n,
            axes=cycle.axes,
            l1_diversity=l1_diversity,
        )
        all_candidate_results[osp_c.lineage.id] = results

        # Path 2 — full-run cache replay.
        if was_cached:
            elim_check.register_completed(
                [r.get("score", 0.0) for r in results], candidate_id=osp_c.lineage.id
            )
            _fire(
                idx,
                _build_score_report(
                    osp_c,
                    override,
                    scores,
                    results,
                    dataset,
                    resumed_from_cache=True,
                    l1_diversity=l1_diversity,
                ),
            )
            continue

        # Path 3 — scored. Snapshot priors BEFORE helper registers this candidate.
        priors_at_test = list(elim_check.prior_ids)
        report, residual = _handle_scored_candidate(
            osp_c,
            override,
            results,
            scores,
            signal,
            merged_pp[idx],
            dataset,
            elim_check,
            round_num,
            l1_diversity=l1_diversity,
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
