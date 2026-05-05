"""L1 phase: generate → measure → score → execute round.

The round-loop spine. Validators + invariant detection live in
``l1_validators``; population-shape helpers + ``PopulationScoreReport`` live in
``l1_population``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from promptpotter.application.optimization.cycle import Cycle, DecisionRecord
from promptpotter.application.optimization.dispatch import (
    Layer,
    build_dispatch_state,
    compile_prompt_vars,
)
from promptpotter.application.optimization.elimination import PoBBCheck, PoBBConfig
from promptpotter.application.optimization.formatting import candidate_summaries
from promptpotter.application.optimization.l1_critique import (
    format_l1_critique_for_prompt,
    run_l1_critique,
)
from promptpotter.application.optimization.l1_population import (
    INVALID_SCORES,
    PopulationScoreReport,
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
    from promptpotter.application.runner import RunListener
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

    state = build_dispatch_state(
        Layer.L1_GENERATE,
        cycle,
        round_num=round_num,
        pipeline_schema=pipeline_schema,
    )
    prompt_vars = compile_prompt_vars(
        Layer.L1_GENERATE,
        state,
        opt_sp,
        extras={
            "n_variants": str(n_variants),
            "accuracy_pct": f"{cycle.tracking.current_accuracy:.1%}",
            "n_queries": str(len(cycle.tracking.current_results)),
            "rendered_prompt": opt_sp.render(),
        },
    )

    template = load_optimizer_prompt("l1_generate")
    if opt_sp.l1_template_override:
        template = template.model_copy(update={"problem_description": opt_sp.l1_template_override})

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
    section_sizes = sorted(
        (
            (name, len(value.rstrip()))
            for name, value in prompt_vars.items()
            if value and value.strip()
        ),
        key=lambda x: -x[1],
    )
    logger.info(
        "L1 R%d meta-prompt: %d chars | %s",
        round_num,
        len(meta_prompt),
        " | ".join(f"{n}={s}" for n, s in section_sizes[:6]),
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


async def score_population(
    cycle: Cycle,
    population: list[OptSearchPoint],
    merged_pp: list[dict | None],
    proposals: list[CandidateProposal],
    dataset: list,
    *,
    degradation_checks: list[StopRule] | None = None,
    callbacks: RunListener,
    pobb_config: PoBBConfig,
    round_num: int = 0,
    decisions: list[DecisionRecord] | None = None,
    l1_diversity: float = 1.0,
) -> tuple[dict[str, list[QueryMeasurement]], list[CandidateScore], EscalationSignal | None]:
    """Score each individual; dispatch over three exit paths (validation/cache/scored)."""
    session = cycle.session
    obs = session.state.obs
    n = len(population)

    all_candidate_results: dict[str, list[QueryMeasurement]] = {}
    candidate_scores: list[CandidateScore] = []
    escalation_signal: EscalationSignal | None = None
    elim_check = PoBBCheck(pobb_config, n_queries=len(dataset))

    def _fire(idx: int, report: CandidateScore) -> None:
        candidate_scores.append(report)
        callbacks.on_candidate_scored(idx, n, report.to_dict())
        if obs:
            with graceful("CandidateScored emit failed"):
                obs.emit_write_point(
                    CandidateScored,
                    campaign_id=session.state.tracing_campaign_id,
                    round_num=round_num,
                    candidate_idx=idx,
                    report=report.to_dict(),
                )

    for idx, osp_c in enumerate(population):
        pipeline_params_override = proposals[idx].pipeline_params_override or None
        callbacks.on_candidate_started(
            idx, n, osp_c.lineage.changes_description or "", pipeline_params_override
        )
        # Bind the PoBBCheck to this candidate so its per-query snapshot
        # lands on the live telemetry stream tagged with the right id.
        _candidate_idx = idx

        def _emit_p_best(snap, _ci=_candidate_idx) -> None:
            callbacks.on_p_best_update(round_num, _ci, n, snap)

        elim_check.set_current(osp_c.lineage.id, on_snapshot=_emit_p_best)

        # Path 1 — validation-skip synthetic-0.
        if osp_c.validation_failures:
            all_candidate_results[osp_c.lineage.id] = []
            _fire(
                idx,
                build_score_report(
                    osp_c,
                    pipeline_params_override,
                    INVALID_SCORES,
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
            on_query_scored=_on_result,
            on_query_starting=_on_start,
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
                [r.get("fitness", 0.0) for r in results], candidate_id=osp_c.lineage.id
            )
            _fire(
                idx,
                build_score_report(
                    osp_c,
                    pipeline_params_override,
                    scores,
                    results,
                    dataset,
                    resumed_from_cache=True,
                    l1_diversity=l1_diversity,
                ),
            )
            continue

        # Path 3 — scored. SnapshotRecord priors BEFORE eval registers this candidate.
        priors_at_test = list(elim_check.prior_ids)
        elimination_stopped = signal is not None and signal.is_elimination
        leader_locked_loose = signal is not None and signal.is_leader_lock
        scoring_error_abort = signal is not None and signal.check_name == "scoring_error_abort"
        aborted = (
            bool(signal)
            and not leader_locked_loose
            and (scoring_error_abort or len(results) < len(dataset))
        )
        # Aborted candidates must NOT seed priors — their scores are synthetic 0s.
        if len(results) == len(dataset) and not aborted:
            elim_check.register_completed(
                [r.get("fitness", 0.0) for r in results], candidate_id=osp_c.lineage.id
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
                node_cfg = (merged_pp[idx] or {}).get(dominant.split(":", 1)[0], {})
                rate = float(cr.get("degraded_rate", 0.0))
            else:
                dominant = str(cr.get("dominant_warning") or "scoring_error")
                node_cfg = merged_pp[idx] or {}
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
        if (
            (elimination_stopped or leader_locked_loose)
            and signal is not None
            and signal.check_name == "elimination"
        ):
            cr = signal.check_result
            elim_ctx = {
                "p_best": float(cr.get("p_best", 0.0)),
                "epsilon": float(cr.get("epsilon", 0.05)),
                "leader_id": str(cr.get("leader_id", "")),
                "queries_scored": int(cr.get("queries_scored", len(results))),
                "total_queries": int(cr.get("total_queries", len(dataset))),
                "n_priors": int(cr.get("n_priors", 0)),
                "leader_locked": leader_locked_loose,
            }

        report = build_score_report(
            osp_c,
            pipeline_params_override,
            scores,
            results,
            dataset,
            aborted=aborted,
            elimination_stopped=elimination_stopped,
            elimination_context=elim_ctx,
            new_runtime_failure=new_rf,
            l1_diversity=l1_diversity,
        )
        residual = None if (elimination_stopped or leader_locked_loose or not signal) else signal

        # PoBB-elimination cut + leader lock-in: decision-record both
        # (divergence-replayed on resume); leader_locked also breaks the loop.
        leader_locked = (
            signal is not None and signal.is_leader_lock and signal.check_name == elim_check.name
        )
        if elimination_stopped and signal is not None and signal.check_name == elim_check.name:
            cr = signal.check_result
            leader_id = str(cr.get("leader_id", ""))
            if leader_id and leader_id in priors_at_test:
                prior_label = next(
                    (
                        f"C{i + 1}"
                        for i, r in enumerate(candidate_scores)
                        if r.candidate_id == leader_id
                    ),
                    None,
                )
                if prior_label and report.elimination_context:
                    report.elimination_context["leader_label"] = prior_label
            if decisions is not None:
                record_decision(
                    decisions,
                    DecisionKind.ELIMINATION_CUT,
                    {
                        "candidate_id": osp_c.lineage.id,
                        "prior_candidate_ids": priors_at_test,
                        "queries_scored": int(cr.get("queries_scored", len(results))),
                        "epsilon": float(elim_check.epsilon),
                        "n_min": int(elim_check.n_min),
                        "round_num": round_num,
                    },
                    True,
                    data=pobb_decision_data(cr),
                    round=round_num,
                )
        if leader_locked and signal is not None and decisions is not None:
            cr = signal.check_result
            record_decision(
                decisions,
                DecisionKind.LEADER_LOCK_IN,
                {
                    "candidate_id": osp_c.lineage.id,
                    "prior_candidate_ids": priors_at_test,
                    "queries_scored": int(cr.get("queries_scored", len(results))),
                    "lock_in": float(elim_check.lock_in),
                    "lock_in_n_min": int(elim_check.lock_in_n_min),
                    "round_num": round_num,
                },
                True,
                data=pobb_decision_data(cr),
                round=round_num,
            )

        _fire(idx, report)

        if leader_locked:
            break  # round leader confirmed — skip remaining candidates
        if residual is not None:
            escalation_signal = residual
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
    callbacks: RunListener,
    degradation_checks: list[StopRule] | None = None,
    pobb_config: PoBBConfig,
    round_num: int = 0,
    yield_stats: L1YieldStats,
) -> PopulationScoreReport:
    """Score candidates and select the round winner (compares fitness, not composite_fitness)."""
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
    return PopulationScoreReport(
        label=best_label,
        winner_osp=best_osp,
        winner_prompt_fields={
            **best_osp.prompt_field_dict(),
            "lineage": best_osp.lineage.model_dump(),
        },
        winner_pipeline_params=merged_pp[winner_idx] if winner_idx is not None else pipeline_params,
        winner_accuracy=best_acc,
        winner_composite_fitness=best_comp,
        hits=base["hits"],
        total=base["total"],
        improved=improved,
        p_value=p_value,
        candidates_scored=len(scored),
        candidate_scores=candidate_scores,
        winner_results=best_results,
        all_candidate_results=all_candidate_results,
        escalation_signal=escalation_signal,
        degraded_queries=count_degraded_queries(best_results),
        deprecated=base["deprecated"],
        winner_evaluators=best_scores,
        decisions=decisions,
        l1_yield=yield_stats.l1_yield,
        l1_n_no_op=yield_stats.l1_n_no_op,
        l1_n_duplicate=yield_stats.l1_n_duplicate,
    )


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
        has_l1_critique=bool(cycle.opt_sp.l1_critique_text),
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
    callbacks: RunListener,
    degradation_checks: list[StopRule] | None = None,
    *,
    skip_critique: bool = False,
) -> RoundResult:
    """Execute one L1 round: generate → score+select → critique → persist to memory.

    ``skip_critique=True`` skips the round-end ``run_l1_critique`` LLM call.
    Sweep mode passes this so the cheap-trial fork stays one full live LLM
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
        obs_type="span",
        campaign_id=session.state.tracing_campaign_id,
        round_num=round_num,
    ):
        scoring_result = await l1_score(
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
        if obs and scoring_result.candidate_scores:
            with graceful("RoundWinnerChosen emit failed"):
                obs.emit_write_point(
                    RoundWinnerChosen,
                    campaign_id=session.state.tracing_campaign_id,
                    round_num=round_num,
                    winner_candidate_id=str(scoring_result.winner_prompt_fields.get("id") or ""),
                    winner_accuracy=scoring_result.winner_accuracy,
                    improved=scoring_result.improved,
                )
    candidate_scores_dicts = [cs.to_dict() for cs in scoring_result.candidate_scores]
    emit_phase(
        callbacks.on_phase,
        CampaignPhase.L1_SCORE,
        "exit",
        round=round_num,
        winner_label=scoring_result.label,
        winner_accuracy=scoring_result.winner_accuracy,
        winner_composite_fitness=scoring_result.winner_composite_fitness,
        winner_evaluators=dict(scoring_result.winner_evaluators),
        improved=scoring_result.improved,
        candidate_scores=candidate_scores_dicts,
    )

    critique_text = ""
    if scoring_result.winner_results and not skip_critique:
        crit_llm = _llm_client.get_llm_client(config.optimizer_llm.provider)
        # Critique is round-over-round feedback; survive a malformed LLM
        # response rather than crash the campaign.
        with graceful("L1 critique failed; continuing without round-over-round feedback"):
            critique_result = await run_l1_critique(
                cycle,
                scoring_result,
                session.pipeline_schema,
                crit_llm,
                round_num=round_num,
                model=config.optimizer_llm.model,
                recorder=session.state.audit_projection,
            )
            critique_text = format_l1_critique_for_prompt(critique_result)
    if obs and critique_text:
        with graceful("L1CritiqueWritten emit failed"):
            obs.emit_write_point(
                L1CritiqueWritten,
                campaign_id=session.state.tracing_campaign_id,
                round_num=round_num,
                l1_critique_text=critique_text,
            )

    cycle.apply_round_outcome(scoring_result, critique_text)

    round_result = RoundResult(
        round=round_num,
        label=scoring_result.label,
        accuracy=scoring_result.winner_accuracy,
        composite_fitness=scoring_result.winner_composite_fitness,
        hits=scoring_result.hits,
        total=scoring_result.total,
        improved=scoring_result.improved,
        p_value=scoring_result.p_value,
        baseline_accuracy=baseline.accuracy,
        prompt_fields=scoring_result.winner_prompt_fields,
        pipeline_params=scoring_result.winner_pipeline_params,
        results=scoring_result.winner_results,
        all_candidate_results=dict(scoring_result.all_candidate_results),
        candidates_scored=scoring_result.candidates_scored,
        candidate_scores=candidate_scores_dicts,
        decisions=[d.to_dict() for d in scoring_result.decisions],
        degraded_queries=scoring_result.degraded_queries,
        deprecated=scoring_result.deprecated,
        escalation_signal=scoring_result.escalation_signal,
        evaluators=scoring_result.winner_evaluators,
        l1_yield=scoring_result.l1_yield,
        l1_n_no_op=scoring_result.l1_n_no_op,
        l1_n_duplicate=scoring_result.l1_n_duplicate,
    )

    if obs:
        with graceful("RoundEnd emit failed"):
            obs.emit(
                RoundEnd(
                    campaign_id=session.state.tracing_campaign_id,
                    round_num=round_num,
                    accuracy=scoring_result.winner_accuracy,
                    hits=scoring_result.hits,
                    total=scoring_result.total,
                    improved=scoring_result.improved,
                    winner_prompt_fields_id=scoring_result.winner_prompt_fields.get("id", ""),
                    candidate_scores=candidate_scores_dicts,
                    model=config.optimizer_llm.model or "",
                    n_variants=config.optimization.n_variants,
                    optimizer_templates=["l1_generate", "l1_critique"],
                    evaluators=dict(scoring_result.winner_evaluators),
                )
            )
        with graceful("PromptVersion emit failed"):
            w_osp = scoring_result.winner_osp
            obs.emit(
                PromptVersion(
                    campaign_id=session.state.tracing_campaign_id,
                    round_num=round_num,
                    prompt_fields_id=w_osp.lineage.id,
                    rendered_prompt=w_osp.render(),
                    layer1_fields={f: getattr(w_osp, f) for f in PROMPT_STRING_FIELDS},
                    parent_id=w_osp.lineage.parent_id,
                )
            )

    return round_result
