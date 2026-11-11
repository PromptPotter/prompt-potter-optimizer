from __future__ import annotations

from promptpotter.application.bench.cycle import Cycle
from promptpotter.application.initialization.session import Session
from promptpotter.application.optimizer_manifest import SelectedOptimizer, bound_optimizer
from promptpotter.application.run_observers import RunObservers, declare_run_stop
from promptpotter.application.runner.bench import line_bench_series, line_spans, own_level
from promptpotter.application.runner.campaign_result import (
    bank_campaign_result,
    declare_line_bench,
)
from promptpotter.application.runner.round import flush_pending_decisions
from promptpotter.domain.bench import BenchScore
from promptpotter.domain.campaign_config import CampaignConfig
from promptpotter.domain.phases import (
    SELF_PAUSE_CAUSE,
    STOP_REASON_INFO,
    ErrorRecord,
    PauseCause,
    StopOutcome,
    StopReason,
)
from promptpotter.domain.results import CycleResult
from promptpotter.domain.run_records import CycleFinal
from promptpotter.domain.run_standing import round_clocks
from promptpotter.domain.spend import SpendRollup
from promptpotter.infrastructure.store.campaign_store.ledger_scan import scan_ledger_wall_clock
from promptpotter.shared.clock import utcnow_iso


def build_cycle_result(
    cycle: Cycle | None,
    session: Session,
    *,
    stop_reason: StopReason,
    stepped: bool,
    cycle_error: ErrorRecord | None,
    finished_at: str,
    spend: SpendRollup | None,
    bench: BenchScore,
) -> CycleResult:
    picked = cycle.selection if cycle is not None else None
    picked_sp = cycle.selected_sp if cycle is not None else None
    # Round 0 is the reference the result is differenced against, never a search result.
    cycle_rounds = [rr for rr in cycle.rounds if rr.round > 0] if cycle is not None else []
    bench_origin: float | None = None
    bench_levels: list[float | None] = []
    spent = 0
    round_budget: int | None = None
    if cycle is not None:
        scorer = session.state.scoring.scorer
        spans = line_spans(session.store, scorer, session.hop)
        spent = sum(len(span.own) for span in spans)
        bench_origin, bench_levels = line_bench_series(
            session.store, cycle.bench_passes, scorer, spans
        )
        cap = cycle.config.optimization.budget.rounds
        # The cycle's cap holds what its line had left: the line's adds every round a rebase left.
        round_budget = None if cap is None else cap + spent - len(cycle_rounds)
    return CycleResult(
        rounds=cycle_rounds,
        n_rounds_after_origin=spent,
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
        round_budget=round_budget,
        result_prompt_fields=picked_sp.prompt_fields if picked_sp else {},
        result_pipeline_params=picked_sp.pipeline_params if picked_sp else None,
        stop_reason=stop_reason,
        stepped=stepped,
        started_at=session.state.launch_clock().started_at,
        finished_at=finished_at,
        cycle_id=session.cycle_id,
        resumed_from_round=session.state.resumed_from_round,
        spend=spend,
        error=cycle_error,
        bench=bench,
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
    # Drained on every exit, a raise while banking included: the artifacts name what landed.
    try:
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
            stepped=pause_cause is PauseCause.STEP and stop_reason is StopReason.PAUSED,
            cycle_error=cycle_error,
            finished_at=finished_at,
            # In-memory, never the debounced ``dashboard.json``.
            spend=observers.dashboard.live.state.spend,
            bench=bench,
        )
        _finalize_run(
            session,
            observers,
            cycle_result,
            config=config,
            selected=selected,
            diag=diag,
            open_round=open_round,
            pause_cause=pause_cause,
            rebasing=rebasing,
        )
    finally:
        observers.drain_all()
    return cycle_result


def _finalize_run(
    session: Session,
    observers: RunObservers,
    cycle_result: CycleResult,
    *,
    config: CampaignConfig,
    selected: SelectedOptimizer,
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
            interrupted_by=SELF_PAUSE_CAUSE.get(stop_reason, pause_cause),
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
                scorer_id=session.state.scoring.scorer.id,
                mode="diag" if diag else "full",
                result_round=cycle_result.result_round,
                result_prompt_fields=cycle_result.result_prompt_fields,
                result_pipeline_params=cycle_result.result_pipeline_params,
            ),
        )
        # After what it banked, and before the drain renders the artifacts that name the stop.
        declare_run_stop(session, observers, stop_reason, interrupted_by=pause_cause)
