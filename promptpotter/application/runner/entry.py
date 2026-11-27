from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace

from promptpotter.application.bench.cycle import Cycle
from promptpotter.application.bench.resume_and_fork.fork_siblings import ForkCut, cut_run_fork
from promptpotter.application.initialization.scoring_scope import session_scope
from promptpotter.application.initialization.session import Session
from promptpotter.application.jobs.shares import declare_round_allowance
from promptpotter.application.optimizer_manifest import SelectedOptimizer
from promptpotter.application.run_observers import RunObservers, declare_run_stop, end_run_on
from promptpotter.application.run_stop import RUN_ENDS, RUN_STOPS
from promptpotter.application.runner.bench import (
    bank_campaign_result,
    bench_selection,
    declare_line_bench,
    line_round_budget,
    line_spans,
    own_level,
    read_curve,
)
from promptpotter.application.runner.loop import open_origin_round, run_round_loop
from promptpotter.application.runner.round import flush_pending_decisions
from promptpotter.application.runner.run_init import (
    PreparedRun,
    close_init,
    onto_fork,
    open_cycle,
    open_launch,
    start_launch,
)
from promptpotter.application.served_dashboard import fork_remainder
from promptpotter.domain.bench import BenchCurve, BenchScore
from promptpotter.domain.campaign import run_clamp
from promptpotter.domain.campaign_config import CampaignConfig, CycleConfig, start_layer
from promptpotter.domain.cycle_result import CycleResult
from promptpotter.domain.paired_reading import RoundArm
from promptpotter.domain.phases import (
    SELF_PAUSE_CAUSE,
    STOP_REASON_INFO,
    PauseCause,
    StopOutcome,
    StopReason,
)
from promptpotter.domain.records.error import ErrorRecord
from promptpotter.domain.records.run_records import (
    ConfigOverrides,
    CycleFinalRecord,
    CycleStart,
    RebaseRequest,
)
from promptpotter.domain.run_mode import RunMode
from promptpotter.domain.run_standing import round_clocks
from promptpotter.domain.sample import Sample
from promptpotter.domain.spend import HeldBudget, SpendRollup
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm.spend_book import SpendBook, require_spend_book
from promptpotter.infrastructure.run_scope import scoped
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
    selected: SelectedOptimizer,
    fitness_ceiling: float | None,
) -> CycleResult:
    picked = cycle.selection if cycle is not None else None
    picked_sp = cycle.selected_sp if cycle is not None else None
    # Round 0 is the reference the result is differenced against, never a search result.
    cycle_rounds = [rr for rr in cycle.rounds if rr.round > 0] if cycle is not None else []
    spent = round_budget = 0
    curve: BenchCurve | None = None
    if cycle is not None:
        scorer = session.state.scoring.scorer
        spans = line_spans(session.store, scorer, session.hop)
        spent = sum(len(span.own) for span in spans)
        round_budget = line_round_budget(spans, cycle.config.optimization.budget.rounds)
        curve = read_curve(session.store, cycle.bench_passes, scorer, spans, budget=round_budget)
    started_at = session.state.launch_clock().started_at
    return CycleResult(
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
        round_budget=round_budget,
        result_prompt_fields=picked_sp.prompt_fields if picked_sp else {},
        result_pipeline_params=picked_sp.pipeline_params if picked_sp else None,
        stop_reason=stop_reason,
        started_at=started_at,
        finished_at=finished_at,
        wall_clock=scan_ledger_wall_clock(
            [session.store.campaigns.ledger(session.hop)],
            started_at=started_at,
            finished_at=finished_at,
            optimizer_phases=selected.phases,
        ),
        **round_clocks(cycle_rounds, fitness_ceiling=fitness_ceiling)._asdict(),
        prompt_hashes=selected.prompt_hashes(),
        scorer_id=session.state.scoring.scorer.id,
        cycle_id=session.cycle_id,
        resumed_from_round=session.state.resumed_from_round,
        spend=spend,
        error=cycle_error,
        bench=bench,
        curve=curve,
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
    pause_cause: PauseCause,
    rebasing: bool,
    selected: SelectedOptimizer,
    detail: str,
) -> CycleResult:
    """*rebasing*: the line continues onto a fork, whose close banks the launch's clock."""
    finished_at = utcnow_iso()
    # Drained on every exit, a raise while banking included: the artifacts name what landed.
    try:
        banked = None if cycle is None else cycle.bench_passes
        bench = declare_line_bench(
            session, config, passes=banked, selecting=False, ending=stop_reason
        )
        cycle_result = build_cycle_result(
            cycle,
            session,
            stop_reason=stop_reason,
            cycle_error=cycle_error,
            finished_at=finished_at,
            # In-memory, never the debounced ``dashboard.json``.
            spend=observers.dashboard.spend,
            bench=bench,
            selected=selected,
            fitness_ceiling=config.fitness_ceiling,
        )
        _finalize_run(
            session,
            observers,
            cycle_result,
            selected=selected,
            open_round=open_round,
            pause_cause=pause_cause,
            rebasing=rebasing,
            detail=detail,
        )
    finally:
        observers.drain_all()
    return cycle_result


def _finalize_run(
    session: Session,
    observers: RunObservers,
    cycle_result: CycleResult,
    *,
    selected: SelectedOptimizer,
    open_round: int | None,
    pause_cause: PauseCause,
    rebasing: bool,
    detail: str,
) -> None:
    stop_reason = cycle_result.stop_reason
    info = STOP_REASON_INFO[stop_reason]
    is_paused = info.outcome is StopOutcome.PAUSED
    # Both needed: the table says the reason leaves a partial round, the loop whether one was open.
    interrupted_round = open_round if info.halts_mid_round else None
    if not rebasing:
        # Before either declaration: a reader seeing the run stopped finds its clock banked.
        bank_campaign_result(
            session,
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
        session.store.campaigns.append(
            session.hop,
            CycleFinalRecord(interrupted_round=interrupted_round, final=cycle_result),
        )
        # After what it banked, and before the drain renders the artifacts that name the stop.
        declare_run_stop(session, observers, stop_reason, interrupted_by=pause_cause, detail=detail)


@contextmanager
def launch_scope(
    session: Session, observers: RunObservers, config: CampaignConfig
) -> Iterator[None]:
    """The launch's one scope (`run_scope.py`): round 0, outside every round the loop enters; a
    cell's run nests its own inside the spawner's."""
    start_launch(session)
    with (
        scoped(
            round=0,
            ledger=CycleEventLog.open(observers.cycle_dir),
            account=session.store,
            determinism=run_clamp(config.optimization.determinism, session.purpose),
        ),
        session_scope(session),
    ):
        yield


async def open_line(
    prep: PreparedRun, *, session: Session, observers: RunObservers, mode: RunMode
) -> tuple[Cycle, RunObservers]:
    """The line's cycle, INIT closed, and its observers: a fork run init took is a cycle of its own."""
    cycle, cut = await open_cycle(prep, session=session, mode=mode)
    if cut is not None:
        # On the parent's own ledger, as a rebase's close ends it, before its readout names the fork.
        declare_run_stop(
            session,
            observers,
            StopReason.REBASED,
            interrupted_by=PauseCause.INTERRUPT,
            detail=cut.said(session.store),
        )
        observers.drain_all()
        cycle.session, observers = await onto_fork(session, cut, observers, prep=prep)
    close_init(cycle)
    return cycle, observers


@dataclass
class _CycleOutcome:
    """``session`` and ``observers`` are the ones the cycle ENDED on: run init may have forked it."""

    session: Session
    cycle_result: CycleResult
    # The fork the line continues onto, cut before the stop naming it; ``None`` where it ends here.
    onto: tuple[PreparedRun, ForkCut] | None
    observers: RunObservers
    # Re-raised at the launch's exit, past its clock: it must reach the canceller.
    cancelled: asyncio.CancelledError | None


async def _run_single_cycle(
    prep: PreparedRun,
    *,
    session: Session,
    observers: RunObservers,
    mode: RunMode,
    step_rounds: int | None,
) -> _CycleOutcome:
    dataset, campaign_config = prep.dataset, prep.campaign_config

    cycle: Cycle | None = None
    cancel_exc: asyncio.CancelledError | None = None
    book: SpendBook | None = None
    fork: RebaseRequest | None = None
    open_round: int | None = None
    pause_cause = PauseCause.INTERRUPT
    detail = ""
    try:
        cycle, observers = await open_line(prep, session=session, observers=observers, mode=mode)
        session = cycle.session
        declare_round_allowance(
            session.store,
            session.hop,
            resumed_from_round=session.state.resumed_from_round,
            step_rounds=step_rounds,
        )
        book = require_spend_book("the bench's set-aside")
        ended = await open_origin_round(
            cycle, dataset, resumed_ending=prep.resumed_ending
        ) or await run_round_loop(cycle, cycle.config, session)
        stop_reason, cycle_error, fork, open_round, cancel_exc, stepped, detail = ended
        if stepped:
            pause_cause = PauseCause.STEP
    except RUN_ENDS as exc:
        stop_reason, cycle_error, detail = end_run_on(exc)
        if isinstance(exc, asyncio.CancelledError):
            cancel_exc = exc

    onto: tuple[PreparedRun, ForkCut] | None = None
    try:
        if cycle is not None:
            # Before the result is built: a decision made after the last round has no next
            # `close_round`, and a rebase's fork lifts it.
            flush_pending_decisions(cycle)
        if (
            cycle is not None
            and book is not None
            and cycle.bench_passes is not None
            and STOP_REASON_INFO[stop_reason].grades_selection
        ):
            book.release_aside()
            # Under the last round, as the `held-out` verb grades it (`scoring/passes.py::grade_line_bench`).
            with scoped(round=cycle.rounds[-1].round):
                cycle.bench_passes = await bench_selection(
                    session,
                    cycle.rounds,
                    points=cycle.points,
                    banked=cycle.bench_passes,
                    trigger=cycle.config.bench_trigger,
                )
        elif fork is not None and cancel_exc is None and stop_reason == StopReason.REBASED:
            # Before the stop, which names the fork; off the loop: its reads wait on the line.
            onto = await asyncio.to_thread(_cut_rebase_fork, prep, session, fork)
            detail = onto[1].said(session.store)
    except RUN_ENDS as exc:
        # A pause leaves the pass owed, which the resume takes again; anything else that ends it
        # is the stop the cycle closes on, never an escape past `close_cycle`.
        stop_reason, cycle_error, detail = end_run_on(exc)
        if isinstance(exc, asyncio.CancelledError):
            cancel_exc = exc
    cycle_result = close_cycle(
        cycle,
        session,
        observers,
        stop_reason=stop_reason,
        cycle_error=cycle_error,
        open_round=open_round,
        config=campaign_config,
        pause_cause=PauseCause.CANCELLED if cancel_exc is not None else pause_cause,
        rebasing=onto is not None,
        selected=prep.optimizer,
        detail=detail,
    )
    if book is not None:
        # Past the stop record, which names what is left of it: no later run's book holds it.
        book.release_aside()
    return _CycleOutcome(
        session=session,
        cycle_result=cycle_result,
        onto=onto,
        observers=observers,
        cancelled=cancel_exc,
    )


def _cut_rebase_fork(
    prep: PreparedRun, session: Session, rebase_req: RebaseRequest
) -> tuple[PreparedRun, ForkCut]:
    """``config_overrides`` re-snapshot BEFORE the mint, so start and in-process config agree. The
    fork's cap is what its line has left; its working state is the parent's latest, since the cut
    rewinds the line, never what the optimizer learned on it. A re-election is the round it hangs
    from, so the line continues from the arm a layer named. Untaken: `onto_fork` takes it."""
    parent = session.hop
    store = session.store.campaigns
    cut = rebase_req.fork_from_round
    overrides = (
        fork_remainder(session.store, parent)
        .past_cut(cut)
        .under(rebase_req.config_overrides or ConfigOverrides())
    )
    prep = replace(prep, campaign_config=prep.campaign_config.overlaid(start_layer(overrides)))
    held = store.standing_rounds(parent, moment=None).rounds
    learned = held[max(held)].close.optimizer_state
    hung = rebase_req.hangs_from
    picked = hung.election.selected if hung is not None and hung.election is not None else None
    fork = cut_run_fork(
        session,
        cut,
        trigger=rebase_req.trigger,
        reason=rebase_req.reason,
        issued_by=rebase_req.issued_by,
        named=None if picked is None else RoundArm(round=cut - 1, arm=picked),
        # No `origin_prompts`: a rebase replays its origin from the parent's round.
        start=CycleStart(config_overrides=overrides),
    )
    branch = hung or store.standing_rounds(fork.child, moment=None).rounds[cut - 1].close
    store.append(
        fork.child,
        branch.restated(branch.optimizer_state.model_copy(update={"slices": learned.slices})),
    )
    return prep, fork


async def _run_line(
    prep: PreparedRun,
    *,
    session: Session,
    observers: RunObservers,
    mode: RunMode,
    step_rounds: int | None,
) -> _CycleOutcome:
    while True:
        outcome = await _run_single_cycle(
            prep,
            session=session,
            observers=observers,
            mode=mode,
            step_rounds=step_rounds,
        )
        if outcome.onto is None:
            return outcome
        # The operator's `--from` rewound the line's first cycle; a fork stands on its rebase's cut.
        mode = mode.model_copy(update={"from_round": None})
        prep, cut = outcome.onto
        session, observers = await onto_fork(outcome.session, cut, outcome.observers, prep=prep)


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
    try:
        with launch_scope(session, observers, campaign_config):
            try:
                prep = await open_launch(
                    dataset,
                    cycle,
                    session=session,
                    observers=observers,
                    budget=budget,
                    optimizer=optimizer,
                )
            except RUN_STOPS as stop:
                stop_reason, cycle_error, detail = end_run_on(stop)
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
                        pause_cause=PauseCause.INTERRUPT,
                        rebasing=False,
                        selected=optimizer,
                        detail=detail,
                    ),
                    onto=None,
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
