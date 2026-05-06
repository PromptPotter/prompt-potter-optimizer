"""L1 phase: generate → measure → score → execute round.

The round-loop spine. Validators + invariant detection live in
``l1_validators``; population-shape helpers (``parse_population``,
``build_score_report``, ``pobb_decision_data``) live in ``l1_population``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from functools import partial
from typing import TYPE_CHECKING

from promptpotter.application.optimization.cycle import Cycle, DecisionRecord
from promptpotter.application.optimization.dispatch_hub import (
    DispatchHub,
    build_bundle,
    format_l1_critique_for_prompt,
)
from promptpotter.application.optimization.elimination import PoBBCheck, PoBBConfig
from promptpotter.application.optimization.formatting import candidate_summaries
from promptpotter.application.optimization.l1_critique import run_l1_critique
from promptpotter.application.optimization.l1_population import (
    INVALID_SCORES,
    build_score_report,
    parse_population,
    pobb_decision_data,
)
from promptpotter.application.optimization.l1_validators import (
    L1YieldStats,
    _normalize_pp_override,
    build_l1_output_schema,
    detect_invariants,
)
from promptpotter.application.optimization.llm_call import (
    load_optimizer_prompt,
    run_optimizer_node,
)
from promptpotter.application.optimization.round_diagnostics import (
    compute_round_diagnostics,
)
from promptpotter.application.scoring.metrics import (
    _compute_accuracy,
    compute_composite_fitness,
    count_degraded_queries,
)
from promptpotter.application.scoring.search_point_scorer import score_search_point
from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.domain.analysis import EscalationSignal, RuntimeFailure
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.phases import CampaignPhase, emit_phase
from promptpotter.domain.results import (
    CandidateProposal,
    CandidateScore,
    RoundBaseline,
    RoundResult,
)
from promptpotter.domain.run_records import DecisionKind, record_decision
from promptpotter.domain.scoring import QueryMeasurement
from promptpotter.domain.validators import StopRule

# Module-level alias for test monkeypatching.
from promptpotter.infrastructure import llm as _llm_client
from promptpotter.infrastructure.llm import LLMClientBase
from promptpotter.infrastructure.tracing import (
    CandidateCreated,
    CandidateScored,
    L1CritiqueWritten,
    PromptVersion,
    RoundEnd,
    RoundStart,
    RoundWinnerChosen,
    observed_node,
)
from promptpotter.shared.errors import graceful
from promptpotter.shared.statistics import proportion_test

if TYPE_CHECKING:
    from promptpotter.application.run_callbacks import RunCallbacks
    from promptpotter.domain.sample import Sample
    from promptpotter.infrastructure.tracing import ObservabilityBridge

logger = logging.getLogger(__name__)

__all__ = [
    "execute_round",
    "l1_generate",
    "l1_score",
    "score_population",
]


# ---------------------------------------------------------------------------
# Section 1 — Generation
# ---------------------------------------------------------------------------


async def l1_generate(
    cycle: Cycle,
    *,
    n_variants: int,
    creativity: float,
    llm_client: LLMClientBase,
    model: str | None = None,
    obs: ObservabilityBridge | None = None,
    round_num: int = 0,
) -> list[CandidateProposal]:
    """Generate candidate variants via LLM meta-prompt; context read from cycle."""
    if n_variants <= 0:
        raise ValueError(f"n_variants must be >0, got {n_variants}")

    opt_sp = cycle.opt_sp
    pipeline_schema = cycle.session.pipeline_schema
    tracing_campaign_id = cycle.session.state.tracing_campaign_id

    bundle = build_bundle(cycle)
    template = DispatchHub.fill_l1(load_optimizer_prompt("l1_generate"), opt_sp.l1_layout, bundle)
    prompt_vars: dict[str, str] = {
        "n_variants": str(n_variants),
        "accuracy_pct": f"{cycle.tracking.current_accuracy:.1%}",
        "n_queries": str(len(cycle.tracking.current_results)),
    }

    output_schema = build_l1_output_schema(pipeline_schema) if pipeline_schema else None
    generated, meta_prompt = await run_optimizer_node(
        template_name="l1_generate",
        prompt_vars=prompt_vars,
        llm_client=llm_client,
        model=model,
        temperature=creativity,
        json_schema=output_schema,
        recorder=cycle.session.state.audit_projection,
        template=template,
        optimizer_call_cache=cycle.session.store.optimizer_calls,
    )
    slot_sizes = sorted(
        (
            (slot, len(slot_text.rstrip()))
            for slot in ("persona", "task_intent", "problem_description", "thinking_style")
            if (slot_text := getattr(template, slot)) and slot_text.strip()
        ),
        key=lambda x: -x[1],
    )
    logger.info(
        "L1 R%d meta-prompt: %d chars | %s",
        round_num,
        len(meta_prompt),
        " | ".join(f"{n}={s}" for n, s in slot_sizes),
    )

    variants_list = generated.get("variants", []) if isinstance(generated, dict) else generated

    population: list[CandidateProposal] = []
    for v in variants_list[:n_variants]:
        prompt_changes, tc_changes, pipeline_params_override = _normalize_pp_override(
            v.get("pipeline_params_override") or {}, pipeline_schema
        )
        # Override validation is deferred to parse_population — one producer of truth.
        child = opt_sp.mutate(
            changes_description=v.get("changes_description", ""),
            source="l1_generate",
            **prompt_changes,
        )
        if tc_changes:
            child.task_context = child.task_context.merge(tc_changes)
        population.append(
            CandidateProposal(osp=child, pipeline_params_override=pipeline_params_override)
        )

        if obs:
            with graceful("CandidateCreated emit failed"):
                obs.emit_write_point(
                    CandidateCreated,
                    campaign_id=tracing_campaign_id,
                    round_num=round_num,
                    candidate_idx=len(population) - 1,
                    candidate_id=child.lineage.id,
                )

    return population


# ---------------------------------------------------------------------------
# Section 2 — Measurement (score_population — three exit paths)
# ---------------------------------------------------------------------------


def _on_query_scored(
    callbacks: RunCallbacks,
    idx: int,
    n_total: int,
    result,
    query_idx: int,
    query_total: int,
) -> None:
    callbacks.on_sample_scored(idx, n_total, query_idx, query_total, result)


def _on_query_starting(
    callbacks: RunCallbacks,
    idx: int,
    n_total: int,
    query_text,
    query_idx: int,
    query_total: int,
) -> None:
    callbacks.on_sample_started(idx, n_total, query_idx, query_total, query_text)


def _emit_p_best(
    callbacks: RunCallbacks,
    round_num: int,
    idx: int,
    n: int,
    snap,
) -> None:
    callbacks.on_p_best_update(round_num, idx, n, snap)


class CandidateOutcome(StrEnum):
    """How ``score_one_candidate`` exited. Caller fires the report unconditionally
    and uses the tag to decide whether to break the loop or continue.

    SCORED is the default exit. SKIPPED_VALIDATION / REPLAYED_FROM_CACHE are
    early returns from paths 1 and 2; both still produce a report. LEADER_LOCKED
    and ESCALATED are scored-path tags that signal the caller to break."""

    SKIPPED_VALIDATION = "skipped_validation"
    REPLAYED_FROM_CACHE = "replayed_from_cache"
    SCORED = "scored"
    LEADER_LOCKED = "leader_locked"
    ESCALATED = "escalated"


@dataclass(frozen=True)
class CandidateRunResult:
    """One candidate's full lifecycle output. ``runtime_failure`` is the
    caller's signal to append to ``osp_c.runtime_failures``; the function
    cannot mutate it directly because the OSP is shared with other paths."""

    outcome: CandidateOutcome
    results: list[QueryMeasurement] = field(default_factory=list)
    report: CandidateScore = None  # type: ignore[assignment]
    runtime_failure: RuntimeFailure | None = None
    escalation_signal: EscalationSignal | None = None


@dataclass(frozen=True)
class SignalEffect:
    """One pure decode of an ``EscalationSignal`` over a candidate's eval state.

    Folds four overlapping reads of ``signal.check_result`` (RuntimeFailure
    construction, elimination context, ELIMINATION_CUT decision payload,
    LEADER_LOCK_IN decision payload) into a single pass. The caller still
    owns leader-label decoration (needs prior-rank lookup over already-scored
    candidates) and decision emission gating. Decision payloads are kept as
    ``(inputs_ref, data)`` tuples — the ``DecisionKind`` literal stays at
    the ``record_decision`` callsite (static check in
    ``test_no_bare_string_decision_kinds``).
    """

    aborted: bool
    elimination_stopped: bool
    leader_locked: bool
    leader_locked_loose: bool
    leader_id: str
    runtime_failure: RuntimeFailure | None
    elim_context: dict | None
    elimination_decision: tuple[dict, dict] | None
    leader_lock_decision: tuple[dict, dict] | None


def decode_signal_effect(
    signal: EscalationSignal | None,
    *,
    results: list,
    dataset: list,
    merged_pp: dict | None,
    round_num: int,
    elim_check: PoBBCheck,
    candidate_id: str,
    candidate_label: str,
    priors_at_test: list[str],
) -> SignalEffect:
    """Decode all per-candidate signal effects in one pass over ``check_result``."""
    if signal is None:
        return SignalEffect(False, False, False, False, "", None, None, None, None)

    elimination_stopped = signal.is_elimination
    leader_locked_loose = signal.is_leader_lock
    scoring_error_abort = signal.check_name == "scoring_error_abort"
    leader_locked = signal.is_leader_lock and signal.check_name == elim_check.name
    aborted = not leader_locked_loose and (scoring_error_abort or len(results) < len(dataset))

    cr = signal.check_result

    new_rf: RuntimeFailure | None = None
    if elimination_stopped and signal.check_name == "degradation":
        rf_kind: str | None = "degradation_check"
        dominant = cr.get("dominant_warning", "unknown:unknown")
        node_cfg = (merged_pp or {}).get(dominant.split(":", 1)[0], {})
        rate = float(cr.get("degraded_rate", 0.0))
    elif scoring_error_abort:
        rf_kind = "scoring_error_abort"
        dominant = str(cr.get("dominant_warning") or "scoring_error")
        node_cfg = merged_pp or {}
        dc_tmp = int(cr.get("degraded_count", 0))
        te_tmp = int(cr.get("total_scored", len(results)))
        rate = (dc_tmp / te_tmp) if te_tmp else 0.0
    else:
        rf_kind = None
    if rf_kind is not None:
        new_rf = RuntimeFailure(
            source=rf_kind,
            dominant_warning=dominant,
            warning_types=dict(cr.get("warning_types") or {}),
            degraded_rate=rate,
            degraded_count=int(cr.get("degraded_count", 0)),
            total_scored=int(cr.get("total_scored", len(results))),
            observed_config=dict(node_cfg),
            first_seen_round=round_num,
            candidate_label=candidate_label,
        )

    elim_ctx: dict | None = None
    leader_id = ""
    if (elimination_stopped or leader_locked_loose) and signal.check_name == "elimination":
        leader_id = str(cr.get("leader_id", ""))
        elim_ctx = {
            "p_best": float(cr.get("p_best", 0.0)),
            "epsilon": float(cr.get("epsilon", 0.05)),
            "leader_id": leader_id,
            "queries_scored": int(cr.get("queries_scored", len(results))),
            "total_queries": int(cr.get("total_queries", len(dataset))),
            "n_priors": int(cr.get("n_priors", 0)),
            "leader_locked": leader_locked_loose,
        }

    queries_scored = int(cr.get("queries_scored", len(results)))
    elimination_decision: tuple[dict, dict] | None = None
    if elimination_stopped and signal.check_name == elim_check.name:
        elimination_decision = (
            {
                "candidate_id": candidate_id,
                "prior_candidate_ids": priors_at_test,
                "queries_scored": queries_scored,
                "epsilon": float(elim_check.epsilon),
                "n_min": int(elim_check.n_min),
                "round_num": round_num,
            },
            pobb_decision_data(cr),
        )
    leader_lock_decision: tuple[dict, dict] | None = None
    if leader_locked:
        leader_lock_decision = (
            {
                "candidate_id": candidate_id,
                "prior_candidate_ids": priors_at_test,
                "queries_scored": queries_scored,
                "lock_in": float(elim_check.lock_in),
                "lock_in_n_min": int(elim_check.lock_in_n_min),
                "round_num": round_num,
            },
            pobb_decision_data(cr),
        )

    return SignalEffect(
        aborted=aborted,
        elimination_stopped=elimination_stopped,
        leader_locked=leader_locked,
        leader_locked_loose=leader_locked_loose,
        leader_id=leader_id,
        runtime_failure=new_rf,
        elim_context=elim_ctx,
        elimination_decision=elimination_decision,
        leader_lock_decision=leader_lock_decision,
    )


async def score_one_candidate(
    *,
    idx: int,
    osp_c: OptSearchPoint,
    pipeline_params_override: dict | None,
    cycle: Cycle,
    dataset: list,
    n_total: int,
    merged_pp: dict | None,
    elim_check: PoBBCheck,
    callbacks: RunCallbacks,
    degradation_checks: list[StopRule] | None,
    decisions: list[DecisionRecord] | None,
    candidate_scores: list[CandidateScore],
    round_num: int,
    l1_diversity: float,
) -> CandidateRunResult:
    """Run one candidate through the three-exit-path lifecycle.

    Path 1 — validation-skip: synthetic-0 score (no eval).
    Path 2 — cache-replay: full-run cache hit (no backend calls).
    Path 3 — scored: full eval; classifies signal into SCORED / LEADER_LOCKED /
    ESCALATED, builds RuntimeFailure on degradation, records ELIMINATION_CUT
    / LEADER_LOCK_IN decisions on ``decisions``.

    ``candidate_scores`` is read for prior_label resolution (already-scored
    candidates only — caller appends the current report after this returns)."""
    # Path 1 — validation-skip synthetic-0.
    if osp_c.validation_failures:
        return CandidateRunResult(
            outcome=CandidateOutcome.SKIPPED_VALIDATION,
            results=[],
            report=build_score_report(
                osp_c,
                pipeline_params_override,
                INVALID_SCORES,
                [],
                dataset,
                invalid=True,
                l1_diversity=l1_diversity,
            ),
        )

    results, scores, was_cached, signal = await score_search_point(
        osp_c.to_job_search_point(
            base_pipeline_params=merged_pp, schema=cycle.session.pipeline_schema
        ),
        dataset,
        cycle.session,
        label=f"candidate_{idx}",
        on_query_scored=partial(_on_query_scored, callbacks, idx, n_total),
        on_query_starting=partial(_on_query_starting, callbacks, idx, n_total),
        degradation_checks=[*(degradation_checks or []), elim_check],
        candidate_idx=idx,
        n_total_candidates=n_total,
        axes=cycle.axes,
        l1_diversity=l1_diversity,
    )

    # Path 2 — full-run cache replay.
    if was_cached:
        elim_check.register_completed(
            [r.get("fitness", 0.0) for r in results], candidate_id=osp_c.lineage.id
        )
        return CandidateRunResult(
            outcome=CandidateOutcome.REPLAYED_FROM_CACHE,
            results=results,
            report=build_score_report(
                osp_c,
                pipeline_params_override,
                scores,
                results,
                dataset,
                resumed_from_cache=True,
                l1_diversity=l1_diversity,
            ),
        )

    # Path 3 — scored. Snapshot priors BEFORE eval registers this candidate.
    priors_at_test = list(elim_check.prior_ids)
    effect = decode_signal_effect(
        signal,
        results=results,
        dataset=dataset,
        merged_pp=merged_pp,
        round_num=round_num,
        elim_check=elim_check,
        candidate_id=osp_c.lineage.id,
        candidate_label=osp_c.lineage.changes_description or "",
        priors_at_test=priors_at_test,
    )
    # Aborted candidates must NOT seed priors — their scores are synthetic 0s.
    if len(results) == len(dataset) and not effect.aborted:
        elim_check.register_completed(
            [r.get("fitness", 0.0) for r in results], candidate_id=osp_c.lineage.id
        )

    report = build_score_report(
        osp_c,
        pipeline_params_override,
        scores,
        results,
        dataset,
        aborted=effect.aborted,
        elimination_stopped=effect.elimination_stopped,
        elimination_context=dict(effect.elim_context) if effect.elim_context else None,
        new_runtime_failure=effect.runtime_failure,
        l1_diversity=l1_diversity,
    )

    # Decorate elim_ctx with prior label when the leader was a prior candidate.
    if (
        effect.leader_id
        and effect.leader_id in priors_at_test
        and report.elimination_context is not None
    ):
        prior_label = next(
            (
                f"C{i + 1}"
                for i, r in enumerate(candidate_scores)
                if r.candidate_id == effect.leader_id
            ),
            None,
        )
        if prior_label:
            report.elimination_context["leader_label"] = prior_label

    if decisions is not None and effect.elimination_decision is not None:
        inputs_ref, data = effect.elimination_decision
        record_decision(
            decisions,
            DecisionKind.ELIMINATION_CUT,
            inputs_ref,
            True,
            data=data,
            round=round_num,
        )
    if decisions is not None and effect.leader_lock_decision is not None:
        inputs_ref, data = effect.leader_lock_decision
        record_decision(
            decisions,
            DecisionKind.LEADER_LOCK_IN,
            inputs_ref,
            True,
            data=data,
            round=round_num,
        )

    residual = (
        None if (effect.elimination_stopped or effect.leader_locked_loose or not signal) else signal
    )
    if effect.leader_locked:
        outcome = CandidateOutcome.LEADER_LOCKED
    elif residual is not None:
        outcome = CandidateOutcome.ESCALATED
    else:
        outcome = CandidateOutcome.SCORED
    return CandidateRunResult(
        outcome=outcome,
        results=results,
        report=report,
        runtime_failure=effect.runtime_failure,
        escalation_signal=residual if outcome == CandidateOutcome.ESCALATED else None,
    )


async def score_population(
    cycle: Cycle,
    population: list[OptSearchPoint],
    merged_pp: list[dict | None],
    proposals: list[CandidateProposal],
    dataset: list,
    *,
    degradation_checks: list[StopRule] | None = None,
    callbacks: RunCallbacks,
    pobb_config: PoBBConfig,
    round_num: int = 0,
    decisions: list[DecisionRecord] | None = None,
    l1_diversity: float = 1.0,
) -> tuple[dict[str, list[QueryMeasurement]], list[CandidateScore], EscalationSignal | None]:
    """Score each individual; dispatch over three exit paths (validation/cache/scored).

    Per-candidate body lives in ``score_one_candidate``; this function owns
    the loop, the shared accumulators (``candidate_scores``, ``decisions``,
    ``all_candidate_results``), and the break conditions on
    LEADER_LOCKED / ESCALATED outcomes."""
    session = cycle.session
    obs = session.state.obs
    n = len(population)

    all_candidate_results: dict[str, list[QueryMeasurement]] = {}
    candidate_scores: list[CandidateScore] = []
    escalation_signal: EscalationSignal | None = None
    elim_check = PoBBCheck(pobb_config, n_queries=len(dataset))

    for idx, osp_c in enumerate(population):
        pipeline_params_override = proposals[idx].pipeline_params_override or None
        callbacks.on_candidate_started(
            idx, n, osp_c.lineage.changes_description or "", pipeline_params_override
        )
        # Bind the PoBBCheck to this candidate so its per-query snapshot
        # lands on the live telemetry stream tagged with the right id.
        elim_check.set_current(
            osp_c.lineage.id,
            on_snapshot=partial(_emit_p_best, callbacks, round_num, idx, n),
        )

        cr_result = await score_one_candidate(
            idx=idx,
            osp_c=osp_c,
            pipeline_params_override=pipeline_params_override,
            cycle=cycle,
            dataset=dataset,
            n_total=n,
            merged_pp=merged_pp[idx],
            elim_check=elim_check,
            callbacks=callbacks,
            degradation_checks=degradation_checks,
            decisions=decisions,
            candidate_scores=candidate_scores,
            round_num=round_num,
            l1_diversity=l1_diversity,
        )
        all_candidate_results[osp_c.lineage.id] = cr_result.results
        if cr_result.runtime_failure is not None:
            osp_c.runtime_failures = [*osp_c.runtime_failures, cr_result.runtime_failure]
        candidate_scores.append(cr_result.report)
        callbacks.on_candidate_scored(idx, n, cr_result.report.to_dict())
        if obs:
            with graceful("CandidateScored emit failed"):
                obs.emit_write_point(
                    CandidateScored,
                    campaign_id=session.state.tracing_campaign_id,
                    round_num=round_num,
                    candidate_idx=idx,
                    report=cr_result.report.to_dict(),
                )

        if cr_result.outcome == CandidateOutcome.LEADER_LOCKED:
            break  # round leader confirmed — skip remaining candidates
        if cr_result.outcome == CandidateOutcome.ESCALATED:
            escalation_signal = cr_result.escalation_signal
            break  # true degradation — abort remaining candidates

    return all_candidate_results, candidate_scores, escalation_signal


# ---------------------------------------------------------------------------
# Section 3 — Scoring orchestration (winner selection)
# ---------------------------------------------------------------------------


async def l1_score(
    cycle: Cycle,
    candidates: list[CandidateProposal],
    dataset: list,
    baseline: RoundBaseline,
    *,
    pipeline_params: dict | None = None,
    improvement_threshold: float = 0.01,
    callbacks: RunCallbacks,
    degradation_checks: list[StopRule] | None = None,
    pobb_config: PoBBConfig,
    round_num: int = 0,
    yield_stats: L1YieldStats,
) -> tuple[RoundResult, OptSearchPoint]:
    """Score candidates and select the round winner (compares fitness, not composite_fitness).

    Returns ``(round_result, winner_osp)``. ``RoundResult`` is the
    persistence shape (dict-typed lists for serialization); ``winner_osp``
    rides alongside for the ``PromptVersion`` tracing emit in the caller.
    """
    session = cycle.session
    schema = session.pipeline_schema
    assert schema is not None, "l1_score requires pipeline_schema"

    osp_population, merged_pp = parse_population(candidates, pipeline_params, schema)
    decisions: list[DecisionRecord] = []
    all_candidate_results, candidate_scores, escalation_signal = await score_population(
        cycle,
        osp_population,
        merged_pp,
        candidates,
        dataset,
        degradation_checks=degradation_checks,
        callbacks=callbacks,
        pobb_config=pobb_config,
        round_num=round_num,
        decisions=decisions,
        l1_diversity=yield_stats.l1_yield,
    )

    aborted_ids = {
        cs.candidate_id
        for cs in candidate_scores
        if cs.escalation_aborted and not cs.elimination_stopped
    }
    scored = [
        ind
        for ind in osp_population
        if ind.lineage.id in all_candidate_results and ind.lineage.id not in aborted_ids
    ]
    best_acc = baseline.accuracy
    best_comp = baseline.composite_fitness
    best_osp: OptSearchPoint = baseline.osp
    best_results: list = list(baseline.results)
    best_label = baseline.label
    best_scores: dict[str, float] = dict(baseline.evaluators)
    winner_idx: int | None = None
    for idx, ind in enumerate(scored):
        s = compute_composite_fitness(
            all_candidate_results[ind.lineage.id],
            schema,
            opt_sp=ind,
            round_scorer=session.scoring.round_scorer,
            l1_diversity=yield_stats.l1_yield,
        )
        if s["accuracy"] > best_acc:
            best_acc = s["accuracy"]
            best_comp = s["composite_fitness"]
            best_osp = ind
            best_results = list(all_candidate_results[ind.lineage.id])
            best_label = ind.lineage.changes_description or ind.lineage.id[:12]
            best_scores = dict(s.get("evaluators") or {})
            winner_idx = idx

    record_decision(
        decisions,
        DecisionKind.ROUND_WINNER,
        {
            "candidate_ids": [ind.lineage.id for ind in scored],
            "round_num": round_num,
        },
        scored[winner_idx].lineage.id if winner_idx is not None and scored else "",
        data={"current_best_accuracy_at_record": baseline.accuracy},
        round=round_num,
    )

    base = _compute_accuracy(best_results)
    improved = best_acc > baseline.accuracy + improvement_threshold
    p_value: float | None = None
    if improved and base["total"] > 0:
        bl_hits = round(baseline.accuracy * base["total"])
        p_value = proportion_test(base["hits"], base["total"], bl_hits, base["total"])
    round_result = RoundResult(
        round=round_num,
        label=best_label,
        accuracy=best_acc,
        composite_fitness=best_comp,
        hits=base["hits"],
        total=base["total"],
        improved=improved,
        p_value=p_value,
        baseline_accuracy=baseline.accuracy,
        prompt_fields={
            **best_osp.prompt_field_dict(),
            "lineage": best_osp.lineage.model_dump(),
        },
        pipeline_params=merged_pp[winner_idx] if winner_idx is not None else pipeline_params,
        results=best_results,
        all_candidate_results=dict(all_candidate_results),
        candidates_scored=len(scored),
        candidate_scores=[cs.to_dict() for cs in candidate_scores],
        decisions=[d.to_dict() for d in decisions],
        degraded_queries=count_degraded_queries(best_results),
        deprecated=base["deprecated"],
        escalation_signal=escalation_signal,
        evaluators=best_scores,
        l1_yield=yield_stats.l1_yield,
        l1_n_no_op=yield_stats.l1_n_no_op,
        l1_n_duplicate=yield_stats.l1_n_duplicate,
    )
    return round_result, best_osp


# ---------------------------------------------------------------------------
# Section 4 — Round execution (top-level L1 round driver)
# ---------------------------------------------------------------------------


async def generate_or_load_candidates(
    round_num: int,
    cycle: Cycle,
    on_phase=None,
    n_eval_queries: int = 0,
    *,
    obs: ObservabilityBridge | None = None,
) -> tuple[list[CandidateProposal], L1YieldStats]:
    """Load persisted candidates or generate fresh ones via LLM; detect no-op + duplicate variants."""
    session = cycle.session
    config = cycle.config
    # Cap n_variants at 3× config so L2 can't blow up the round budget.
    opt = config.optimization
    model = config.optimizer_llm.model
    opt_params = cycle.opt_sp.optimizer_params
    _n_variants = min(opt_params.get("n_variants", opt.n_variants), opt.n_variants * 3)
    _creativity = opt_params.get("creativity", opt.creativity)
    prompt_preview = cycle.opt_sp.render()[:120]

    assert cycle.tracking.current_sp is not None
    emit_phase(
        on_phase,
        CampaignPhase.L1_GENERATE,
        "enter",
        round=round_num,
        current_accuracy=cycle.tracking.current_accuracy,
        prompt_preview=prompt_preview,
        n_variants=_n_variants,
        creativity=_creativity,
        model=model or "(default)",
        has_l1_critique=bool(cycle.rounds[-1].critique) if cycle.rounds else False,
        pipeline_params=cycle.tracking.current_sp.pipeline_params,
        parent_prompt_fields={k: v for k, v in cycle.opt_sp.prompt_field_dict().items() if v},
    )

    if session.state.cycle_id:
        persisted_raw = session.store.campaigns.load_round_candidates(
            session.backend_id,
            session.state.cycle_id,
            round_num,
        )
        if persisted_raw is not None:
            persisted = [CandidateProposal.model_validate(d) for d in persisted_raw]
            logger.debug("Loaded %d persisted candidates for round %d", len(persisted), round_num)
            yield_stats = detect_invariants(persisted, cycle.opt_sp)
            # llm_call never fires on this branch — synthesize l1_generate
            # so dashboard.json + round_NNNN.json don't miss the node.
            if _rr := session.state.audit_projection:
                _rr.set_node(
                    "l1_generate",
                    {
                        "input": {"source": "loaded_from_disk", "round": round_num},
                        "output": {"candidates": candidate_summaries(persisted)},
                    },
                )
            emit_phase(
                on_phase,
                CampaignPhase.L1_GENERATE,
                "exit",
                round=round_num,
                n_candidates=len(persisted),
                n_eval_queries=n_eval_queries,
                loaded_from_disk=True,
                candidates=candidate_summaries(persisted),
                l1_yield=yield_stats.l1_yield,
                l1_n_no_op=yield_stats.l1_n_no_op,
                l1_n_duplicate=yield_stats.l1_n_duplicate,
            )
            return persisted, yield_stats

    logger.debug("No persisted candidates for round %d — generating fresh", round_num)

    client = _llm_client.get_llm_client(config.optimizer_llm.provider)
    async with observed_node(
        f"l1_generate_r{round_num}",
        "llm/meta",
        obs=obs,
        campaign_id=session.state.tracing_campaign_id,
        round_num=round_num,
    ):
        candidates = await l1_generate(
            cycle,
            n_variants=_n_variants,
            creativity=_creativity,
            llm_client=client,
            model=model,
            obs=obs,
            round_num=round_num,
        )

    yield_stats = detect_invariants(candidates, cycle.opt_sp)

    if session.state.cycle_id:
        session.store.campaigns.save_round_candidates(
            session.backend_id,
            session.state.cycle_id,
            round_num,
            [cp.model_dump() for cp in candidates],
        )

    emit_phase(
        on_phase,
        CampaignPhase.L1_GENERATE,
        "exit",
        round=round_num,
        n_candidates=len(candidates),
        n_eval_queries=n_eval_queries,
        loaded_from_disk=False,
        candidates=candidate_summaries(candidates),
        l1_yield=yield_stats.l1_yield,
        l1_n_no_op=yield_stats.l1_n_no_op,
        l1_n_duplicate=yield_stats.l1_n_duplicate,
    )

    return candidates, yield_stats


async def execute_round(
    cycle: Cycle,
    round_num: int,
    scoring_set: list[Sample],
    callbacks: RunCallbacks,
    degradation_checks: list[StopRule] | None = None,
    *,
    skip_critique: bool = False,
) -> RoundResult:
    """Execute one L1 round: generate → score+select → critique. Returns
    the ``RoundResult`` with ``.critique`` set when L1_CRITIQUE fired; the
    runner calls ``cycle.absorb_round`` to fold it into Cycle state at the
    boundary. l1.py never mutates Cycle directly.

    ``skip_critique=True`` skips the round-end ``run_l1_critique`` LLM call.
    Sweep mode passes this so the cheap-round_data fork stays one full live LLM
    call (round 2 generate-only); the round-1 critique would otherwise dwarf
    that call and is the same across all forks since round 1 is identical.
    """
    session = cycle.session
    config = cycle.config
    opt = config.optimization
    obs = session.state.obs
    if obs:
        with graceful("RoundStart emit failed"):
            obs.emit(RoundStart(campaign_id=session.state.tracing_campaign_id, round_num=round_num))

    candidates, yield_stats = await generate_or_load_candidates(
        round_num, cycle, callbacks.on_phase, n_eval_queries=len(scoring_set), obs=obs
    )

    assert cycle.tracking.current_sp is not None
    emit_phase(
        callbacks.on_phase,
        CampaignPhase.L1_SCORE,
        "enter",
        round=round_num,
        n_candidates=len(candidates),
        n_queries=len(scoring_set),
        current_best_accuracy=cycle.tracking.current_accuracy,
        improvement_threshold=opt.improvement_threshold,
        current_pipeline_params=cycle.tracking.current_sp.pipeline_params,
    )
    baseline = cycle.baseline_for_round(scoring_set, round_num)
    async with observed_node(
        f"l1_score_r{round_num}",
        "scoring",
        obs=obs,
        as_type="span",
        campaign_id=session.state.tracing_campaign_id,
        round_num=round_num,
    ):
        round_result, winner_osp = await l1_score(
            cycle,
            candidates,
            scoring_set,
            baseline,
            pipeline_params=cycle.tracking.current_sp.pipeline_params,
            improvement_threshold=opt.improvement_threshold,
            callbacks=callbacks,
            degradation_checks=degradation_checks,
            pobb_config=PoBBConfig(
                n_min=opt.elimination_n_min,
                epsilon=opt.pobb_epsilon,
                lock_in=opt.pobb_lock_in,
                lock_in_n_min=opt.pobb_lock_in_n_min,
            ),
            round_num=round_num,
            yield_stats=yield_stats,
        )
        if obs and round_result.candidate_scores:
            with graceful("RoundWinnerChosen emit failed"):
                obs.emit_write_point(
                    RoundWinnerChosen,
                    campaign_id=session.state.tracing_campaign_id,
                    round_num=round_num,
                    winner_candidate_id=str(round_result.prompt_fields.get("id") or ""),
                    winner_accuracy=round_result.accuracy,
                    improved=round_result.improved,
                )
    emit_phase(
        callbacks.on_phase,
        CampaignPhase.L1_SCORE,
        "exit",
        round=round_num,
        winner_label=round_result.label,
        winner_accuracy=round_result.accuracy,
        winner_composite_fitness=round_result.composite_fitness,
        winner_evaluators=dict(round_result.evaluators),
        improved=round_result.improved,
        candidate_scores=round_result.candidate_scores,
    )

    # Compute deterministic post-scoring stats once and attach to the round
    # result. The dispatch hub's ``diagnostics`` signal reads this; rendering
    # is layer-agnostic so the same payload feeds L1_CRITIQUE / L2 / L3.
    # ``cycle.probe_next_round`` is still True at this point — runner only
    # resets it after ``absorb_round`` lands. Reading ``best_accuracy`` here
    # is the pre-probe full-set best (probe didn't fold yet).
    rounds_history = [*cycle.rounds, round_result]
    prior_full_accuracy = cycle.tracking.best_accuracy if cycle.probe_next_round else 0.0
    round_result.diagnostics = compute_round_diagnostics(
        round_result,
        rounds_history,
        session.pipeline_schema,
        prompt_chars=len(cycle.opt_sp.render()),
        probe_just_completed=cycle.probe_next_round,
        axis_tested=cycle.last_l2_axis,
        prior_full_accuracy=prior_full_accuracy,
    )

    critique_text = ""
    if round_result.results and not skip_critique:
        crit_llm = _llm_client.get_llm_client(config.optimizer_llm.provider)
        # Critique is round-over-round feedback; survive a malformed LLM
        # response rather than crash the campaign.
        with graceful("L1 critique failed; continuing without round-over-round feedback"):
            critique_result = await run_l1_critique(
                cycle,
                round_result,
                session.pipeline_schema,
                crit_llm,
                round_num=round_num,
                model=config.optimizer_llm.model,
                recorder=session.state.audit_projection,
            )
            round_result.critique = critique_result
            critique_text = format_l1_critique_for_prompt(critique_result)
    if obs and critique_text:
        with graceful("L1CritiqueWritten emit failed"):
            obs.emit_write_point(
                L1CritiqueWritten,
                campaign_id=session.state.tracing_campaign_id,
                round_num=round_num,
                l1_critique_text=critique_text,
            )

    if obs:
        with graceful("RoundEnd emit failed"):
            obs.emit(
                RoundEnd(
                    campaign_id=session.state.tracing_campaign_id,
                    round_num=round_num,
                    accuracy=round_result.accuracy,
                    hits=round_result.hits,
                    total=round_result.total,
                    improved=round_result.improved,
                    winner_prompt_fields_id=round_result.prompt_fields.get("id", ""),
                    candidate_scores=round_result.candidate_scores,
                    model=config.optimizer_llm.model or "",
                    n_variants=config.optimization.n_variants,
                    optimizer_templates=["l1_generate", "l1_critique"],
                    evaluators=dict(round_result.evaluators),
                )
            )
        with graceful("PromptVersion emit failed"):
            obs.emit(
                PromptVersion(
                    campaign_id=session.state.tracing_campaign_id,
                    round_num=round_num,
                    prompt_fields_id=winner_osp.lineage.id,
                    rendered_prompt=winner_osp.render(),
                    layer1_fields={f: getattr(winner_osp, f) for f in PROMPT_STRING_FIELDS},
                    parent_id=winner_osp.lineage.parent_id,
                )
            )

    return round_result
