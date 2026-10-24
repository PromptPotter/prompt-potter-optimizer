from __future__ import annotations

from promptpotter.application.bench.cycle import Cycle
from promptpotter.application.campaign_config import CampaignConfig
from promptpotter.application.initialization.session import Session
from promptpotter.application.optimizer_manifest import SelectedOptimizer, bound_optimizer
from promptpotter.application.run_observers import RunObservers, declare_run_stop
from promptpotter.application.runner.bench import bench_series, own_level
from promptpotter.application.runner.campaign_result import (
    bank_campaign_result,
    declare_line_bench,
)
from promptpotter.application.runner.output import (
    publish_export,
    write_log_md,
    write_review_md,
)
from promptpotter.application.runner.round import flush_pending_decisions
from promptpotter.application.scoring.evaluators import resolve_cell_formula
from promptpotter.config.settings import APP_VERSION
from promptpotter.domain.bench import BenchScore
from promptpotter.domain.export import PromptExport, build_prompt_export
from promptpotter.domain.phases import (
    STOP_REASON_INFO,
    ErrorRecord,
    PauseCause,
    StopOutcome,
    StopReason,
)
from promptpotter.domain.results import CycleResult, round_clocks
from promptpotter.domain.run_records import CycleFinal
from promptpotter.domain.spend import SpendRollup
from promptpotter.infrastructure.store.campaign_store.ledger_scan import scan_ledger_wall_clock
from promptpotter.shared.clock import utcnow_iso


def build_cycle_result(
    cycle: Cycle | None,
    session: Session,
    *,
    stop_reason: StopReason,
    cycle_error: ErrorRecord | None,
    finished_at: str,
    spend: SpendRollup | None,
    bench: BenchScore,
    langfuse_trace_id: str | None,
) -> CycleResult:
    picked = cycle.selection if cycle is not None else None
    picked_sp = cycle.selected_sp if cycle is not None else None
    # Round 0 is the reference the result is differenced against, never a search result.
    cycle_rounds = [rr for rr in cycle.rounds if rr.round > 0] if cycle is not None else []
    bench_origin: float | None = None
    bench_levels: list[float | None] = []
    if cycle is not None:
        bench_origin, bench_levels = bench_series(
            session.store,
            cycle.bench_passes,
            session.state.scoring.require_scorer(),
            rounds=cycle_rounds,
        )
    return CycleResult(
        rounds=cycle_rounds,
        n_rounds_after_origin=len(cycle_rounds),
        result_accuracy=None if picked is None or picked.fold is None else picked.fold.accuracy,
        result_round=picked.round if picked is not None else 0,
        origin=own_level(cycle.origin_round.results) if cycle is not None else None,
        level_series=(
            session.store.campaigns.standing_rounds(session.hop, moment=None).level_series
            if cycle is not None
            else None
        ),
        bench_origin_level=bench_origin,
        bench_round_levels=bench_levels,
        round_budget=cycle.config.optimization.budget.rounds if cycle is not None else None,
        result_prompt_fields=picked_sp.prompt_fields if picked_sp else {},
        result_pipeline_params=picked_sp.pipeline_params if picked_sp else None,
        stop_reason=stop_reason,
        started_at=session.state.launch_clock().started_at,
        finished_at=finished_at,
        cycle_id=session.cycle_id,
        resumed_from_round=session.state.resumed_from_round,
        spend=spend,
        error=cycle_error,
        bench=bench,
        langfuse_trace_id=langfuse_trace_id,
    )


def _export_artifact(
    session: Session,
    cycle_result: CycleResult,
    cycle: Cycle | None,
    *,
    formula: str | None,
) -> PromptExport | None:
    # A cycle no round of which closed has nothing on record to export.
    if cycle is None or (series := cycle_result.level_series) is None:
        return None
    winner = cycle.selection
    campaign = session.store.campaigns.load_campaign(session.campaign_id)
    return build_prompt_export(
        winner,
        cycle.rounds,
        tool_version=APP_VERSION,
        campaign_id=session.campaign_id,
        cycle_id=session.cycle_id,
        dataset_name=session.measured_dataset,
        dataset_hash=session.bank.dataset_hash,
        treatment=campaign.treatment if campaign else None,
        stop_reason=cycle_result.stop_reason,
        finished_at=cycle_result.finished_at,
        formula=formula,
        own=own_level(winner.results),
        level=series.reading(winner.round),
        schema=session.pipeline_schema,
        framing=cycle.framing,
        demo=session.state.scoring.require_partition().demo,
        bench=cycle_result.bench,
    )


def close_cycle(
    cycle: Cycle | None,
    session: Session,
    observers: RunObservers,
    *,
    stop_reason: StopReason,
    cycle_error: ErrorRecord | None,
    open_round: int | None,
    config: CampaignConfig,
    diag: bool,
    pause_cause: PauseCause,
    rebasing: bool,
) -> CycleResult:
    """*rebasing*: the line continues onto a fork, whose close banks the launch's clock."""
    finished_at = utcnow_iso()
    selected = bound_optimizer()
    # Before the result is built: a decision made after the last round has no next `close_round`.
    if cycle is not None:
        flush_pending_decisions(cycle, observers.callbacks.ledger)
    banked = None if cycle is None else cycle.bench_passes
    bench = declare_line_bench(
        session, config, observers.callbacks, passes=banked, selecting=False, ending=stop_reason
    )
    cycle_result = build_cycle_result(
        cycle,
        session,
        stop_reason=stop_reason,
        cycle_error=cycle_error,
        finished_at=finished_at,
        # In-memory, never the debounced ``dashboard.json``.
        spend=observers.dashboard.state.spend,
        bench=bench,
        langfuse_trace_id=observers.tracing.langfuse_trace_id(),
    )
    _finalize_run(
        session,
        observers,
        cycle_result,
        config=config,
        selected=selected,
        cycle=cycle,
        diag=diag,
        open_round=open_round,
        pause_cause=pause_cause,
        rebasing=rebasing,
    )
    return cycle_result


def _finalize_run(
    session: Session,
    observers: RunObservers,
    cycle_result: CycleResult,
    *,
    config: CampaignConfig,
    selected: SelectedOptimizer,
    cycle: Cycle | None,
    diag: bool,
    open_round: int | None,
    pause_cause: PauseCause,
    rebasing: bool,
) -> None:
    stop_reason = cycle_result.stop_reason
    info = STOP_REASON_INFO[stop_reason]
    is_paused = info.outcome is StopOutcome.PAUSED
    # Both needed: the table says the reason leaves a partial round, the loop whether one was open.
    interrupted_round = open_round if info.halts_mid_round else None
    if not rebasing:
        # Before either declaration: a reader seeing the run stopped finds its clock banked.
        bank_campaign_result(
            session.store,
            session.hop,
            ledger=observers.callbacks.ledger,
            clock=session.state.launch_clock(),
            finished_at=cycle_result.finished_at,
            optimizer_phases=selected.phases,
        )
    if is_paused:
        # Declared where every paused exit converges, rather than trusting each raise site.
        declare_run_stop(
            session,
            observers,
            stop_reason,
            interrupted_by=(
                PauseCause.BOUND if stop_reason is StopReason.PANEL_CUT else pause_cause
            ),
        )
    else:
        # A pause leaves the cycle ACTIVE: the PAUSED just declared must stay the last declaration read.
        session.store.campaigns.bank_final(
            session.hop,
            interrupted_round=interrupted_round,
            final=CycleFinal(
                started_at=cycle_result.started_at,
                wall_clock=scan_ledger_wall_clock(
                    [session.store.campaigns.ledger(session.hop)],
                    started_at=cycle_result.started_at,
                    finished_at=cycle_result.finished_at,
                    optimizer_phases=selected.phases,
                ),
                **round_clocks(
                    cycle_result.rounds, accuracy_ceiling=config.accuracy_ceiling
                )._asdict(),
                prompt_hashes=selected.prompt_hashes(),
                origin_composite_fitness=(
                    None
                    if (origin := cycle_result.origin) is None or origin.composite is None
                    else origin.composite.value
                ),
                scorer_id=session.state.scoring.require_scorer().id,
                mode="diag" if diag else "full",
                result_round=cycle_result.result_round,
                result_prompt_fields=cycle_result.result_prompt_fields,
                result_pipeline_params=cycle_result.result_pipeline_params,
            ),
            export=_export_artifact(
                session,
                cycle_result,
                cycle,
                formula=resolve_cell_formula(
                    session.state.scoring.require_scorer().per_cell, session.pipeline_schema
                )[0],
            ),
        )
        # After what it banked, before the digests that name the stop, and before the drain.
        declare_run_stop(session, observers, stop_reason, interrupted_by=pause_cause)
        # A diagnostic cycle answers a question about the optimizer; it has no winner to hand on.
        if not diag:
            publish_export(session)
        write_log_md(session, config)
        # Re-rendered once `final` is banked: the bench score is taken after the last round closes.
        if cycle is not None:
            write_review_md(
                session,
                accuracy_ceiling=config.accuracy_ceiling,
                optimizer=cycle.optimizer,
                framing=cycle.framing,
            )
    observers.drain_all(interrupted=interrupted_round is not None)
