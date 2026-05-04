"""Optimization loop orchestrator — L1 generate → score → escalate, counter-based."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.application.baseline import CampaignBaseline
from promptpotter.application.bootstrap import (
    Session,
    init_optimization_loop,
)
from promptpotter.application.config import CampaignConfig
from promptpotter.application.optimization.cycle import (
    Cycle,
    apply_sweep_payload_to_osp,
    build_escalation_entry,
    escalate_l2,
)
from promptpotter.application.optimization.l1 import execute_round
from promptpotter.application.review import render_review_md
from promptpotter.application.scoring.formula import (
    apply_steer_file,
    apply_zero_signal_exclusions,
)
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.phases import (
    CampaignPhase,
    StopLoop,
    StopReason,
    emit_phase,
)
from promptpotter.domain.results import RoundResult, RunResult
from promptpotter.domain.run_records import Phase, Snapshot, SweepPayload
from promptpotter.domain.sample import Sample
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.infrastructure.projections import LiveDashboardProjection
from promptpotter.presentation.views.render_markdown import to_markdown
from promptpotter.presentation.views.view_factories import (
    from_disk_log,
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
    """Single ingress for in-loop events. Builds typed ``RunRecord`` instances
    and appends them to the per-cycle ``RunLedger``; subscribers (live
    dashboard projection, audit trail projection, terminal display)
    consume via ``on_record`` only.

    Owns the phase-view ctx so ``from_phase_event`` (stateful — reads/writes
    ctx) runs once per ``PhaseEvent``; the typed view is serialised via
    ``view_to_wire_dict`` onto ``Phase.payload['view']`` so subscribers
    don't re-derive it.

    Pre-binding events (e.g. ``INIT.enter`` fired before the cycle dir is
    known) buffer in memory; the first append after ``ledger`` is set
    drains the buffer so subscribers see the full history."""

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
        """Bind the ledger and drain any pre-binding buffer in order.

        Subscribers must be bound to the ledger BEFORE this assignment so
        they receive the buffered records via ``on_record`` when the
        drain fires.
        """
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
            Phase(
                phase=str(event.phase),
                event=str(event.event),
                round=event.round,
                payload={"view": view, "data": event.data},
            )
        )

    def on_round_complete(self, round_result: Any, l1_stall_count: int) -> None:
        self._phase_ctx["l1_stall_count"] = l1_stall_count
        self._emit(
            Phase(
                phase="round",
                event="complete",
                round=getattr(round_result, "round", self._current_round),
                payload={
                    "round_result": round_result,
                    "l1_stall_count": l1_stall_count,
                    "phase_ctx": dict(self._phase_ctx),
                },
            )
        )

    def on_candidate_started(
        self, idx: int, total: int, changes_description: str, pp_override: dict | None
    ) -> None:
        self._emit(
            Snapshot(
                event="candidate_started",
                round=self._current_round,
                candidate_idx=idx,
                candidate_total=total,
                payload={
                    "changes_description": changes_description,
                    "pp_override": pp_override,
                },
            )
        )

    def on_candidate_scored(self, idx: int, total: int, scores: dict) -> None:
        self._emit(
            Snapshot(
                event="candidate_scored",
                round=self._current_round,
                candidate_idx=idx,
                candidate_total=total,
                payload={
                    "scores": scores,
                    "phase_ctx": dict(self._phase_ctx),
                },
            )
        )

    def on_sample_started(self, ci: int, ct: int, qi: int, qt: int, query_text: str) -> None:
        self._emit(
            Snapshot(
                event="sample_started",
                round=self._current_round,
                candidate_idx=ci,
                candidate_total=ct,
                sample_idx=qi,
                sample_total=qt,
                payload={"query_text": query_text},
            )
        )

    def on_sample_scored(self, ci: int, ct: int, qi: int, qt: int, result: dict) -> None:
        self._emit(
            Snapshot(
                event="sample_scored",
                round=self._current_round,
                candidate_idx=ci,
                candidate_total=ct,
                sample_idx=qi,
                sample_total=qt,
                payload={"result": result},
            )
        )

    def on_p_best_update(self, round_num: int, ci: int, ct: int, snapshot: Any) -> None:
        """Per-query Posterior-of-Being-Best snapshot from PoBBCheck.

        Archive-only — observability stream, not a divergence-gated decision.
        Subscribed by the live dashboard projection (merges into
        ``current_round.nodes.candidates[].p_best``) and the JSONL stream
        projection (one record per query).
        """
        self._emit(
            Snapshot(
                event="p_best_update",
                round=round_num,
                candidate_idx=ci,
                candidate_total=ct,
                sample_idx=int(snapshot.n_queries) - 1,
                payload={
                    "current_id": str(snapshot.current_id),
                    "n_queries": int(snapshot.n_queries),
                    "p_best": dict(snapshot.p_best),
                },
            )
        )

    def set_round(self, round_num: int) -> None:
        """Track the active round for Snapshot record context."""
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
        obs_campaign_id=session.state.obs_campaign_id,
        escalation_check_result=escalation_check_result,
    )
    if stop:
        raise StopLoop(stop)


def _advance_l1_stall(cycle: Cycle, round_result: RoundResult, cb: RunListener) -> None:
    """Bump (or reset) the L1 stall counter, clear volatile state on improvement, fan out."""
    cycle.escalation.l1_stall_count = (
        0 if round_result.improved else cycle.escalation.l1_stall_count + 1
    )
    if round_result.improved:
        cycle.opt_sp.clear_volatile()
    cb.on_round_complete(round_result, cycle.escalation.l1_stall_count)


def _persist_round(
    cycle: Cycle, round_result: RoundResult, round_num: int, session: Session
) -> None:
    """Flush pending decisions onto the round, mirror to ledger, write trial + log.md, flush recorder.

    Ledger dual-write is Phase 3 of the persistence cleanup: every
    decision and the round-boundary phase event land on the per-cycle
    ``events.jsonl`` alongside the legacy trial-JSON write. Phase 5
    will retire the legacy paths and let projections subscribe to the
    ledger for their views.
    """
    if cycle.pending_decisions:
        flushed = list(cycle.pending_decisions)
        cycle.pending_decisions.clear()
        round_result.decisions.extend(d.to_dict() for d in flushed)
        if (ledger := session.state.ledger) is not None:
            for d in flushed:
                ledger.append(d.model_copy(update={"round": round_num}))

    if (ledger := session.state.ledger) is not None:
        ledger.append(
            Phase(
                phase="round",
                event="complete",
                round=round_num,
                payload={
                    "accuracy": round_result.accuracy,
                    "composite": round_result.composite,
                    "improved": round_result.improved,
                    "label": round_result.label,
                },
            )
        )

    if session.state.cycle_id:
        with graceful("Round checkpoint failed"):
            session.store.campaigns.add_trial(
                session.backend_id,
                session.state.cycle_id,
                cycle.checkpoint(round_result, round_num),
            )
        _write_log_md(session)
        _write_review_md(session, cycle)
        with graceful("Tenant leaderboard refresh failed"):
            _refresh_tenant_leaderboards(session)

    if _rr := session.state.round_recorder:
        _rr.flush()


def _refresh_axes_and_filter(
    cycle: Cycle,
    config: CampaignConfig,
    session: Session,
    dataset: list[Sample],
    round_num: int,
    cb: RunListener,
) -> None:
    """Refresh AxisIndex from the archive, then prune zero-signal queries when enabled.

    The zero-signal filter is round-boundary mutation #1: it physically
    moves always-hit/always-miss queries to ``datasets/{name}.json::excluded``
    once an axis has ≥ N observations, and drops them from the active
    scoring set in memory.
    """
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
        session.scoring.scoring_dataset[:] = [
            s for s in session.scoring.scoring_dataset if s.query not in excl_q
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
    """Round-boundary mutation #2: Rasch + KG swap on the active scoring set.

    Off by default. Trades understood samples for high-info samples in
    ``session.scoring.scoring_dataset`` (in-memory only — never disk).
    """
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
            current_scoring_set=session.scoring.scoring_dataset,
            rounds=cycle.rounds,
            config=exp_cfg,
            elimination_n_min=config.optimization.elimination_n_min,
        )
        # Cache the posterior on the cycle so finalize can reuse it.
        if ss_result.rasch is not None:
            cycle.last_rasch_posterior = ss_result.rasch
        if not (ss_result.swapped_in or ss_result.swapped_out):
            return
        session.scoring.scoring_dataset[:] = ss_result.new_scoring_set
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


async def _check_stop_or_escalate(
    cycle: Cycle,
    config: CampaignConfig,
    session: Session,
    round_num: int,
    cb: RunListener,
) -> None:
    """Raise ``StopLoop`` on perfect/patience, or trigger L2 escalation when stalled."""
    if cycle.current_accuracy >= 1.0:
        raise StopLoop(StopReason.PERFECT)
    if cycle.escalation.l1_stall_count >= config.optimization.l1_patience:
        if not config.optimization.enable_l2:
            raise StopLoop(StopReason.PATIENCE)
        await _escalate_or_stop(cycle, config, session, round_num, cb)
        cycle.escalation.l1_stall_count = 0


async def _post_round(
    cycle: Cycle,
    round_result: RoundResult,
    round_num: int,
    config: CampaignConfig,
    session: Session,
    dataset: list[Sample],
    cb: RunListener,
) -> None:
    """Normal-path bookkeeping after a non-escalation round. Raises StopLoop on stop condition."""
    _advance_l1_stall(cycle, round_result, cb)
    _persist_round(cycle, round_result, round_num, session)

    # Operator-driven composite-score steering: hot-swap the per-round
    # formula when ``campaigns/{cycle_id}/scoring_steer.json`` is present.
    # Sits before the round-boundary mutation block so the next round's
    # candidates are evaluated under the new formula.
    with graceful("Scoring steer apply failed"):
        apply_steer_file(session, round_num, cb.on_phase)

    _refresh_axes_and_filter(cycle, config, session, dataset, round_num, cb)
    _evolve_scoring_set_if_enabled(cycle, round_result, round_num, dataset, config, session, cb)
    await _check_stop_or_escalate(cycle, config, session, round_num, cb)


async def _run_sweep_generation_only(
    cycle: Cycle,
    session: Session,
    cb: RunListener,
    round_num: int,
    *,
    label: str = "sweep_gen_only",
) -> None:
    """Emit L1 variants without scoring; audit projection captures ``l1_generate`` via the recorder, trial JSON is the minimal ``status="generation_only"`` record."""
    from promptpotter.application.optimization.l1 import _generate_or_load_candidates

    cb.set_round(round_num)
    if (ledger := session.state.ledger) is not None:
        ledger.append(Phase(phase="round", event="enter", round=round_num))
    elif _rr := session.state.round_recorder:
        _rr.begin_round(round_num)

    candidates, yield_stats = await _generate_or_load_candidates(
        round_num,
        cycle,
        cb.on_phase,
        n_eval_queries=0,
    )

    if session.state.cycle_id:
        with graceful("Sweep generation-only trial write failed"):
            session.store.campaigns.add_trial(
                session.backend_id,
                session.state.cycle_id,
                {
                    "trial_id": f"round_{round_num}",
                    "round": round_num,
                    "label": label,
                    "status": "generation_only",
                    "accuracy": 0.0,
                    "composite": 0.0,
                    "hits": 0,
                    "total": 0,
                    "improved": False,
                    "candidates_scored": 0,
                    "candidate_scores": [],
                    "decisions": [],
                    "evaluators": {},
                    "l1_yield": yield_stats.yield_,
                    "l1_n_no_op": yield_stats.n_no_op,
                    "l1_n_duplicate": yield_stats.n_duplicate,
                    "opt_search_point": cycle.opt_sp.model_dump(),
                    **cycle.escalation.to_checkpoint_dict(),
                },
            )
        _write_log_md(session)
        _write_review_md(session, cycle)

    if (ledger := session.state.ledger) is not None:
        ledger.append(
            Phase(
                phase="round",
                event="complete",
                round=round_num,
                payload={"status": "generation_only", "n_candidates": len(candidates)},
            )
        )
    elif _rr := session.state.round_recorder:
        _rr.flush()


async def _force_l2(
    cycle: Cycle,
    config: CampaignConfig,
    session: Session,
    round_num: int,
    cb: RunListener,
) -> None:
    """Fire L2-context unconditionally — diag mode's bridge between round 1
    and the round-2 generation peek.

    Wraps :func:`_escalate_or_stop` with a ``forced=diag_mode`` marker so
    L2 reads round-1 evidence and writes its directive + L1-surface
    overrides onto ``cycle.opt_sp`` even though no stall has accumulated.
    """
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
    """Execute the round loop: generate → score → escalate → stop.

    ``sweep=True`` runs M10's cheap-trial mode: one full scored round, then
    a generation-only round (L1 produces variants, no scoring) for the
    review surface to compare against. Halts with ``SWEEP_COMPLETE`` after
    the gen-only round persists.

    ``diag=True`` runs M10's diagnostic mode: one full scored round, then
    L2-context fires (forced, regardless of stall counter), then a
    generation-only round 2 with L2's overrides applied. Halts with
    ``DIAG_COMPLETE``.
    """
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
                round_eval_data = session.scoring.scoring_dataset
                round_checks = session.scoring.degradation_checks

            logger.debug(
                "Round %d (clean=%d/%d, acc=%.3f, stall=%d/%d%s)",
                round_num,
                clean_rounds,
                max_rounds,
                cycle.current_accuracy,
                cycle.escalation.l1_stall_count,
                opt.l1_patience,
                ", PROBE" if is_probe else "",
            )

            # Round-enter on the ledger drives AuditTrailProjection.begin_round
            # via on_record. Direct begin_round is kept as the no-ledger path
            # (test fixtures, headless tools) so behaviour is unchanged when
            # session.state.ledger is None.
            cb.set_round(round_num)
            if (ledger := session.state.ledger) is not None:
                ledger.append(Phase(phase="round", event="enter", round=round_num))
            elif _rr := session.state.round_recorder:
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
                            cycle.current_sp.pipeline_params if cycle.current_sp else None,
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
                # Force L2 to fire on round-1 evidence (bypasses stall counter),
                # then run the round-2 L1-generate peek with L2's overrides
                # applied. ``round_num`` was incremented after _post_round, so
                # the just-completed round is ``round_num - 1``.
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
) -> RunResult:
    """Run optimization end-to-end. Runs baseline when ``baseline`` is None. Returns RunResult.

    ``session.session_id`` / ``session.state.cycle_id`` carry the active ids;
    callers set them before calling. When ``session.session_id`` is empty
    (notebook entry path), this auto-mints a fresh session+cycle.

    ``sweep=True`` is M10's cheap-trial mode (Track 5): one full scored
    round, then a generation-only round (variants emitted but not scored)
    for the review surface to compare across candidate L1 prompts. The
    cycle's ``index.json::final.mode`` lands as ``"sweep"`` so the
    leaderboard can pair sweep cycles with their full counterparts.
    """
    started_at = datetime.now(UTC).isoformat()
    cb = listener if listener is not None else RunListener()

    # Bind the cycle ledger + subscribers BEFORE baseline scoring so the
    # per-query events from prepare_scoring_context flow to dashboard.json
    # and CLI stdout live, instead of buffering on RunListener until the
    # late bind block below. CLI builds emitter+display ahead of time
    # (see ``_build_run_observers``); we just need a ledger to plug them
    # into. Skipped when no store is wired (test fixtures) or when the
    # caller already scored baseline (notebook path with baseline != None).
    early_bind_done = False
    if baseline is None and emitter is not None and session.store is not None:
        from promptpotter.application.baseline import load_baseline_prompt
        from promptpotter.application.bootstrap import auto_mint_session
        from promptpotter.domain.cycle_paths import CycleDir
        from promptpotter.infrastructure.ledger import RunLedger

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
            session.state.ledger = RunLedger.open(cycle_dir)
            ledger = session.state.ledger
            ledger.bind(emitter)
            if session.state.round_recorder is not None:
                ledger.bind(session.state.round_recorder)
            if display is not None:
                ledger.bind(display)
            from promptpotter.infrastructure.projections import PoBBStreamProjection

            ledger.bind(PoBBStreamProjection.from_cycle_dir(cycle_dir))
            cb.ledger = ledger
            early_bind_done = True

    if baseline is None:
        from promptpotter.application.baseline import (
            extract_campaign_baseline,
            prepare_scoring_context,
        )

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
        from promptpotter.application.bootstrap import auto_mint_session

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

    from promptpotter.application.scoring.formula import split_scoring_block

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

    # Sweep-fork override stamp. After bootstrap returns the cycle but
    # before the round loop reads ``cycle.opt_sp``, apply the operator's
    # L1-surface deltas. The cycle's existing checkpoint code then
    # persists the stamped OSP into trial JSONs via the same path that
    # JobSearchPoint already round-trips.
    if fork_payload is not None:
        apply_sweep_payload_to_osp(cycle.opt_sp, fork_payload)

    # Fork-on-divergence rebinding. ``init_optimization_loop`` may have
    # minted a new fork cycle and updated ``session.state.cycle_id``. The
    # live dashboard projection is family-root-anchored so its telemetry
    # paths stay correct, but the ``AuditTrailProjection`` writes
    # ``.runtime/cache/rounds/round_NNN.json`` per cycle and must be rebuilt to
    # point at the fork's own dir. Output.log gets a banner so the
    # operator can see the cutover inline.
    forked = (
        pre_loop_cycle_id and session.state.cycle_id and pre_loop_cycle_id != session.state.cycle_id
    )
    if forked and session.state.cycle_id and session.store is not None:
        from promptpotter.domain.cycle_paths import CycleDir
        from promptpotter.infrastructure.ledger import RunLedger
        from promptpotter.infrastructure.projections import AuditTrailProjection

        cycle_dir = CycleDir(session.store.campaigns.campaign_dir(session.state.cycle_id))
        session.state.round_recorder = AuditTrailProjection.from_cycle_dir(cycle_dir)
        session.state.round_recorder.rehydrate_sticky()
        fork_ledger = RunLedger.open(cycle_dir)
        # Re-open the parent so the offset count reflects ``_fork_at_divergence``'s
        # FORK_CUT append (which used a separate RunLedger instance and so
        # didn't bump the in-memory cursor of ``session.state.ledger``).
        # The fork inherits the parent's full history up to the fresh tail
        # so a tail of ``fork.iter()`` sees the FORK_CUT marker before the
        # fork's own appends.
        if pre_loop_cycle_id:
            parent_dir = CycleDir(session.store.campaigns.campaign_dir(pre_loop_cycle_id))
            fresh_parent = RunLedger.open(parent_dir)
            fork_ledger.inherit_from(fresh_parent, fresh_parent.next_offset)
        session.state.ledger = fork_ledger

    if emitter is None:
        from promptpotter.application.baseline import build_campaign_emitter

        emitter = build_campaign_emitter(
            session,
            campaign_config,
            baseline_accuracy=baseline.baseline_acc,
            resumed_from_round=session.state.resumed_from_round,
            recorder=session.state.round_recorder,
        )
    elif forked and session.state.cycle_id and pre_loop_cycle_id:
        emitter._recorder = session.state.round_recorder
        emitter.log_fork(
            old_cycle_id=pre_loop_cycle_id,
            new_cycle_id=session.state.cycle_id,
            from_round=session.state.resumed_from_round or 0,
        )
    elif early_bind_done and emitter._recorder is None:
        # Recorder may have been built by init_optimization_loop after
        # we early-bound the emitter; patch it on so per-round node
        # snapshots reach dashboard.json::current_round.nodes.
        emitter._recorder = session.state.round_recorder
    # Ledger subscription: every subscriber consumes via ``on_record``;
    # there is no parallel direct-callback path. Display binds last so
    # any pre-binding events buffered on the listener (e.g. ``INIT.enter``
    # fired before the cycle dir was known) flush in order. When early
    # bind already ran we still re-bind on fork-on-divergence (fork ledger
    # replaced session.state.ledger) but skip the no-fork case to avoid
    # double-subscribing.
    late_ledger = session.state.ledger
    if late_ledger is not None and (not early_bind_done or forked):
        late_ledger.bind(emitter)
        if session.state.round_recorder is not None:
            late_ledger.bind(session.state.round_recorder)
        if display is not None:
            late_ledger.bind(display)
        if session.state.cycle_id and session.store is not None:
            from promptpotter.domain.cycle_paths import CycleDir
            from promptpotter.infrastructure.projections import PoBBStreamProjection

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
    run_result = RunResult(
        rounds=cycle.rounds,
        n_rounds=len(cycle.rounds),
        best_accuracy=cycle.best_accuracy,
        best_round=cycle.best_round,
        baseline_accuracy=baseline.baseline_acc,
        winner_prompt_fields=cycle.opt_sp.prompt_field_dict() if cycle.best_sp else {},
        winner_pipeline_params=cycle.best_sp.pipeline_params if cycle.best_sp else None,
        stop_reason=stop_reason,
        started_at=started_at,
        finished_at=finished_at,
        cycle_id=session.state.cycle_id,
        session_id=session.session_id or None,
        resumed_from_round=session.state.resumed_from_round,
    )
    _finalize_run(cycle, session, emitter, run_result, campaign_config, sweep=sweep, diag=diag)
    return run_result


def _finalize_run(
    cycle: Cycle,
    session: Session,
    emitter: LiveDashboardProjection | None,
    run_result: RunResult,
    campaign_config: CampaignConfig,
    *,
    sweep: bool = False,
    diag: bool = False,
) -> None:
    """Mark cycle finished, fold the run summary into ``index.json::final``,
    render ``log.md`` (with the hard-samples heatmap inlined when the sorter
    ran), finalize the emitter. Mutates ``run_result.langfuse_trace_id``."""
    stop_reason = run_result.stop_reason
    if session.state.cycle_id:
        status_map = {
            str(StopReason.INTERRUPTED): "interrupted",
        }
        session.store.campaigns.mark_finished(
            session.backend_id,
            session.state.cycle_id,
            status=status_map.get(stop_reason, "completed"),
            stop_reason=stop_reason,
            best_accuracy=run_result.best_accuracy,
            best_round=run_result.best_round,
            n_rounds=run_result.n_rounds,
            finished_at=run_result.finished_at,
        )

    obs = session.state.obs
    if obs:
        obs.end_campaign(
            session.state.obs_campaign_id,
            best_accuracy=run_result.best_accuracy,
            n_rounds=run_result.n_rounds,
            stop_reason=stop_reason,
            best_round=run_result.best_round,
        )

    run_result.langfuse_trace_id = (
        None
        if stop_reason == StopReason.INTERRUPTED or obs is None
        else obs.get_langfuse_trace_id(session.state.obs_campaign_id)
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
            baseline_composite = cycle.baseline_composite
            final_block = run_result.model_dump(exclude={"rounds"}, mode="json")
            final_block["scorer_round_formula"] = formula_full
            final_block["scorer_round_formula_short"] = formula_short
            final_block["baseline_composite"] = baseline_composite
            from promptpotter.application.optimization.pipeline import (
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
                # Diag mode's payload: the L2-evolved L1 surface that operator
                # promotes by running plain optimize on the diag fork.
                osp = cycle.opt_sp
                final_block["diag"] = {
                    "l2_directive": (osp.l2_directive or "").strip(),
                    "l1_section_overrides": dict(osp.l1_section_overrides or {}),
                    "l1_section_overrides_text_keys": sorted(
                        (osp.l1_section_overrides_text or {}).keys()
                    ),
                    "l1_template_override": bool(osp.l1_template_override),
                }
            session.store.campaigns.update(
                session.backend_id,
                session.state.cycle_id,
                {
                    "final": final_block,
                    # Seal top-level baseline against the truth source so it
                    # cannot drift from ``final.baseline_accuracy``. Belt-and-
                    # braces over Fix 2 in ``bootstrap_cycle``.
                    "baseline_accuracy": run_result.baseline_accuracy,
                },
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
                    posterior=getattr(cycle, "last_rasch_posterior", None),
                )
        _write_log_md(session, hard_samples_artifact=artifact)
        _write_review_md(session, cycle)
        with graceful("Tenant leaderboard refresh failed"):
            _refresh_tenant_leaderboards(session)
        emitter.finalize()


def _write_log_md(session: Session, *, hard_samples_artifact: dict | None = None) -> None:
    """Render ``log.md`` for the current cycle. When the current cycle is a
    fork, also re-render the family root's ``log.md`` so its ``## Forks``
    section reflects this fork's latest result. Pure derived view; wrapped
    in ``graceful()`` so a render bug never breaks the run."""
    if not session.state.cycle_id or session.store is None:
        return
    with graceful("log.md render failed"):
        store = session.store.campaigns
        cycle_id = session.state.cycle_id
        _render_log_md_for(store, session.backend_id, cycle_id, hard_samples_artifact)
        from promptpotter.infrastructure.store.stores import root_cycle_id

        root_id = root_cycle_id(cycle_id)
        if root_id != cycle_id:
            # Fork finished — refresh the family root's log.md so its Forks
            # section picks up this fork's latest best/baseline/stop_reason.
            _render_log_md_for(store, session.backend_id, root_id, None)


def _render_log_md_for(
    store: Any,
    backend_id: str,
    cycle_id: str,
    hard_samples_artifact: dict | None,
) -> None:
    index = store.load(backend_id, cycle_id)
    if not index:
        return
    n_trials = int(index.get("n_trials", 0) or 0)
    trials = store.load_trials_range(backend_id, cycle_id, 0, n_trials - 1) if n_trials else []
    cycle_dir = store.campaign_dir(cycle_id)
    streams_dir = cycle_dir / ".runtime" / "streams"
    fork_indices = _load_fork_indices(cycle_dir)
    content = to_markdown(
        from_disk_log(
            index,
            trials,
            hard_samples_artifact=hard_samples_artifact,
            streams_dir=streams_dir,
            fork_indices=fork_indices,
        )
    )
    (cycle_dir / "log.md").write_text(content, encoding="utf-8")


def _load_fork_indices(cycle_dir: Path) -> list[dict] | None:
    """Read every sibling cycle's ``index.json`` under the family root.

    Walks all three sibling kinds: ``forks/``, ``diag/``, and
    ``sweeps/*/forks/``. Returns ``None`` when no sibling dirs exist (i.e.
    the cycle is itself a fork, or no siblings have been minted yet) so
    ``from_disk_log`` knows to skip the Forks section.
    """
    sibling_dirs: list[Path] = []
    for sibling_kind in ("forks", "diag"):
        parent = cycle_dir / sibling_kind
        if parent.is_dir():
            sibling_dirs.extend(sorted(parent.iterdir()))
    sweeps_dir = cycle_dir / "sweeps"
    if sweeps_dir.is_dir():
        for batch_dir in sorted(sweeps_dir.iterdir()):
            batch_forks = batch_dir / "forks"
            if batch_forks.is_dir():
                sibling_dirs.extend(sorted(batch_forks.iterdir()))
    if not sibling_dirs:
        return None
    out: list[dict] = []
    for fork_dir in sibling_dirs:
        if not fork_dir.is_dir():
            continue
        idx = fork_dir / "index.json"
        if not idx.is_file():
            continue
        try:
            out.append(json.loads(idx.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def _refresh_tenant_leaderboards(session: Session) -> None:
    """Refresh the four operator-facing dashboards at ``library/`` top.

    Tenant-scoped, idempotent overwrites: ``runs.md`` (every cycle, grouped
    by L1-generate template, sweep view conditional), ``individuals.md``
    (every measured JSP ranked), ``hard_samples.md`` (cross-cycle Rasch
    fit + heatmap), and ``README.md`` (10-line orientation). Each .md is
    a complete view — open one and read it without context.
    """
    from datetime import UTC, datetime

    from promptpotter.application.intelligence.hard_sample_archive import (
        build_archive_hard_samples_artifact,
    )
    from promptpotter.application.leaderboard import (
        build_individuals_rows,
        build_leaderboard_rows,
        format_individuals_md,
        format_runs_md,
    )
    from promptpotter.presentation.views.render_markdown import render_hard_sample_heatmap

    if session.store is None:
        return
    out_dir = session.store.base_dir / "library"
    out_dir.mkdir(parents=True, exist_ok=True)

    backend_id = session.backend_id
    generated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    with graceful("library/ refresh failed"):
        rows = build_leaderboard_rows(session.store)
        dataset_label = _pick_dataset_label(rows)
        runs_body = f"# Runs\n\n_Generated {generated_at}._\n\n" + format_runs_md(rows)
        (out_dir / "runs.md").write_text(runs_body, encoding="utf-8")

        ind_rows = build_individuals_rows(session.store, backend_id)
        ind_body = f"# Individuals\n\n_Generated {generated_at}._\n\n" + format_individuals_md(
            ind_rows
        )
        (out_dir / "individuals.md").write_text(ind_body, encoding="utf-8")

        artifact = build_archive_hard_samples_artifact(session.store.archive, backend_id)
        sample_lookup = _build_sample_query_lookup(session.store.archive, backend_id)
        heatmap = render_hard_sample_heatmap(artifact, sample_query_lookup=sample_lookup)
        title = f"# Hard Samples — {dataset_label}" if dataset_label else "# Hard Samples"
        if heatmap:
            hs_body = f"{title}\n\n_Generated {generated_at}._\n\n```\n{heatmap}\n```\n"
        else:
            hs_body = (
                f"{title}\n\n_Generated {generated_at}._\n\n"
                "_No measurements yet — run a cycle first._\n"
            )
        (out_dir / "hard_samples.md").write_text(hs_body, encoding="utf-8")

        (out_dir / "README.md").write_text(_render_library_readme(), encoding="utf-8")


def _pick_dataset_label(rows: list[Any]) -> str:
    """Pick a single dataset label for headers when one dominates."""
    seen: dict[str, int] = {}
    for r in rows:
        d = getattr(r, "dataset", "") or ""
        if d and d != "?":
            seen[d] = seen.get(d, 0) + 1
    if not seen:
        return ""
    if len(seen) == 1:
        return next(iter(seen))
    return ", ".join(sorted(seen))


def _build_sample_query_lookup(archive: Any, backend_id: str) -> dict[int, str]:
    """First-seen ``sample_id → query`` map across the entire archive.

    Walks index entries and the first measurement file that yields the
    sample_id. Cheap because callers only need the *text* of the top-K
    hardest samples; we still scan everything for correctness.
    """
    out: dict[int, str] = {}
    for entry in archive.list_all(backend_id):
        run_id = entry.get("run_id")
        if not run_id:
            continue
        detail = archive.load_by_id(backend_id, run_id)
        if detail is None:
            continue
        for item in detail.get("measurements", []):
            sid = item.get("sample_id")
            if sid is None:
                continue
            sid_int = int(sid)
            if sid_int in out:
                continue
            q = (item.get("query") or "").strip()
            if q:
                out[sid_int] = q
    return out


def _render_library_readme() -> str:
    return (
        "# library — cross-cycle dashboards for this tenant\n"
        "\n"
        "Open the .md files at the top of this folder to see what's been measured.\n"
        "\n"
        "| File              | Question it answers                                            |\n"
        "|-------------------|----------------------------------------------------------------|\n"
        "| `runs.md`         | What cycles has this tenant produced and how did they go?      |\n"
        "| `individuals.md`  | Which target configs have been measured, ranked by mean score? |\n"
        "| `hard_samples.md` | Which dataset samples are hardest? Heatmap + Rasch leaderboard.|\n"
        "\n"
        "`.archive/` holds machine state the optimizer needs at runtime — measurement\n"
        "facts, prompt-alias dedup, backend registrations. Don't read these by hand;\n"
        "each .md above derives its view from this archive on every round-end.\n"
    )


def _write_review_md(session: Session, cycle: Cycle) -> None:
    """Render ``campaigns/{cycle_id}/review.md`` from index + trials + round audits.

    M10's prompt-iteration feedback surface — peer of ``log.md``. Reads
    each round's ``.runtime/cache/rounds/round_NNNN.json`` so behaviour checks can
    evaluate the live L1 variants. Pulls the three context_object items
    off the cycle's ``task_context`` so the seeded
    ``context_object_honored`` check has something to match against.
    """
    if not session.state.cycle_id or session.store is None:
        return
    with graceful("review.md render failed"):
        store = session.store.campaigns
        index = store.load(session.backend_id, session.state.cycle_id)
        if not index:
            return
        n_trials = int(index.get("n_trials", 0) or 0)
        trials = (
            store.load_trials_range(session.backend_id, session.state.cycle_id, 0, n_trials - 1)
            if n_trials
            else []
        )
        cycle_dir = store.campaign_dir(session.state.cycle_id)
        rounds_dir = cycle_dir / ".runtime" / "cache" / "rounds"
        round_audits: list[dict | None] = []
        for trial in trials:
            round_num = int(trial.get("round") or 0)
            audit_path = rounds_dir / f"round_{round_num:04d}.json"
            audit: dict | None = None
            if audit_path.is_file():
                try:
                    audit = json.loads(audit_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    audit = None
            round_audits.append(audit)
        td = cycle.opt_sp.task_context
        context_object = [
            td.pipeline_purpose,
            td.optimization_goals,
            td.key_challenges,
        ]
        content = render_review_md(
            index,
            trials,
            round_audits=round_audits,
            context_object=context_object,
        )
        (cycle_dir / "review.md").write_text(content, encoding="utf-8")
