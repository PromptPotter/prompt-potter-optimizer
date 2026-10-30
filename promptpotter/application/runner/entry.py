from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace

from promptpotter.application.bench.cycle import Cycle
from promptpotter.application.bench.resume_and_fork.fork_siblings import mint_fork
from promptpotter.application.initialization.loop_start import (
    cycle_partition,
    finalize_loop_state,
    init_optimization_loop,
    scope_memory,
)
from promptpotter.application.initialization.session import LaunchClock, Session
from promptpotter.application.optimizer_manifest import SelectedOptimizer, binding_optimizer
from promptpotter.application.run_observers import (
    RunObservers,
    build_run_observers,
    declare_run_stop,
)
from promptpotter.application.run_phase_control import RunControl
from promptpotter.application.runner.bench import bench_selection
from promptpotter.application.runner.campaign_result import (
    bench_origin,
    declare_line_bench,
)
from promptpotter.application.runner.cycle_close import close_cycle
from promptpotter.application.runner.inner.spawn_context import publish_inner_spawn_context
from promptpotter.application.runner.loop import run_round_loop
from promptpotter.application.runner.run_prep import (
    PreparedRun,
    arm_run_controls,
    declare_round_allowance,
    prepare_run,
    rebase_held,
)
from promptpotter.application.runner.termination import (
    RUN_ENDS,
    RUN_STOPS,
    end_run_on,
    run_stop_reason,
)
from promptpotter.application.served_dashboard import fork_remainder
from promptpotter.domain.campaign_config import CampaignConfig, apply_config_overrides
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.phases import STOP_REASON_INFO, PauseCause, StopReason
from promptpotter.domain.results import CycleResult
from promptpotter.domain.run_mode import RunMode
from promptpotter.domain.run_records import (
    ConfigOverrides,
    CycleSeed,
    RebaseRequest,
)
from promptpotter.domain.sample import Sample
from promptpotter.domain.spend import HeldBudget
from promptpotter.infrastructure.llm.pricing import refresh_rates_in_background
from promptpotter.infrastructure.llm.spend_book import SpendBook
from promptpotter.infrastructure.llm.telemetry import at_round, bind_priced
from promptpotter.infrastructure.producer_lock import release_cycle
from promptpotter.infrastructure.store.campaign_store.ledger_scan import scan_ledger_priced_keys
from promptpotter.shared.errors import PayloadInvalidError

logger = logging.getLogger(__name__)


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
    dataset: list[Sample],
    session: Session,
    observers: RunObservers,
    mode: RunMode,
    step_rounds: int | None,
) -> _CycleOutcome:
    origin = prep.origin
    campaign_config = prep.campaign_config
    cb = observers.callbacks

    cycle: Cycle | None = None
    forked_from: CycleHop | None = None
    cancel_exc: asyncio.CancelledError | None = None
    book: SpendBook | None = None
    fork: RebaseRequest | None = None
    open_round: int | None = None
    pause_cause = PauseCause.INTERRUPT
    try:
        cycle, forked_from = await init_optimization_loop(
            origin,
            dataset,
            campaign_config,
            cb=cb,
            no_divergence_check=mode.no_divergence_check,
            fork_on_divergence=mode.fork_on_divergence,
            resume_from_round_override=mode.from_round,
            session=session,
        )
        if forked_from is not None:
            # On the parent's own ledger, as a rebase's close ends it, before its readout names the fork.
            declare_run_stop(session, observers, StopReason.REBASED, interrupted_by=pause_cause)
            observers.drain_all()
            observers = _onto_fork(
                cycle.session, forked_from, observers, campaign_config=campaign_config
            )
            cb = observers.callbacks
        session = cycle.session
        finalize_loop_state(cycle, cb)

        book = session.state.control.book
        if (
            campaign_config.bench_trigger != "manual"
            and session.state.scoring.require_partition().bench
        ):
            # Its price is set aside so the selection's pass still fits under the search's ceiling.
            cycle.bench_passes = await bench_origin(
                session,
                cycle.points.job(origin.origin.individual),
                origin_id=origin.origin.individual.id,
                spend=observers.dashboard.state.spend,
                cb=cb,
            )
            if cycle.bench_passes is not None and book.binds("bench"):
                book.set_aside(cycle.bench_passes.reserve_usd, cycle.bench_passes.reserve_tokens)
            if (
                cycle.bench_passes is not None
                and campaign_config.bench_trigger == "each_round"
                and len(cycle.rounds) > 1
            ):
                # The selection a resumed line stands on, graded where its line holds no pass:
                # a re-enacting fork's floor, read today as the round it re-runs will be.
                with at_round(cycle.rounds[-1].round):
                    cycle.bench_passes = await bench_selection(
                        session,
                        cycle.rounds,
                        points=cycle.points,
                        banked=cycle.bench_passes,
                        cb=cb,
                    )
        declare_line_bench(
            session, campaign_config, cb, passes=cycle.bench_passes, selecting=True, ending=None
        )
        declare_round_allowance(session, step_rounds)
        stop_reason, cycle_error, fork, open_round, cancel_exc, stepped = await run_round_loop(
            cycle,
            dataset,
            campaign_config,
            session,
            cb,
            # `run_optimization` refused a --diag launch on an optimizer with no controller.
            diagnosing=cycle.optimizer.plan.controller if mode.diag else None,
        )
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
            # Under the last round, as the `bench` verb grades it (`grade_bench.py`).
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
        diag=mode.diag,
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


def _onto_fork(
    session: Session,
    parent: CycleHop,
    observers: RunObservers,
    *,
    campaign_config: CampaignConfig,
) -> RunObservers:
    rebase_held(session, parent)
    observers = build_run_observers(
        session=session,
        campaign_config=campaign_config,
        forked_from=observers,
    )
    arm_run_controls(session, observers)
    return observers


def _mint_and_rebase_fork(
    prep: PreparedRun,
    *,
    session: Session,
    observers: RunObservers,
    rebase_req: RebaseRequest,
) -> tuple[PreparedRun, Session, RunObservers]:
    """``config_overrides`` re-snapshot BEFORE the mint, so seed and in-process config agree. The
    fork's cap is what its line has left; its working state is the parent's latest, since the cut
    rewinds the line, never what the optimizer learned on it."""
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
    new_cycle_id = mint_fork(
        campaign_store=store,
        parent=parent,
        fork_from_round=cut,
        trigger=rebase_req.trigger,
        reason=rebase_req.reason,
        issued_by=rebase_req.issued_by,
        # No `origin_prompts`: a rebase replays its origin from the parent's round.
        seed=CycleSeed(config_overrides=overrides),
    )
    child = CycleHop(campaign_id=parent.campaign_id, cycle_id=new_cycle_id)
    branch = store.standing_rounds(child, moment=None).rounds[cut - 1].close
    store.restate_round(
        child, cut - 1, branch.optimizer_state.model_copy(update={"slices": learned.slices})
    )
    session = session.bound(child, purpose=session.purpose)
    observers = _onto_fork(
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
    dataset: list[Sample],
    session: Session,
    observers: RunObservers,
    mode: RunMode,
    step_rounds: int | None,
) -> _CycleOutcome:
    while True:
        outcome = await _run_single_cycle(
            prep,
            dataset=dataset,
            session=session,
            observers=observers,
            mode=mode,
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
    campaign_config: CampaignConfig,
    *,
    session: Session,
    observers: RunObservers,
    langfuse_session_id: str | None = None,
    mode: RunMode,
    budget: HeldBudget,
    optimizer: SelectedOptimizer,
) -> tuple[Session, CycleResult]:
    """*observers* MUST be pre-built (ledger bound before origin); *dataset* is the whole bank.

    Answers the session the run ENDED on: a fork run init or a rebase took is a cycle of its own.
    """
    if not session.cycle_id:
        raise RuntimeError("run_optimization runs a minted cycle: bind its session first")
    session.state.clock = LaunchClock.now()
    try:
        # Round 0 outside every round the loop enters: the origin pass, its gate and the close.
        with (
            at_round(0),
            binding_optimizer(optimizer, determinism=campaign_config.optimization.determinism),
        ):
            if mode.diag and optimizer.plan.controller is None:
                raise PayloadInvalidError(
                    f"--diag shows what an optimizer's controller does at a round boundary, and "
                    f"{optimizer.name} declares no controller node"
                )
            scope_memory(session)
            partition = cycle_partition(session, dataset, campaign_config)
            dataset = list(partition.search)
            # Before this launch takes a cell: a re-read of a cell the campaign priced prices nothing.
            campaigns = session.store.campaigns
            bind_priced(
                scan_ledger_priced_keys(campaigns.campaign_cycle_ledgers(session.campaign_id))
            )
            refresh_rates_in_background()
            # Unconditional: the runner cannot know a child will recurse.
            publish_inner_spawn_context(session)
            # Fresh per launch, keeping only the cycles this run measures FOR.
            session.state.control = RunControl(enclosing=session.state.control.enclosing)
            try:
                prep = await prepare_run(
                    dataset,
                    campaign_config,
                    session=session,
                    partition=partition,
                    observers=observers,
                    budget=budget,
                    langfuse_session_id=langfuse_session_id,
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
                        diag=mode.diag,
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
                    dataset=dataset,
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
        # Whatever path exits, a fork included: the lock left the opened cycle when the line moved.
        locked = session.state.locked_cycle
        assert locked is not None, "a run opens on observers that hold its cycle"
        release_cycle(locked)
