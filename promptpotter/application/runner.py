"""Optimization loop orchestrator — L1 generate → score → escalate, counter-based."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from promptpotter.application.baseline import (
    CampaignBaseline,
    build_campaign_emitter,
    extract_campaign_baseline,
    load_baseline_prompt,
    prepare_scoring_context,
)
from promptpotter.application.bootstrap import (
    Session,
    auto_mint_session,
    init_optimization_loop,
)
from promptpotter.application.config import CampaignConfig
from promptpotter.application.optimization.cycle import (
    Cycle,
    NextAction,
    build_escalation_entry,
)
from promptpotter.application.optimization.escalation import (
    apply_sweep_payload_to_osp,
    escalate_l2,
)
from promptpotter.application.optimization.l1 import execute_round, generate_or_load_candidates
from promptpotter.application.presentation_writers import (
    refresh_tenant_leaderboards,
    write_log_md,
    write_review_md,
)
from promptpotter.application.scoring.formula import (
    apply_steer_file,
    apply_zero_signal_exclusions,
    split_scoring_block,
)
from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.phases import (
    CampaignPhase,
    StopLoop,
    StopReason,
    emit_phase,
)
from promptpotter.domain.results import CycleResult, RoundResult
from promptpotter.domain.run_records import (
    DecisionRecord,
    PhaseRecord,
    SnapshotRecord,
    SweepPayload,
)
from promptpotter.domain.sample import Sample
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.infrastructure.ledger import CycleLedger
from promptpotter.infrastructure.projections import (
    AuditTrailProjection,
    LiveDashboardProjection,
    PoBBStreamProjection,
)
from promptpotter.presentation.views.view_factories import (
    from_phase_event,
    view_to_wire_dict,
)
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.search_point import JobSearchPoint

logger = logging.getLogger(__name__)

__all__ = [
    "RunListener",
    "build_baseline_cycle_id",
    "cycle_config_identity",
    "run_optimization",
]


def cycle_config_identity(jsp: JobSearchPoint, dataset: list) -> str:
    """Stable identity hash for a feedback cycle's baseline ``JobSearchPoint``.

    Covers the rendered prompt, dataset, and full ``pipeline_params`` (active
    steps + per-node target-layer config). Loop-control / strategy knobs on
    ``CampaignConfig`` are deliberately excluded — tweaking optimizer
    strategy or resuming with different budgets does not start a new cycle.
    """
    return f"cycle_{jsp.content_hash(dataset)[:12]}"


def build_baseline_cycle_id(
    osp: OptSearchPoint,
    schema: PipelineSchema | None,
    dataset: list,
) -> str:
    """Cycle ID for a baseline ``OptSearchPoint`` — the OSP → JSP projection ceremony."""
    base_pp = schema.to_pipeline_params() if schema else {}
    jsp = osp.to_job_search_point(base_pipeline_params=base_pp, schema=schema)
    return cycle_config_identity(jsp, dataset)


class RunListener:
    """Single ingress: callbacks → typed CycleRecord → per-cycle CycleLedger.

    Subscribers consume via ``on_record`` only. PhaseRecord-view ctx is owned
    here (``from_phase_event`` is stateful) and serialised onto
    ``PhaseRecord.payload['view']``. Events emitted before ``ledger`` is set
    are buffered and drained on the first post-binding append.
    """

    def __init__(self, *, ledger: Any = None) -> None:
        self._ledger: Any = None
        self._phase_ctx: dict[str, Any] = {}
        self._current_round: int = 0
        self._buffer: list[Any] = []
        self.ledger = ledger  # triggers setter — drains the (empty) buffer

    @property
    def ledger(self) -> Any:
        return self._ledger

    @ledger.setter
    def ledger(self, value: Any) -> None:
        """Bind ledger + drain pre-binding buffer. Subscribers must bind first."""
        self._ledger = value
        if value is None or not self._buffer:
            return
        pending, self._buffer = self._buffer, []
        for buffered in pending:
            with graceful("ledger buffered append failed"):
                value.append(buffered)

    def _emit(self, record: Any) -> None:
        if self._ledger is None:
            self._buffer.append(record)
            return
        with graceful("ledger append failed"):
            self._ledger.append(record)

    def on_phase(self, event: Any) -> None:
        view = view_to_wire_dict(from_phase_event(event, self._phase_ctx))
        self._emit(
            PhaseRecord(
                phase=str(event.phase),
                event=str(event.event),
                round=event.round,
                payload={"view": view, "data": event.data},
            )
        )

    def on_round_complete(self, round_result: Any, l1_stall_count: int) -> None:
        self._phase_ctx["l1_stall_count"] = l1_stall_count
        self._emit(
            PhaseRecord(
                phase="round",
                event="complete",
                round=round_result.round,
                payload={
                    "round_result": round_result,
                    "l1_stall_count": l1_stall_count,
                    "phase_ctx": dict(self._phase_ctx),
                },
            )
        )

    def _snapshot(
        self,
        event: str,
        ci: int,
        ct: int,
        payload: dict,
        *,
        round_num: int | None = None,
        sample_idx: int | None = None,
        sample_total: int | None = None,
    ) -> None:
        self._emit(
            SnapshotRecord(
                event=event,
                round=self._current_round if round_num is None else round_num,
                candidate_idx=ci,
                candidate_total=ct,
                sample_idx=sample_idx,
                sample_total=sample_total,
                payload=payload,
            )
        )

    def on_candidate_started(
        self, idx: int, total: int, changes_description: str, pp_override: dict | None
    ) -> None:
        self._snapshot(
            "candidate_started",
            idx,
            total,
            {"changes_description": changes_description, "pp_override": pp_override},
        )

    def on_candidate_scored(self, idx: int, total: int, scores: dict) -> None:
        self._snapshot(
            "candidate_scored", idx, total, {"scores": scores, "phase_ctx": dict(self._phase_ctx)}
        )

    def on_sample_started(self, ci: int, ct: int, qi: int, qt: int, query_text: str) -> None:
        self._snapshot(
            "sample_started", ci, ct, {"query_text": query_text}, sample_idx=qi, sample_total=qt
        )

    def on_sample_scored(self, ci: int, ct: int, qi: int, qt: int, result: dict) -> None:
        self._snapshot("sample_scored", ci, ct, {"result": result}, sample_idx=qi, sample_total=qt)

    def on_p_best_update(self, round_num: int, ci: int, ct: int, snapshot: Any) -> None:
        """Per-query PoBB snapshot — archive-only, not divergence-gated."""
        self._snapshot(
            "p_best_update",
            ci,
            ct,
            {
                "current_id": str(snapshot.current_id),
                "n_queries": int(snapshot.n_queries),
                "p_best": dict(snapshot.p_best),
            },
            round_num=round_num,
            sample_idx=int(snapshot.n_queries) - 1,
        )

    def set_round(self, round_num: int) -> None:
        self._current_round = round_num


async def _escalate_or_stop(
    cycle: Cycle,
    config: CampaignConfig,
    session: Session,
    round_num: int,
    cb: RunListener,
    *,
    escalation_check_result: dict | None = None,
) -> None:
    """Run L2 escalation; raise ``StopLoop`` if it returned a stop reason."""
    stop = await escalate_l2(
        cycle,
        config,
        session.pipeline_schema,
        round_num,
        cb.on_phase,
        obs=session.state.obs,
        tracing_campaign_id=session.state.tracing_campaign_id,
        escalation_check_result=escalation_check_result,
    )
    if stop:
        raise StopLoop(stop)


def _persist_round(
    cycle: Cycle, round_result: RoundResult, round_num: int, session: Session
) -> None:
    """Flush decisions, mirror to ledger, write round_data + log.md/review.md, flush recorder."""
    flushed: list[DecisionRecord] = []
    if cycle.pending_decisions:
        flushed = list(cycle.pending_decisions)
        cycle.pending_decisions.clear()
        round_result.decisions.extend(d.to_dict() for d in flushed)

    if (ledger := session.state.ledger) is not None:
        for d in flushed:
            ledger.append(d)
        ledger.append(
            PhaseRecord(
                phase="round",
                event="complete",
                round=round_num,
                payload={
                    "accuracy": round_result.accuracy,
                    "composite_fitness": round_result.composite_fitness,
                    "improved": round_result.improved,
                    "label": round_result.label,
                },
            )
        )

    if session.state.cycle_id:
        with graceful("Round checkpoint failed"):
            session.store.campaigns.save_round_file(
                session.backend_id,
                session.state.cycle_id,
                cycle.checkpoint(round_result, round_num),
            )
        write_log_md(session)
        write_review_md(session, cycle)
        with graceful("Tenant leaderboard refresh failed"):
            refresh_tenant_leaderboards(session)

    if _rr := session.state.audit_projection:
        _rr.flush()


def _refresh_axes_and_filter(
    cycle: Cycle,
    config: CampaignConfig,
    session: Session,
    dataset: list[Sample],
    round_num: int,
    cb: RunListener,
) -> None:
    """Refresh AxisIndex; round-boundary mutation #1 — prune zero-signal queries."""
    if not cycle.axes:
        return

    if session.store and session.backend_id:
        cycle.axes.refresh(
            session.store,
            session.backend_id,
            scorer=session.scoring.scorer,
            scorer_id=session.scoring.scorer_id,
            scorer_formula=session.scoring.scorer_formula,
        )

    zsf = config.optimization.zero_signal_filter_enabled
    dataset_name = session.dataset_name
    if not (zsf and dataset_name and session.store is not None):
        return

    with graceful("Zero-signal filter failed"):
        excluded = apply_zero_signal_exclusions(
            store=session.store,
            dataset_name=dataset_name,
            axes=cycle.axes,
            active_dataset=dataset,
            min_observations=config.optimization.zero_signal_filter_min_observations,
            campaign_id=session.state.cycle_id or "",
        )
        if not excluded:
            return
        excl_q = {e["query"] for e in excluded}
        session.scoring.scoring_set[:] = [
            s for s in session.scoring.scoring_set if s.query not in excl_q
        ]
        always_miss = sum(1 for e in excluded if e["hit_rate"] == 0.0)
        emit_phase(
            cb.on_phase,
            "zero_signal_filter",
            "applied",
            round=round_num,
            count=len(excluded),
            always_miss=always_miss,
            always_hit=len(excluded) - always_miss,
            examples=[e["query"] for e in excluded[:3]],
            dataset_name=dataset_name,
        )


def _evolve_scoring_set_if_enabled(
    cycle: Cycle,
    round_result: RoundResult,
    round_num: int,
    dataset: list[Sample],
    config: CampaignConfig,
    session: Session,
    cb: RunListener,
) -> None:
    """Round-boundary mutation #2 (off by default): Rasch+KG swap, in-memory only."""
    exp_cfg = config.optimization.exploration
    if not (exp_cfg.enabled and cycle.rounds):
        return

    from promptpotter.application.intelligence.exploration import (
        build_scoring_set_event,
        evolve_scoring_set,
    )

    with graceful("ScoringSet evolve failed"):
        ss_result = evolve_scoring_set(
            full_dataset=dataset,
            current_scoring_set=session.scoring.scoring_set,
            rounds=cycle.rounds,
            config=exp_cfg,
            elimination_n_min=config.optimization.elimination_n_min,
        )
        # Cache the posterior on the cycle so finalize can reuse it.
        if ss_result.rasch is not None:
            cycle.last_rasch_posterior = ss_result.rasch
        if not (ss_result.swapped_in or ss_result.swapped_out):
            return
        session.scoring.scoring_set[:] = ss_result.new_scoring_set
        event = build_scoring_set_event(round_num=round_num, result=ss_result)
        round_result.scoring_set_events.append(event)
        emit_phase(
            cb.on_phase,
            "scoring_set",
            "evolved",
            round=round_num,
            swapped_out=event["swapped_out"],
            swapped_in=event["swapped_in"],
            new_scoring_set_size=event["new_scoring_set_size"],
        )


async def _post_round(
    cycle: Cycle,
    round_result: RoundResult,
    round_num: int,
    config: CampaignConfig,
    session: Session,
    dataset: list[Sample],
    cb: RunListener,
) -> None:
    """Normal-path bookkeeping after a non-escalation round. Raises StopLoop on stop condition.

    The state machine observes the round outcome up front (bumping L1 stall,
    deciding CONTINUE / FIRE_L2 / STOP_*); the rest of this function persists
    the post-observe state and dispatches the chosen action.
    """
    event = cycle.escalation.observe_round(
        improved=round_result.improved,
        current_accuracy=cycle.tracking.current_accuracy,
        l1_patience=config.optimization.l1_patience,
        enable_l2=config.optimization.enable_l2,
    )
    if round_result.improved:
        cycle.opt_sp.clear_volatile()
    cb.on_round_complete(round_result, cycle.escalation.l1_stall_count)

    _persist_round(cycle, round_result, round_num, session)

    # Hot-swap composite_fitness formula via scoring_steer.json BEFORE round-boundary
    # mutations so next round evaluates under the new formula.
    with graceful("Scoring steer apply failed"):
        apply_steer_file(session, round_num, cb.on_phase)

    _refresh_axes_and_filter(cycle, config, session, dataset, round_num, cb)
    _evolve_scoring_set_if_enabled(cycle, round_result, round_num, dataset, config, session, cb)

    if event.stop_reason is not None:
        raise StopLoop(event.stop_reason)
    if event.next_action == NextAction.FIRE_L2:
        await _escalate_or_stop(cycle, config, session, round_num, cb)


async def _run_sweep_generation_only(
    cycle: Cycle,
    session: Session,
    cb: RunListener,
    round_num: int,
    *,
    label: str = "sweep_gen_only",
) -> None:
    """L1 variants without scoring; round_data JSON is minimal status='generation_only'."""
    cb.set_round(round_num)
    if (ledger := session.state.ledger) is not None:
        ledger.append(PhaseRecord(phase="round", event="enter", round=round_num))
    elif _rr := session.state.audit_projection:
        _rr.begin_round(round_num)

    candidates, yield_stats = await generate_or_load_candidates(
        round_num,
        cycle,
        cb.on_phase,
        n_eval_queries=0,
    )

    if session.state.cycle_id:
        with graceful("Sweep generation-only round_data write failed"):
            session.store.campaigns.save_round_file(
                session.backend_id,
                session.state.cycle_id,
                {
                    "round_id": f"round_{round_num}",
                    "round": round_num,
                    "label": label,
                    "status": "generation_only",
                    "accuracy": 0.0,
                    "composite_fitness": 0.0,
                    "hits": 0,
                    "total": 0,
                    "improved": False,
                    "candidates_scored": 0,
                    "candidate_scores": [],
                    "decisions": [],
                    "evaluators": {},
                    "l1_yield": yield_stats.l1_yield,
                    "l1_n_no_op": yield_stats.l1_n_no_op,
                    "l1_n_duplicate": yield_stats.l1_n_duplicate,
                    "opt_search_point": cycle.opt_sp.model_dump(),
                },
            )
        write_log_md(session)
        write_review_md(session, cycle)

    if (ledger := session.state.ledger) is not None:
        ledger.append(
            PhaseRecord(
                phase="round",
                event="complete",
                round=round_num,
                payload={"status": "generation_only", "n_candidates": len(candidates)},
            )
        )
    elif _rr := session.state.audit_projection:
        _rr.flush()


async def _force_l2(
    cycle: Cycle,
    config: CampaignConfig,
    session: Session,
    round_num: int,
    cb: RunListener,
) -> None:
    """Force L2 (bypass stall counter) — diag-mode bridge to round-2 peek."""
    await _escalate_or_stop(
        cycle,
        config,
        session,
        round_num,
        cb,
        escalation_check_result={"forced": "diag_mode"},
    )


async def _run_round_loop(
    cycle: Cycle,
    dataset: list[Sample],
    config: CampaignConfig,
    session: Session,
    cb: RunListener,
    *,
    sweep: bool = False,
    diag: bool = False,
) -> StopReason:
    """Round loop: generate → score → escalate → stop. sweep/diag halt after round 2."""
    opt = config.optimization
    hard_cap = opt.hard_cap
    round_num = session.state.resumed_from_round
    clean_rounds = session.state.resumed_from_round
    max_rounds = opt.max_rounds or 999

    try:
        while clean_rounds < max_rounds and round_num < hard_cap:
            is_probe = cycle.probe_next_round
            if is_probe:
                warned = {q for q, e in cycle.opt_sp.warning_inventory.items() if e.get("warnings")}
                round_eval_data = [s for s in dataset if s.query in warned]
                round_checks = None
            else:
                round_eval_data = session.scoring.scoring_set
                round_checks = session.scoring.degradation_checks

            logger.debug(
                "Round %d (clean=%d/%d, acc=%.3f, stall=%d/%d%s)",
                round_num,
                clean_rounds,
                max_rounds,
                cycle.tracking.current_accuracy,
                cycle.escalation.l1_stall_count,
                opt.l1_patience,
                ", PROBE" if is_probe else "",
            )

            # Ledger drives AuditTrailProjection.begin_round; direct call is
            # kept as no-ledger fallback (test fixtures, headless tools).
            cb.set_round(round_num)
            if (ledger := session.state.ledger) is not None:
                ledger.append(PhaseRecord(phase="round", event="enter", round=round_num))
            elif _rr := session.state.audit_projection:
                _rr.begin_round(round_num)

            round_result = await execute_round(
                cycle,
                round_num,
                round_eval_data,
                cb,
                degradation_checks=round_checks,
                skip_critique=sweep,
            )
            cycle.record_round(round_result, round_num)

            if cycle.axes and len(cycle.rounds) >= 2:
                cycle.axes.record_flips_from_rounds(cycle.rounds, round_num)

            if is_probe:
                cycle.probe_next_round = False
                if opt.enable_l2:
                    await _escalate_or_stop(cycle, config, session, round_num, cb)
                round_num += 1
                clean_rounds += 1
                continue

            if round_result.escalation_signal:
                signal = round_result.escalation_signal
                emit_phase(
                    cb.on_phase,
                    CampaignPhase.ESCALATION,
                    "enter",
                    round=round_num,
                    check_name=signal.check_name,
                    target=signal.target,
                    degraded_rate=signal.check_result.get("degraded_rate"),
                    warning_types=signal.check_result.get("warning_types"),
                )
                if signal.routes_to_optimizer and opt.enable_l2:
                    cycle.opt_sp.append_escalation(
                        build_escalation_entry(
                            round_num,
                            signal.check_result,
                            cycle.tracking.current_sp.pipeline_params
                            if cycle.tracking.current_sp
                            else None,
                        )
                    )
                    await _escalate_or_stop(
                        cycle,
                        config,
                        session,
                        round_num,
                        cb,
                        escalation_check_result=signal.check_result,
                    )
                elif signal.is_abort:
                    raise StopLoop(StopReason.ABORT)
                if session.state.cycle_id:
                    session.store.campaigns.delete_round_candidates(
                        session.backend_id,
                        session.state.cycle_id,
                        round_num + 1,
                    )
                emit_phase(cb.on_phase, CampaignPhase.ESCALATION, "exit", round=round_num)
                round_num += 1
                continue

            await _post_round(cycle, round_result, round_num, config, session, dataset, cb)
            round_num += 1
            clean_rounds += 1

            if sweep and clean_rounds >= 1:
                await _run_sweep_generation_only(cycle, session, cb, round_num)
                return StopReason.SWEEP_COMPLETE

            if diag and clean_rounds >= 1:
                # Force L2 on round-1 evidence (bypass stall), then peek round 2
                # with L2 overrides. round_num was incremented after _post_round.
                await _force_l2(cycle, config, session, round_num - 1, cb)
                await _run_sweep_generation_only(
                    cycle, session, cb, round_num, label="diag_gen_only"
                )
                return StopReason.DIAG_COMPLETE

        return StopReason.HARD_CAP if round_num >= hard_cap else StopReason.MAX_ROUNDS

    except StopLoop as sl:
        return sl.reason
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.warning("Optimization interrupted at round %d.", len(cycle.rounds))
        return StopReason.INTERRUPTED


async def run_optimization(
    dataset: list[Sample],
    campaign_config: CampaignConfig,
    *,
    session: Session,
    baseline: CampaignBaseline | None = None,
    listener: RunListener | None = None,
    experiment_id: str | None = None,
    task_context: TaskDecomposition | dict | None = None,
    display: Any = None,
    langfuse_session_id: str | None = None,
    resume_from_round_override: int | None = None,
    emitter: LiveDashboardProjection | None = None,
    no_divergence_check: bool = False,
    fork_on_divergence: bool = False,
    sweep: bool = False,
    diag: bool = False,
    fork_payload: SweepPayload | None = None,
) -> CycleResult:
    """Run optimization end-to-end; auto-mint session+cycle if not pre-set."""
    started_at = datetime.now(UTC).isoformat()
    cb = listener if listener is not None else RunListener()

    # Early-bind ledger + subscribers BEFORE baseline scoring so per-query
    # events from prepare_scoring_context reach dashboard.json + CLI live.
    early_bind_done = False
    if baseline is None and emitter is not None and session.store is not None:
        prompt_nodes = (
            session.pipeline_schema.prompt_node_names() if session.pipeline_schema else []
        )
        baseline_osp_for_id = load_baseline_prompt(
            session.experiment_extract or {},
            prompt_node_names=prompt_nodes,
            dataset_name=campaign_config.dataset_name,
        )
        if not session.session_id:
            auto_mint_session(
                session,
                campaign_config,
                cycle_id=build_baseline_cycle_id(
                    baseline_osp_for_id, session.pipeline_schema, dataset
                ),
                baseline_acc=0.0,
                baseline_prompt_fields=baseline_osp_for_id.prompt_field_dict(),
                dataset_size=len(dataset),
                experiment_id=experiment_id,
            )
        if session.state.cycle_id:
            cycle_dir = CycleDir(session.store.campaigns.campaign_dir(session.state.cycle_id))
            session.state.ledger = CycleLedger.open(cycle_dir)
            ledger = session.state.ledger
            ledger.bind(emitter)
            if session.state.audit_projection is not None:
                ledger.bind(session.state.audit_projection)
            if display is not None:
                ledger.bind(display)
            ledger.bind(PoBBStreamProjection.from_cycle_dir(cycle_dir))
            cb.ledger = ledger
            early_bind_done = True

    if baseline is None:
        _, _, campaign_rounds, _ = await prepare_scoring_context(
            session.experiment_extract,
            dataset,
            campaign_config,
            pipeline_params=session.pipeline_params,
            pipeline_schema=session.pipeline_schema,
            svc=session,
            listener=cb,
        )
        baseline = extract_campaign_baseline(campaign_rounds)
        if display is not None and hasattr(display, "set_baseline"):
            display.set_baseline(baseline.baseline_acc)

    if not session.session_id:
        ps = baseline.baseline_ps
        baseline_prompt_fields = (
            ps.prompt_field_dict() if isinstance(ps, OptSearchPoint) else (ps or {})
        )
        baseline_osp = OptSearchPoint.from_prompt_fields(baseline_prompt_fields)
        auto_mint_session(
            session,
            campaign_config,
            cycle_id=build_baseline_cycle_id(baseline_osp, session.pipeline_schema, dataset),
            baseline_acc=baseline.baseline_acc,
            baseline_prompt_fields=baseline_prompt_fields,
            dataset_size=len(dataset),
            experiment_id=experiment_id,
        )

    scoring_spec = split_scoring_block(campaign_config.scoring)

    if isinstance(task_context, TaskDecomposition):
        resolved_task_context = task_context
    elif isinstance(task_context, dict):
        resolved_task_context = TaskDecomposition.from_dict(task_context)
    else:
        resolved_task_context = TaskDecomposition()

    pre_loop_cycle_id = session.state.cycle_id

    cycle = await init_optimization_loop(
        baseline,
        dataset,
        campaign_config,
        cb=cb,
        task_context=resolved_task_context,
        scoring_formula=scoring_spec.per_query,
        scoring_round_formula=scoring_spec.per_round,
        scorer_id=scoring_spec.scorer_id,
        no_divergence_check=no_divergence_check,
        fork_on_divergence=fork_on_divergence,
        langfuse_session_id=langfuse_session_id,
        cycle_id=session.state.cycle_id or None,
        resume_from_round_override=resume_from_round_override,
        experiment_id=experiment_id or "",
        session=session,
        started_at=started_at,
    )

    # Sweep-fork: stamp operator's L1-surface deltas onto the fresh OSP.
    if fork_payload is not None:
        apply_sweep_payload_to_osp(cycle.opt_sp, fork_payload)

    # Fork-on-divergence: rebind AuditTrailProjection to the fork's own dir
    # (live dashboard stays family-root-anchored).
    forked = (
        pre_loop_cycle_id and session.state.cycle_id and pre_loop_cycle_id != session.state.cycle_id
    )
    if forked and session.state.cycle_id and session.store is not None:
        cycle_dir = CycleDir(session.store.campaigns.campaign_dir(session.state.cycle_id))
        session.state.audit_projection = AuditTrailProjection.from_cycle_dir(cycle_dir)
        session.state.audit_projection.rehydrate_sticky()
        fork_ledger = CycleLedger.open(cycle_dir)
        # Re-open parent so the offset reflects _fork_at_divergence's FORK_CUT
        # append (which used a separate CycleLedger instance).
        if pre_loop_cycle_id:
            parent_dir = CycleDir(session.store.campaigns.campaign_dir(pre_loop_cycle_id))
            fresh_parent = CycleLedger.open(parent_dir)
            fork_ledger.inherit_from(fresh_parent, fresh_parent.next_offset)
        session.state.ledger = fork_ledger

    if emitter is None:
        emitter = build_campaign_emitter(
            session,
            campaign_config,
            baseline_accuracy=baseline.baseline_acc,
            resumed_from_round=session.state.resumed_from_round,
            recorder=session.state.audit_projection,
        )
    elif forked and session.state.cycle_id and pre_loop_cycle_id:
        emitter._recorder = session.state.audit_projection
        emitter.log_fork(
            old_cycle_id=pre_loop_cycle_id,
            new_cycle_id=session.state.cycle_id,
            from_round=session.state.resumed_from_round or 0,
        )
    elif early_bind_done and emitter._recorder is None:
        # init_optimization_loop built the recorder after early-bind; patch on.
        emitter._recorder = session.state.audit_projection
    # Late-bind subscribers: skip when early-bind already ran on the same
    # ledger (would double-subscribe), but always re-bind on fork.
    late_ledger = session.state.ledger
    if late_ledger is not None and (not early_bind_done or forked):
        late_ledger.bind(emitter)
        if session.state.audit_projection is not None:
            late_ledger.bind(session.state.audit_projection)
        if display is not None:
            late_ledger.bind(display)
        if session.state.cycle_id and session.store is not None:
            late_ledger.bind(
                PoBBStreamProjection.from_cycle_dir(
                    CycleDir(session.store.campaigns.campaign_dir(session.state.cycle_id))
                )
            )
        cb.ledger = late_ledger

    stop_reason = await _run_round_loop(
        cycle, dataset, campaign_config, session, cb, sweep=sweep, diag=diag
    )

    finished_at = datetime.now(UTC).isoformat()
    cycle_result = CycleResult(
        rounds=cycle.rounds,
        n_rounds=len(cycle.rounds),
        best_accuracy=cycle.tracking.best_accuracy,
        best_round=cycle.tracking.best_round,
        baseline_accuracy=baseline.baseline_acc,
        winner_prompt_fields=cycle.opt_sp.prompt_field_dict() if cycle.tracking.best_sp else {},
        winner_pipeline_params=cycle.tracking.best_sp.pipeline_params
        if cycle.tracking.best_sp
        else None,
        stop_reason=stop_reason,
        started_at=started_at,
        finished_at=finished_at,
        cycle_id=session.state.cycle_id,
        session_id=session.session_id or None,
        resumed_from_round=session.state.resumed_from_round,
    )
    _finalize_run(cycle, session, emitter, cycle_result, campaign_config, sweep=sweep, diag=diag)
    return cycle_result


def _finalize_run(
    cycle: Cycle,
    session: Session,
    emitter: LiveDashboardProjection | None,
    cycle_result: CycleResult,
    campaign_config: CampaignConfig,
    *,
    sweep: bool = False,
    diag: bool = False,
) -> None:
    """Mark cycle finished, fold summary into index.json::final, render log.md, finalize emitter."""
    stop_reason = cycle_result.stop_reason
    if session.state.cycle_id:
        status_map = {
            str(StopReason.INTERRUPTED): "interrupted",
        }
        session.store.campaigns.mark_finished(
            session.backend_id,
            session.state.cycle_id,
            status=status_map.get(stop_reason, "completed"),
            stop_reason=stop_reason,
            best_accuracy=cycle_result.best_accuracy,
            best_round=cycle_result.best_round,
            n_rounds=cycle_result.n_rounds,
            finished_at=cycle_result.finished_at,
        )

    obs = session.state.obs
    if obs:
        obs.end_campaign(
            session.state.tracing_campaign_id,
            best_accuracy=cycle_result.best_accuracy,
            n_rounds=cycle_result.n_rounds,
            stop_reason=stop_reason,
            best_round=cycle_result.best_round,
        )

    cycle_result.langfuse_trace_id = (
        None
        if stop_reason == StopReason.INTERRUPTED or obs is None
        else obs.get_langfuse_trace_id(session.state.tracing_campaign_id)
    )

    if session.state.cycle_id:
        with graceful("Final summary write failed"):
            from promptpotter.application.scoring.evaluators import (
                default_per_round_formula,
                default_per_round_formula_short,
            )

            schema = session.pipeline_schema
            if session.scoring.scorer_round_formula:
                formula_full: str | None = session.scoring.scorer_round_formula
                formula_short: str | None = None
            elif schema is not None:
                formula_full = default_per_round_formula(schema)
                formula_short = default_per_round_formula_short(schema)
            else:
                formula_full = None
                formula_short = None
            baseline_composite_fitness = cycle.tracking.baseline_composite_fitness
            final_block = cycle_result.model_dump(exclude={"rounds"}, mode="json")
            final_block["scorer_round_formula"] = formula_full
            final_block["scorer_round_formula_short"] = formula_short
            final_block["baseline_composite_fitness"] = baseline_composite_fitness
            from promptpotter.application.optimization.llm_call import (
                compute_optimizer_prompt_hashes,
            )

            final_block["prompt_hashes"] = compute_optimizer_prompt_hashes()
            if sweep:
                final_block["mode"] = "sweep"
            elif diag:
                final_block["mode"] = "diag"
            else:
                final_block["mode"] = "full"
            if diag:
                # L2-evolved L1 surface that operator promotes by running
                # plain optimize on the diag fork.
                osp = cycle.opt_sp
                final_block["diag"] = {
                    "l2_brief": (osp.l2_brief or "").strip(),
                    "l1_section_overrides": dict(osp.l1_section_overrides or {}),
                    "l1_section_overrides_text_keys": sorted(
                        (osp.l1_section_overrides_text or {}).keys()
                    ),
                    "l1_template_override": bool(osp.l1_template_override),
                }
            # Seal top-level baseline against final.baseline_accuracy drift.
            session.store.campaigns.update(
                session.backend_id,
                session.state.cycle_id,
                {"final": final_block, "baseline_accuracy": cycle_result.baseline_accuracy},
            )

    if emitter:
        from promptpotter.application.intelligence.hard_sample_sorter import (
            build_hard_samples_artifact,
        )

        exp_cfg = campaign_config.optimization.exploration
        artifact: dict | None = None
        if exp_cfg.hard_sample_sorter_enabled:
            with graceful("Hard-sample artifact build failed"):
                artifact = build_hard_samples_artifact(
                    cycle.rounds,
                    cycle_id=session.state.cycle_id,
                    top_k_candidates=exp_cfg.top_k_candidates,
                    top_k_samples=exp_cfg.top_k_samples,
                    posterior=cycle.last_rasch_posterior,
                )
        write_log_md(session, hard_samples_artifact=artifact)
        write_review_md(session, cycle)
        with graceful("Tenant leaderboard refresh failed"):
            refresh_tenant_leaderboards(session)
