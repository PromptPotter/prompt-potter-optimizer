"""A launch's run: a held launch run here or in its own process (`run_job`), its line of cycles
(`run_optimization`) and each cycle's close."""

from __future__ import annotations

import asyncio
import contextlib
import sys
from collections.abc import Awaitable, Callable, Iterator
from contextlib import AsyncExitStack, contextmanager
from dataclasses import dataclass, replace

from promptpotter.application.bench.cycle import Cycle
from promptpotter.application.bench.line import (
    bank_campaign_result,
    bench_selection,
    declare_line_bench,
    line_round_budget,
    line_spans,
    own_level,
    read_curve,
)
from promptpotter.application.bench.resume_and_fork.fork_siblings import ForkCut, cut_run_fork
from promptpotter.application.initialization.scoring_scope import session_scope
from promptpotter.application.initialization.session import Session
from promptpotter.application.jobs.capacity import resolve_run_capacity
from promptpotter.application.jobs.launcher.admission import refuse_campaign
from promptpotter.application.jobs.launcher.mint_and_start import ExistingCycle
from promptpotter.application.jobs.launcher.opening import Opening
from promptpotter.application.jobs.launcher.run_job import HeldRun, JobSpec, stores_of
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.jobs.shares import declare_round_allowance
from promptpotter.application.jobs.spawning import tell_spawner
from promptpotter.application.optimizer_manifest import SelectedOptimizer
from promptpotter.application.run_observers import RunObservers, declare_run_stop, end_run_on
from promptpotter.application.run_stop import RUN_ENDS, RUN_STOPS
from promptpotter.application.runner.loop import open_origin_round, run_round_loop
from promptpotter.application.runner.round import flush_pending_decisions
from promptpotter.application.runner.run_init import (
    PreparedRun,
    close_init,
    enter_cycle,
    onto_fork,
    open_cycle,
    open_launch,
    start_launch,
)
from promptpotter.application.served_dashboard import fork_remainder
from promptpotter.config.logging import setup_logging
from promptpotter.config.paths import READOUT_POINTER, user_data_root
from promptpotter.domain.bench import BenchCurve, BenchScore
from promptpotter.domain.campaign import run_clamp
from promptpotter.domain.campaign_config import CampaignConfig, CycleConfig, start_layer
from promptpotter.domain.cycle_result import CycleResult
from promptpotter.domain.fork import ConfigOverrides, RebaseRequest
from promptpotter.domain.job import LaunchMove
from promptpotter.domain.paired_reading import RoundArm
from promptpotter.domain.phases import (
    STOP_REASON_INFO,
    PauseCause,
    StopOutcome,
    StopReason,
    stop_reason_outcome,
)
from promptpotter.domain.records.error import ErrorRecord
from promptpotter.domain.records.lineage import CycleFinalRecord, CycleStart
from promptpotter.domain.run_mode import RunMode
from promptpotter.domain.run_standing import round_clocks
from promptpotter.domain.sample import Sample
from promptpotter.domain.spend import Budget, HeldBudget
from promptpotter.domain.spend_rollup import SpendRollup
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm.spend_book import SpendBook, require_spend_book
from promptpotter.infrastructure.run_scope import scoped
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    CYCLE_TIMELINES,
    wall_clock_of,
)
from promptpotter.infrastructure.store.io import write_text
from promptpotter.infrastructure.store.layout import CycleLayout
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
        wall_clock=wall_clock_of(
            session.store.campaigns.read(session.hop, CYCLE_TIMELINES),
            started_at=started_at,
            finished_at=finished_at,
            optimizer_phases=selected.phases,
        ),
        **round_clocks(cycle_rounds)._asdict(),
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
    if not is_paused:
        # A pause leaves the cycle ACTIVE: the PAUSED it declares must stay the last declaration read.
        session.store.campaigns.append(
            session.hop,
            CycleFinalRecord(interrupted_round=interrupted_round, final=cycle_result),
        )
    # Where every exit converges, rather than trusting each raise site: after what it banked, and
    # before the drain renders the artifacts that name the stop.
    declare_run_stop(
        session,
        observers,
        stop_reason,
        cause=pause_cause,
        detail=detail,
        error=cycle_result.error,
    )


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
            # Before the stop, which names the fork; off the loop: its reads wait on the line. A
            # cancel waits the cut out, so the stop names every child it minted.
            cut, cancel_exc = await completing(
                asyncio.to_thread(_cut_rebase_fork, prep, session, fork)
            )
            onto, detail = cut, cut[1].said(session.store)
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
        rebasing=onto is not None and cancel_exc is None,
        selected=prep.optimizer,
        detail=detail,
    )
    if book is not None:
        # Past the stop record, which names what is left of it: no later run's book holds it.
        book.release_aside()
    return _CycleOutcome(
        session=session,
        cycle_result=cycle_result,
        # A cancelled line ends on the stop naming its fork, which a resume runs.
        onto=onto if cancel_exc is None else None,
        observers=observers,
        cancelled=cancel_exc,
    )


async def completing[T](work: Awaitable[T]) -> tuple[T, asyncio.CancelledError | None]:
    """*work* run to its end whatever cancels its awaiter: its answer, and the cancel still owed
    to its asker."""
    task = asyncio.ensure_future(work)
    owed: asyncio.CancelledError | None = None
    while True:
        try:
            return await asyncio.shield(task), owed
        except asyncio.CancelledError as exc:
            if task.cancelled():
                raise
            owed = owed or exc


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
                    cause=(
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


async def run_watched(
    held: HeldRun, *, readout_sink: Callable[[str], None] | None
) -> tuple[Session, CycleResult]:
    """A run a person watches (`Inline`): the readout pointer names its cycle first."""
    session = held.session
    readout = CycleLayout(session.store.campaigns.cycle_dir(session.hop).absolute()).readout
    # The PATH, never a copy: parallel runs each own their cycle's readout, and one copy interleaves.
    with contextlib.suppress(OSError):
        write_text(user_data_root() / READOUT_POINTER, f"{readout}\n")
    return await run_held(held, readout_sink=readout_sink)


async def run_held(
    held: HeldRun, *, readout_sink: Callable[[str], None] | None
) -> tuple[Session, CycleResult]:
    """A launch held here runs inside its opening's exit (*held*'s stack)."""
    async with held.closing:
        return await _run_opened(
            held.opening,
            held.session,
            held.cycle,
            mode=held.mode,
            budget=held.budget,
            optimizer=held.optimizer,
            readout_sink=readout_sink,
        )


async def run_job(spec: JobSpec) -> StopOutcome | None:
    """A detached launch's run, in the process `run_job.py::spawn_job` started."""
    stores = stores_of(spec)
    job_registry = JobRegistry(spec.jobs_dir, capacity=resolve_run_capacity)
    # The job, its claim with it, now lives and dies with THIS process: a run killed before it
    # declares itself leaves the cycle free.
    if (adopted := await asyncio.to_thread(job_registry.adopt, spec.job_id)) is None:
        return None
    opening = Opening.of(stores, job_registry, adopted)
    tell_spawner()
    try:
        async with AsyncExitStack() as closing:
            closing.push_async_exit(opening)
            # Asked here alone, under the claim: the admitting process opened nothing of the cycle.
            # A refusal is the job's, never the cycle's stop: the cycle keeps the one it had.
            refuse_campaign(stores, (at := stores.campaigns.campaign_cycle(spec.hop)).campaign)
            opened = await closing.enter_async_context(ExistingCycle(at, Budget()).open(opening))
            opening.owns(spec.hop)
            _, result = await _run_opened(
                opening,
                opened.session,
                opened.cycle,
                mode=spec.mode,
                budget=spec.budget,
                optimizer=opened.optimizer,
            )
    except (KeyboardInterrupt, asyncio.CancelledError):
        return StopOutcome.PAUSED
    except Exception:  # noqa: BLE001 -- the opening's exit released the job with it
        return StopOutcome.FAILED
    return stop_reason_outcome(result.stop_reason)


async def _run_opened(
    opening: Opening,
    session: Session,
    cycle: CycleConfig,
    *,
    mode: RunMode,
    budget: HeldBudget,
    optimizer: SelectedOptimizer,
    readout_sink: Callable[[str], None] | None = None,
) -> tuple[Session, CycleResult]:
    """THE run of a held launch at every door, an inner seat's too: START, the run, its END, the
    END being *opening*'s exit, which its door's stack holds. Answers the session the run ENDED on
    (`run_optimization`). Its START and its hold wait on gates, off the loop."""
    # Unstamped until its observers stand: nothing of this launch reached the cycle before, whose
    # own producer may still be running it.
    opening.owns(None)
    await asyncio.to_thread(opening.job_registry.move, opening.job_id, LaunchMove.START)
    observers = await asyncio.to_thread(enter_cycle, session, readout_sink=readout_sink)
    # Anything raised from here fired OUTSIDE the runner's try, an interrupt aside: it stamps.
    opening.running_on(session.hop)
    return await run_optimization(
        list(session.bank.samples),
        cycle,
        session=session,
        observers=observers,
        mode=mode,
        budget=budget,
        optimizer=optimizer,
    )


if __name__ == "__main__":
    # The detached run's entry alone lands: the landing completes the inner door's registry, which
    # imports `run_held` from this module, so the module itself never imports the landing.
    from promptpotter.application.maintenance.landing import land_at_entry

    setup_logging()
    _spec = JobSpec.model_validate_json(sys.stdin.buffer.read())
    land_at_entry(every_treatment=False)
    if (_outcome := asyncio.run(run_job(_spec))) is not None and (_code := _outcome.exit_code):
        sys.exit(_code)
