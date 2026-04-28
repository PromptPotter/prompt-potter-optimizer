"""Optimization loop orchestrator — L1 generate → score → escalate, counter-based."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from promptpotter.application.campaign.campaign_setup import (
    Session,
    init_optimization_loop,
)
from promptpotter.application.campaign.config import CampaignConfig
from promptpotter.application.campaign.data import CampaignBaseline
from promptpotter.application.campaign.phase_views import build_phase_view
from promptpotter.application.optimization.cycle import Cycle
from promptpotter.application.optimization.layer_escalation import (
    build_escalation_entry,
    escalate_l2,
)
from promptpotter.application.optimization.nodes.l1 import execute_round
from promptpotter.application.optimization.pipeline import get_round_recorder
from promptpotter.application.optimization.results import RoundResult, RunResult
from promptpotter.application.scoring.zero_signal_filter import apply_zero_signal_exclusions
from promptpotter.domain.analysis import EscalationTarget
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.phases import (
    CampaignPhase,
    StopLoop,
    StopReason,
    emit_phase,
)
from promptpotter.domain.sample import Sample
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.infrastructure.persistence.session_emitter import CampaignPersistenceEmitter
from promptpotter.shared.errors import graceful

logger = logging.getLogger(__name__)

__all__ = ["RunListener", "run_optimization"]


class RunListener:
    """Fan-out to emitter + display sinks; ``__getattr__`` dispatches every
    ``on_*`` callback to whichever sinks are present. ``on_phase`` is
    explicit so ``build_phase_view`` runs once on a shared ctx instead of
    twice in parallel."""

    def __init__(
        self,
        *,
        emitter: Any = None,
        display: Any = None,
    ) -> None:
        self.emitter = emitter
        self.display = display
        # Single phase-view ctx: build_phase_view is stateful — it both
        # reads and writes — so both sinks must observe the *same* dict to
        # read consistent baseline_accuracy / round_num / etc. in their
        # other callbacks. We overwrite each sink's pre-existing ctx so
        # later attribute reads (``self._phase_ctx[...]``) hit this shared
        # instance.
        self._phase_ctx: dict[str, Any] = {}
        if emitter is not None:
            emitter._phase_ctx = self._phase_ctx
        if display is not None:
            display._phase_ctx = self._phase_ctx

    @property
    def _sinks(self) -> tuple[Any, ...]:
        return tuple(s for s in (self.emitter, self.display) if s is not None)

    def __getattr__(self, name: str) -> Callable[..., None]:
        if not name.startswith("on_"):
            raise AttributeError(name)

        def fan_out(*args: Any, **kwargs: Any) -> None:
            for sink in self._sinks:
                getattr(sink, name)(*args, **kwargs)

        return fan_out

    def on_phase(self, event: Any) -> None:
        view = build_phase_view(event, self._phase_ctx)
        for sink in self._sinks:
            sink.on_phase(event, view)


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
        obs=session.obs,
        obs_campaign_id=session.obs_campaign_id,
        escalation_check_result=escalation_check_result,
    )
    if stop:
        raise StopLoop(stop)


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
    cycle.escalation.l1_stall_count = (
        0 if round_result.improved else cycle.escalation.l1_stall_count + 1
    )
    if round_result.improved:
        cycle.opt_sp.clear_volatile()

    cb.on_round_complete(round_result, cycle.escalation.l1_stall_count)

    if cycle.pending_decisions:
        round_result.decisions.extend(cycle.flush_decisions())

    if session.cycle_id:
        with graceful("Round checkpoint failed"):
            session.store.campaigns.add_trial(
                session.backend_id,
                session.cycle_id,
                cycle.checkpoint(round_result, round_num),
            )
        _write_log_md(session)

    if _rr := get_round_recorder():
        _rr.flush()

    if cycle.search_memory:
        cycle.search_memory.on_round_complete(cycle, session, round_num)
        # Prune always-hit/always-miss queries from the active set.
        zsf = config.optimization.zero_signal_filter_enabled
        dataset_name = session.dataset_name
        if zsf and dataset_name and session.store is not None:
            with graceful("Zero-signal filter failed"):
                excluded = apply_zero_signal_exclusions(
                    store=session.store,
                    dataset_name=dataset_name,
                    memory=cycle.search_memory,
                    active_dataset=dataset,
                    min_observations=config.optimization.zero_signal_filter_min_observations,
                    campaign_id=session.cycle_id or "",
                )
                if excluded:
                    excl_q = {e["query"] for e in excluded}
                    session.scoring_dataset[:] = [
                        s for s in session.scoring_dataset if s.query not in excl_q
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

    # Rasch + KG swap of the active scoring set.
    ss_cfg = config.optimization.scoring_set
    if ss_cfg.enabled and cycle.rounds:
        from promptpotter.application.intelligence.scoring_set import (
            build_scoring_set_event,
            evolve_scoring_set,
        )

        with graceful("ScoringSet evolve failed"):
            ss_result = evolve_scoring_set(
                full_dataset=dataset,
                current_scoring_set=session.scoring_dataset,
                rounds=cycle.rounds,
                config=ss_cfg,
                elimination_n_min=config.optimization.elimination_n_min,
            )
            if ss_result.swapped_in or ss_result.swapped_out:
                session.scoring_dataset[:] = ss_result.new_scoring_set
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

    if cycle.current_accuracy >= 1.0:
        raise StopLoop(StopReason.PERFECT)
    if cycle.escalation.l1_stall_count >= config.optimization.l1_patience:
        if not config.optimization.enable_l2:
            raise StopLoop(StopReason.PATIENCE)
        await _escalate_or_stop(cycle, config, session, round_num, cb)
        cycle.escalation.l1_stall_count = 0


async def _run_round_loop(
    cycle: Cycle,
    dataset: list[Sample],
    config: CampaignConfig,
    session: Session,
    cb: RunListener,
) -> StopReason:
    """Execute the round loop: generate → score → escalate → stop."""
    opt = config.optimization
    hard_cap = opt.hard_cap
    round_num = session.resumed_from_round
    clean_rounds = session.resumed_from_round
    max_rounds = opt.max_rounds or 999

    try:
        while clean_rounds < max_rounds and round_num < hard_cap:
            is_probe = cycle.probe_next_round
            if is_probe:
                warned = {q for q, e in cycle.opt_sp.warning_inventory.items() if e.get("warnings")}
                round_eval_data = [s for s in dataset if s.query in warned]
                round_checks = None
            else:
                round_eval_data, round_checks = session.scoring_dataset, session.degradation_checks

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

            if _rr := get_round_recorder():
                _rr.begin_round(round_num)

            round_result = await execute_round(
                cycle,
                round_num,
                round_eval_data,
                cb,
                degradation_checks=round_checks,
            )
            cycle.record_round(round_result, round_num)

            if cycle.search_memory and len(cycle.rounds) >= 2:
                cycle.search_memory.record_flips_from_rounds(cycle.rounds, round_num)

            if is_probe:
                cycle.set_probe(False)
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
                if signal.target in (EscalationTarget.L2, EscalationTarget.L3) and opt.enable_l2:
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
                elif signal.target == EscalationTarget.ABORT_CAMPAIGN:
                    raise StopLoop(StopReason.ABORT)
                if session.cycle_id:
                    session.store.campaigns.delete_round_candidates(
                        session.backend_id,
                        session.cycle_id,
                        round_num + 1,
                    )
                emit_phase(cb.on_phase, CampaignPhase.ESCALATION, "exit", round=round_num)
                round_num += 1
                continue

            await _post_round(cycle, round_result, round_num, config, session, dataset, cb)
            round_num += 1
            clean_rounds += 1

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
    session_id: str = "",
    display: Any = None,
    langfuse_session_id: str | None = None,
    cycle_id: str | None = None,
    resume_from_round_override: int | None = None,
    emitter: CampaignPersistenceEmitter | None = None,
    no_divergence_check: bool = False,
    fork_on_divergence: bool = False,
) -> RunResult:
    """Run optimization end-to-end. Runs baseline when ``baseline`` is None. Returns RunResult."""
    started_at = datetime.now(UTC).isoformat()

    if baseline is None:
        from promptpotter.application.campaign.data import (
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
            listener=listener,
        )
        baseline = extract_campaign_baseline(campaign_rounds)
        if display is not None and hasattr(display, "set_baseline"):
            display.set_baseline(baseline.baseline_acc)

    if not session_id:
        from promptpotter.application.campaign.campaign_setup import auto_mint_session
        from promptpotter.domain.cycle_identity import cycle_config_identity

        ps = baseline.baseline_ps
        baseline_prompt_fields = (
            ps.prompt_field_dict() if isinstance(ps, OptSearchPoint) else (ps or {})
        )
        baseline_osp = OptSearchPoint.from_prompt_fields(baseline_prompt_fields)
        base_pp = session.pipeline_schema.to_pipeline_params() if session.pipeline_schema else {}
        baseline_jsp = baseline_osp.to_job_search_point(
            base_pipeline_params=base_pp, schema=session.pipeline_schema
        )
        session_id, minted_cycle_id = auto_mint_session(
            session,
            campaign_config,
            cycle_hash=cycle_config_identity(baseline_jsp, dataset).removeprefix("cycle_"),
            baseline_acc=baseline.baseline_acc,
            baseline_prompt_fields=baseline_prompt_fields,
            dataset_size=len(dataset),
            experiment_id=experiment_id,
        )
        cycle_id = cycle_id or minted_cycle_id
    session.session_id = session_id
    if cycle_id:
        session.cycle_id = cycle_id

    from promptpotter.domain.scoring import split_scoring_block

    scoring_spec = split_scoring_block(campaign_config.scoring)

    if isinstance(task_context, TaskDecomposition):
        resolved_task_context = task_context
    elif isinstance(task_context, dict):
        resolved_task_context = TaskDecomposition.from_dict(task_context)
    else:
        resolved_task_context = TaskDecomposition()

    cb = listener if listener is not None else RunListener(display=display)

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
        cycle_id=cycle_id,
        resume_from_round_override=resume_from_round_override,
        experiment_id=experiment_id or "",
        session=session,
        started_at=started_at,
    )

    if emitter is None:
        from promptpotter.application.campaign.data import build_campaign_emitter

        emitter = build_campaign_emitter(
            session,
            campaign_config,
            baseline_accuracy=baseline.baseline_acc,
            resumed_from_round=session.resumed_from_round,
            recorder_provider=get_round_recorder,
        )
    cb.emitter = emitter

    stop_reason = await _run_round_loop(cycle, dataset, campaign_config, session, cb)

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
        cycle_id=session.cycle_id,
        session_id=session_id or None,
        resumed_from_round=session.resumed_from_round,
    )
    _finalize_run(cycle, session, emitter, run_result, campaign_config)
    return run_result


def _finalize_run(
    cycle: Cycle,
    session: Session,
    emitter: CampaignPersistenceEmitter | None,
    run_result: RunResult,
    campaign_config: CampaignConfig,
) -> None:
    """Mark cycle finished, fold the run summary into ``index.json::final``,
    render ``log.md`` (with the hard-samples heatmap inlined when the sorter
    ran), finalize the emitter. Mutates ``run_result.langfuse_trace_id``."""
    stop_reason = run_result.stop_reason
    if session.cycle_id:
        status_map = {
            str(StopReason.INTERRUPTED): "interrupted",
        }
        session.store.campaigns.mark_finished(
            session.backend_id,
            session.cycle_id,
            status=status_map.get(stop_reason, "completed"),
            stop_reason=stop_reason,
            best_accuracy=run_result.best_accuracy,
            best_round=run_result.best_round,
            n_rounds=run_result.n_rounds,
            finished_at=run_result.finished_at,
        )

    obs = session.obs
    if obs:
        obs.end_campaign(
            session.obs_campaign_id,
            best_accuracy=run_result.best_accuracy,
            n_rounds=run_result.n_rounds,
            stop_reason=stop_reason,
            best_round=run_result.best_round,
        )

    run_result.langfuse_trace_id = (
        None
        if stop_reason == StopReason.INTERRUPTED or obs is None
        else obs.get_langfuse_trace_id(session.obs_campaign_id)
    )

    if session.cycle_id:
        with graceful("Final summary write failed"):
            session.store.campaigns.update(
                session.backend_id,
                session.cycle_id,
                {"final": run_result.model_dump(exclude={"rounds"}, mode="json")},
            )

    if emitter:
        from promptpotter.application.intelligence.hard_sample_sorter import (
            build_hard_samples_artifact,
        )

        hs_cfg = campaign_config.optimization.hard_sample_sorter
        artifact: dict | None = None
        if hs_cfg.enabled:
            with graceful("Hard-sample artifact build failed"):
                artifact = build_hard_samples_artifact(
                    cycle.rounds,
                    cycle_id=session.cycle_id,
                    top_k_candidates=hs_cfg.top_k_candidates,
                    top_k_samples=hs_cfg.top_k_samples,
                )
        _write_log_md(session, hard_samples_artifact=artifact)
        emitter.finalize()


def _write_log_md(session: Session, *, hard_samples_artifact: dict | None = None) -> None:
    """Render ``campaigns/{cycle_id}/log.md`` from index + trials. Pure derived
    view; wrapped in ``graceful()`` so a render bug never breaks the run."""
    if not session.cycle_id or session.store is None:
        return
    with graceful("log.md render failed"):
        from promptpotter.presentation.views.reports import render_log_md

        store = session.store.campaigns
        index = store.load(session.backend_id, session.cycle_id)
        if not index:
            return
        n_trials = int(index.get("n_trials", 0) or 0)
        trials = (
            store.load_trials_range(session.backend_id, session.cycle_id, 0, n_trials - 1)
            if n_trials
            else []
        )
        content = render_log_md(index, trials, hard_samples_artifact=hard_samples_artifact)
        (store.campaign_dir(session.cycle_id) / "log.md").write_text(content, encoding="utf-8")
