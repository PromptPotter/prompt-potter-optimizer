from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace

from promptpotter.application.bench.cycle import Cycle
from promptpotter.application.bench.resume_and_fork.fork_siblings import cut_fork
from promptpotter.application.initialization.scoring_scope import session_scope
from promptpotter.application.initialization.session import Session
from promptpotter.application.optimizer_manifest import SelectedOptimizer, bound_optimizer
from promptpotter.application.optimizers.nodes import Controller
from promptpotter.application.run_observers import RunObservers, declare_run_stop
from promptpotter.application.runner.bench import (
    bank_campaign_result,
    bench_selection,
    declare_line_bench,
    line_bench_series,
    line_spans,
    own_level,
)
from promptpotter.application.runner.loop import open_origin_round, run_round_loop
from promptpotter.application.runner.mint import (
    PreparedRun,
    close_init,
    declare_round_allowance,
    onto_fork,
    open_cycle,
    open_launch,
    start_launch,
)
from promptpotter.application.runner.round import flush_pending_decisions
from promptpotter.application.runner.termination import (
    RUN_ENDS,
    RUN_STOPS,
    end_run_on,
    run_stop_reason,
)
from promptpotter.application.served_dashboard import fork_remainder
from promptpotter.domain.bench import BenchScore
from promptpotter.domain.campaign import run_clamp
from promptpotter.domain.campaign_config import CampaignConfig, CycleConfig, apply_config_overrides
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.phases import (
    SELF_PAUSE_CAUSE,
    STOP_REASON_INFO,
    ErrorRecord,
    PauseCause,
    StopOutcome,
    StopReason,
)
from promptpotter.domain.results import CycleResult
from promptpotter.domain.run_mode import RunMode
from promptpotter.domain.run_records import (
    ComparisonExit,
    ConfigOverrides,
    CycleFinal,
    CycleSeed,
    RebaseRequest,
)
from promptpotter.domain.run_standing import round_clocks
from promptpotter.domain.sample import Sample
from promptpotter.domain.spend import HeldBudget, SpendRollup
from promptpotter.infrastructure.llm.spend_book import SpendBook, require_spend_book
from promptpotter.infrastructure.llm.telemetry import at_round
from promptpotter.infrastructure.store.campaign_store.ledger_scan import scan_ledger_wall_clock
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import PayloadInvalidError
from promptpotter.shared.run_scope import scoped

logger = logging.getLogger(__name__)


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
        result_fitness=None if picked is None or picked.fold is None else picked.fold.fitness,
        fitness_unit=session.state.scoring.scorer.fitness_unit,
        result_round=picked.round if picked is not None else 0,
        origin=None
        if cycle is None
        else own_level(cycle.origin_round.results, session.state.scoring.scorer.fitness_unit),
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
                    cycle_result.rounds, fitness_ceiling=config.fitness_ceiling
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


@dataclass
class _CycleOutcome:
    """``session`` and ``observers`` are the ones the cycle ENDED on: run init may have forked it."""

    session: Session
    cycle_result: CycleResult
    # The rebase the line continues onto; ``None`` where the line ends on this cycle.
    fork: RebaseRequest | None
    observers: RunObservers
    # Re-raised at the launch's exit, past its clock: it must reach the canceller.
    cancelled: asyncio.CancelledError | None


async def _run_single_cycle(
    prep: PreparedRun,
    *,
    session: Session,
    observers: RunObservers,
    mode: RunMode,
    diagnosing: Controller | None,
    step_rounds: int | None,
) -> _CycleOutcome:
    dataset, campaign_config = prep.dataset, prep.campaign_config
    cb = observers.callbacks

    cycle: Cycle | None = None
    forked_from: CycleHop | None = None
    cancel_exc: asyncio.CancelledError | None = None
    book: SpendBook | None = None
    fork: RebaseRequest | None = None
    open_round: int | None = None
    pause_cause = PauseCause.INTERRUPT
    try:
        cycle, forked_from = await open_cycle(prep, session=session, cb=cb, mode=mode)
        if forked_from is not None:
            # On the parent's own ledger, as a rebase's close ends it, before its readout names the fork.
            declare_run_stop(session, observers, StopReason.REBASED, interrupted_by=pause_cause)
            observers.drain_all()
            observers = onto_fork(
                cycle.session, forked_from, observers, campaign_config=campaign_config
            )
            cb = observers.callbacks
        session = cycle.session
        close_init(cycle, cb)
        declare_round_allowance(session, step_rounds)
        book = require_spend_book("the bench's set-aside")
        ended = await open_origin_round(
            cycle,
            dataset,
            cb,
            spend=observers.dashboard.live.state.spend,
            diagnosing=diagnosing is not None,
        ) or await run_round_loop(cycle, campaign_config, session, cb, diagnosing=diagnosing)
        stop_reason, cycle_error, fork, open_round, cancel_exc, stepped = ended
        if stepped:
            pause_cause = PauseCause.STEP
    except RUN_ENDS as exc:
        stop_reason, cycle_error = end_run_on(exc, session, where="before the round loop")
        if isinstance(exc, asyncio.CancelledError):
            cancel_exc = exc

    if (
        cycle is not None
        and book is not None
        and cycle.bench_passes is not None
        and STOP_REASON_INFO[stop_reason].grades_selection
    ):
        book.set_aside(0.0, 0)
        try:
            # Under the last round, as the `bench` verb grades it (`scoring/passes.py::grade_line_bench`).
            with at_round(cycle.rounds[-1].round):
                cycle.bench_passes = await bench_selection(
                    session, cycle.rounds, points=cycle.points, banked=cycle.bench_passes, cb=cb
                )
        except (*RUN_STOPS, KeyboardInterrupt, asyncio.CancelledError) as exc:
            # Only a pause escapes the pass; the resume takes the pass again.
            stop_reason, cycle_error = run_stop_reason(exc), None
            if isinstance(exc, asyncio.CancelledError):
                cancel_exc = exc

    rebasing = fork is not None and cancel_exc is None and stop_reason == StopReason.REBASED
    cycle_result = close_cycle(
        cycle,
        session,
        observers,
        stop_reason=stop_reason,
        cycle_error=cycle_error,
        open_round=open_round,
        config=campaign_config,
        diag=diagnosing is not None,
        pause_cause=PauseCause.CANCELLED if cancel_exc is not None else pause_cause,
        rebasing=rebasing,
    )
    return _CycleOutcome(
        session=session,
        cycle_result=cycle_result,
        fork=fork if rebasing else None,
        observers=observers,
        cancelled=cancel_exc,
    )


def _mint_and_rebase_fork(
    prep: PreparedRun,
    *,
    session: Session,
    observers: RunObservers,
    rebase_req: RebaseRequest,
) -> tuple[PreparedRun, Session, RunObservers]:
    """``config_overrides`` re-snapshot BEFORE the mint, so seed and in-process config agree. The
    fork's cap is what its line has left; its working state is the parent's latest, since the cut
    rewinds the line, never what the optimizer learned on it. A re-election is the round it hangs
    from, so the line continues from the arm a layer named."""
    parent = session.hop
    store = session.store.campaigns
    cut = rebase_req.fork_from_round
    overrides = (
        fork_remainder(session.store, parent)
        .past_cut(cut)
        .under(rebase_req.config_overrides or ConfigOverrides())
    )
    prep = replace(prep, campaign_config=apply_config_overrides(prep.campaign_config, overrides))
    held = store.standing_rounds(parent, moment=None).rounds
    learned = held[max(held)].close.optimizer_state
    new_cycle_id = cut_fork(
        session.store,
        parent=parent,
        fork_from_round=cut,
        trigger=rebase_req.trigger,
        reason=rebase_req.reason,
        issued_by=rebase_req.issued_by,
        # No `origin_prompts`: a rebase replays its origin from the parent's round.
        seed=CycleSeed(config_overrides=overrides),
    ).take(session.store)
    child = CycleHop(campaign_id=parent.campaign_id, cycle_id=new_cycle_id)
    branch = (
        rebase_req.hangs_from or store.standing_rounds(child, moment=None).rounds[cut - 1].close
    )
    store.rebank_round(
        child, branch.restated(branch.optimizer_state.model_copy(update={"slices": learned.slices}))
    )
    session = session.bound(child, purpose=session.purpose)
    observers = onto_fork(
        session,
        parent,
        observers,
        campaign_config=prep.campaign_config,
    )
    logger.info(
        "Auto-rebase: %s → %s at round %d, %s rounds in its cap [trigger=%s, reason=%s]",
        parent.cycle_id,
        new_cycle_id,
        cut,
        overrides.budget.rounds,
        rebase_req.trigger.value,
        rebase_req.reason,
    )
    return prep, session, observers


async def _run_line(
    prep: PreparedRun,
    *,
    session: Session,
    observers: RunObservers,
    mode: RunMode,
    diagnosing: Controller | None,
    step_rounds: int | None,
) -> _CycleOutcome:
    while True:
        outcome = await _run_single_cycle(
            prep,
            session=session,
            observers=observers,
            mode=mode,
            diagnosing=diagnosing,
            step_rounds=step_rounds,
        )
        if outcome.fork is None:
            return outcome
        # The operator's `--from` rewound the line's first cycle; a fork stands on its rebase's cut.
        mode = mode.model_copy(update={"from_round": None})
        prep, session, observers = _mint_and_rebase_fork(
            prep,
            session=outcome.session,
            observers=outcome.observers,
            rebase_req=outcome.fork,
        )


async def run_optimization(
    dataset: list[Sample],
    cycle: CycleConfig,
    *,
    session: Session,
    observers: RunObservers,
    mode: RunMode,
    budget: HeldBudget,
    optimizer: SelectedOptimizer,
) -> tuple[Session, CycleResult]:
    """*observers* MUST be pre-built: their ledger is the run scope's, bound here before origin;
    *dataset* is the whole bank.

    Answers the session the run ENDED on: a fork run init or a rebase took is a cycle of its own.
    """
    if not session.cycle_id:
        raise RuntimeError("run_optimization runs a minted cycle: bind its session first")
    campaign_config = cycle.config
    start_launch(session)
    try:
        # Round 0 outside every round the loop enters: the origin pass, its gate and the close.
        # The run's one scope (`run_scope.py`); a cell's run nests its own inside the spawner's.
        with (
            at_round(0),
            scoped(
                ledger=observers.callbacks.ledger,
                optimizer=optimizer,
                determinism=run_clamp(campaign_config.optimization.determinism, session.purpose),
            ),
            session_scope(session),
        ):
            diagnosing = optimizer.plan.controller if mode.diag else None
            if mode.diag and diagnosing is None:
                raise PayloadInvalidError(
                    f"--diag shows what an optimizer's controller does at a round boundary, and "
                    f"{optimizer.name} declares no controller node"
                )
            if diagnosing is not None:
                # Its rounds are unmeasured: an arm leaves its comparison at every door.
                session.store.campaigns.leave_comparison(session.hop, ComparisonExit.DIAG)
            try:
                prep = await open_launch(
                    dataset, cycle, session=session, observers=observers, budget=budget
                )
            except RUN_STOPS as stop:
                stop_reason, cycle_error = end_run_on(stop, session, where="in run prep")
                outcome = _CycleOutcome(
                    session=session,
                    cycle_result=close_cycle(
                        None,
                        session,
                        observers,
                        stop_reason=stop_reason,
                        cycle_error=cycle_error,
                        open_round=None,
                        config=campaign_config,
                        diag=diagnosing is not None,
                        pause_cause=PauseCause.INTERRUPT,
                        rebasing=False,
                    ),
                    fork=None,
                    observers=observers,
                    cancelled=None,
                )
            except (KeyboardInterrupt, asyncio.CancelledError) as exc:
                # Prep sits outside `_run_single_cycle`'s try: undeclared, it leaves a dead run `running`.
                declare_run_stop(
                    session,
                    observers,
                    StopReason.PAUSED,
                    interrupted_by=(
                        PauseCause.CANCELLED
                        if isinstance(exc, asyncio.CancelledError)
                        else PauseCause.INTERRUPT
                    ),
                )
                observers.drain_all()
                raise
            else:
                outcome = await _run_line(
                    prep,
                    session=session,
                    observers=observers,
                    mode=mode,
                    diagnosing=diagnosing,
                    step_rounds=budget.step_rounds,
                )
            if outcome.cancelled is not None:
                # The caught instance, not a fresh class — it carries the reason its raise site named.
                raise outcome.cancelled
            return outcome.session, outcome.cycle_result
    finally:
        # Whatever path exits, a fork included: the observers left the opened cycle when the line moved.
        held = session.state.observers
        assert held is not None, "a run opens on observers that hold its cycle"
        held.release()
