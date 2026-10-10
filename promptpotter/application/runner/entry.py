from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from pathlib import Path

from promptpotter.application.bench.cycle import Cycle
from promptpotter.application.bench.resume_and_fork.fork_siblings import mint_fork
from promptpotter.application.campaign_config import (
    CampaignConfig,
    apply_config_overrides,
    apply_cycle_seed,
)
from promptpotter.application.initialization.loop_start import (
    init_optimization_loop,
    populate_session_scoring,
)
from promptpotter.application.initialization.session import Session
from promptpotter.application.intelligence.exploration import parent_level_trajectory
from promptpotter.application.optimizer_manifest import (
    bind_optimizer,
    select_optimizer,
    set_determinism_clamp,
)
from promptpotter.application.origin import (
    CampaignOrigin,
    establish_campaign_origin,
)
from promptpotter.application.pipeline_resolve import apply_node_overlay
from promptpotter.application.run_observers import (
    RunObservers,
    build_run_observers,
    declare_run_wiring,
)
from promptpotter.application.run_phase_control import RunControl, declare_run_stop
from promptpotter.application.runner.bench import bench_selection, own_level
from promptpotter.application.runner.campaign_result import (
    bank_campaign_result,
    bench_origin,
    declare_line_bench,
)
from promptpotter.application.runner.inner.ruler import refresh_inner_rulers
from promptpotter.application.runner.inner.spawn_context import publish_inner_spawn_context
from promptpotter.application.runner.loop import run_round_loop, set_round_cap
from promptpotter.application.runner.output import write_log_md, write_review_md
from promptpotter.application.runner.round import flush_pending_decisions, round_plan
from promptpotter.application.runner.termination import (
    RUN_ENDS,
    RUN_STOPS,
    end_run_on,
    run_stop_reason,
)
from promptpotter.application.scoring.evaluators import resolve_cell_formula
from promptpotter.application.scoring.query_loop import NEXT_CELL, FlightGauge
from promptpotter.application.scoring.sample_measurement import cell_bound
from promptpotter.application.views.ingress import run_spend_view
from promptpotter.config.settings import APP_VERSION
from promptpotter.domain.bench import BenchScore, partition_bank
from promptpotter.domain.campaign import ceiling_meter
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.export import PromptExport, build_prompt_export
from promptpotter.domain.launch_limits import HeldLimits, refuse_arm_halt
from promptpotter.domain.measurement_provenance import RunSource
from promptpotter.domain.phases import STOP_REASON_INFO, PauseCause, StopOutcome, StopReason
from promptpotter.domain.pipeline_overlay import (
    overlay_sets_model_outside_allowed,
    permitted_models_from_narrowing,
)
from promptpotter.domain.results import CycleResult, RoundResult, round_clocks
from promptpotter.domain.ruler import AbilityReading
from promptpotter.domain.run_records import (
    MAX_AUTO_REBASES,
    CycleFinal,
    CycleSeed,
    ErrorRecord,
    ForkSpec,
    RebaseRequest,
)
from promptpotter.domain.sample import Sample
from promptpotter.domain.spend import (
    CeilingMeter,
    SpendCeilings,
    SpendRollup,
)
from promptpotter.infrastructure.llm.pricing import refresh_rates_in_background
from promptpotter.infrastructure.llm.send_pacing import set_abort_check
from promptpotter.infrastructure.llm.spend_book import SpendBook, bound_spend_book
from promptpotter.infrastructure.llm.telemetry import bind_priced
from promptpotter.infrastructure.producer_lock import release_cycle
from promptpotter.infrastructure.runtime_flags import armed_run_limits
from promptpotter.infrastructure.store.archive_queries import (
    memory_scoped,
    scope_memory_to_own_answers,
)
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    scan_ledger_answers,
    scan_ledger_priced_keys,
    scan_ledger_wall_clock,
)
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.infrastructure.store.session_pointer import cleanup_stub_fork_if_empty
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import PayloadInvalidError
from promptpotter.shared.hashing import dataset_hash

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunMode:
    """Launch-SHAPE flags only: a bound on how far the run goes is a limit (``HeldLimits``)."""

    no_divergence_check: bool = False
    fork_on_divergence: bool = False
    diag: bool = False
    resume_from_round_override: int | None = None


def _arm_spend_book(
    observers: RunObservers,
    cycle_dir: Path | None,
    *,
    declared: SpendCeilings,
    meters: CeilingMeter,
    reserve: SpendCeilings,
) -> SpendBook:
    """Always armed, caps or none: ``change-run-limits`` can bind a run that declared no ceiling."""
    dashboard = observers.dashboard
    spent = dashboard.spend_metered(meters)
    book = SpendBook(
        declared=declared,
        reserved=reserve,
        cycle_dir=cycle_dir,
        meters=meters,
        usd_metered=spent.metered_usd,
        tokens_spent=spent.metered_tokens,
    )
    observers.arm_spend_book(book)
    return book


def _arm_run_controls(
    session: Session,
    observers: RunObservers,
    campaign_config: CampaignConfig,
) -> None:
    """Re-called per rebase/fork, which mints a different cycle dir; precedes origin scoring, which spends."""
    cycle_dir = session.store.campaigns.cycle_dir(session.hop) if session.state.cycle_id else None
    session.flight = FlightGauge(observers.callbacks.on_flight)
    # A provider's pushback moves inside a cell, while the phase's loop may be blocked on the cells out.
    session.backend_client.backpressure.on_change = session.flight.touch
    book = _arm_spend_book(
        observers,
        cycle_dir,
        declared=campaign_config.optimization.ceiling,
        meters=ceiling_meter(session.arm),
        reserve=session.reserve,
    )
    session.control = replace(session.control, cycle_dir=cycle_dir, book=book)
    # The rate-limit countdown polls it too — the one blocking seam that otherwise ignores a pause.
    set_abort_check(session.control.pause_requested)


def _read_cycle_seed(session: Session) -> CycleSeed | None:
    if not session.state.cycle_id:
        return None
    return session.store.campaigns.read_cycle_seed(session.hop)


@dataclass(frozen=True)
class _PreparedRun:
    """No ceiling of its own: the held one is SET INTO ``campaign_config``, so the value that halts is served."""

    origin: CampaignOrigin
    campaign_config: CampaignConfig
    halt_at_accuracy: float | None
    step_rounds: int | None


async def _prepare_run(
    dataset: list[Sample],
    campaign_config: CampaignConfig,
    *,
    session: Session,
    observers: RunObservers,
    limits: HeldLimits,
    langfuse_session_id: str | None,
) -> _PreparedRun:
    cb = observers.callbacks
    if session.controlled:
        refuse_arm_halt(limits.halt_at_accuracy)

    # Precedence is seed > dataset > backend.
    seed = _read_cycle_seed(session)
    if seed is not None and seed.pipeline_overlay:
        session.pipeline_params = apply_node_overlay(
            session.pipeline_params or {}, seed.pipeline_overlay, session.pipeline_schema
        )
    if seed is not None:
        campaign_config = apply_cycle_seed(campaign_config, seed)
        if (
            overlay_sets_model_outside_allowed(
                seed.pipeline_overlay,
                permitted_models_from_narrowing(campaign_config.optimizer_narrowing),
            )
            and session.state.cycle_id
        ):
            # The ADR-0005 babysit act, stamped here: the mint seam cannot, the index being created at init.
            session.store.campaigns.record_intervention(
                session.hop, kind="disallowed_model_override"
            )
            session.human_intervened = True

    # LAST, after the seed's other knobs; SET, never a `min`: the knob is one layer of the held ceiling.
    campaign_config = campaign_config.model_copy(
        update={
            "optimization": campaign_config.optimization.model_copy(
                update={"ceiling": limits.ceiling}
            )
        }
    )
    session.reserve = limits.reserve
    # A standing round cap outranks the seed's.
    campaigns = session.store.campaigns
    standing = campaigns.read_run_limits(session.hop) if session.state.cycle_id else None
    rounds = None if standing is None else standing.rounds
    if rounds is not None:
        campaign_config = set_round_cap(campaign_config, rounds.max_rounds)
    # Declared before origin scoring; the record is the readout, the arming below the enforcement.
    observers.tracing.start(
        config_snapshot=campaign_config.model_dump(mode="json"),
        dataset=dataset,
        langfuse_session_id=langfuse_session_id,
    )
    declare_run_wiring(session, campaign_config, cb.ledger, tracing=observers.tracing)
    unmoved = SpendCeilings()
    if standing is not None and (
        standing.ceiling != limits.operator or standing.reserve != unmoved
    ):
        # At the HELD values, never the request: the book prefers the standing ceiling over the config.
        campaigns.write_run_limits(
            session.hop, limits.operator, rounds=rounds, pause_at_round=None, reserve=unmoved
        )

    # After the declaration, and before the origin pass, which spends.
    _arm_run_controls(session, observers, campaign_config)

    populate_session_scoring(session, campaign_config, source=RunSource.ORIGIN)
    if (book := bound_spend_book()) is not None and (
        cell := await cell_bound(session, session.pipeline_params or {})
    ) is not None:
        # After the scorer, which the stop this raises is finalized with.
        book.refuse_unpriced(cell, NEXT_CELL)
    # `_CURRENT_ROUND` must be bound for what the origin pass spawns, or its measurements stamp `None`.
    cb.set_round(0)
    origin = await establish_campaign_origin(
        session,
        dataset,
        campaign_config,
        seed=seed,
        listener=cb,
    )
    if origin.locked_scoring is not None:
        campaign_config = campaign_config.model_copy(update={"scoring": origin.locked_scoring})

    return _PreparedRun(
        origin=origin,
        campaign_config=campaign_config,
        halt_at_accuracy=limits.halt_at_accuracy,
        step_rounds=limits.step_rounds,
    )


def _declare_round_allowance(session: Session, step_rounds: int | None) -> None:
    """A launch with no allowance clears the last launch's."""
    if not session.state.cycle_id:
        return
    campaigns = session.store.campaigns
    standing = campaigns.read_run_limits(session.hop)
    pause_at_round = (
        None if step_rounds is None else max(session.state.resumed_from_round - 1, 0) + step_rounds
    )
    if standing.pause_at_round != pause_at_round:
        campaigns.write_run_limits(
            session.hop,
            standing.ceiling,
            rounds=standing.rounds,
            pause_at_round=pause_at_round,
            reserve=standing.reserve,
        )


def _level_of(rr: RoundResult) -> AbilityReading | None:
    if rr.ability is None or rr.ability.se is None:
        return None
    return rr.ability


def _build_cycle_result(
    cycle: Cycle | None,
    session: Session,
    *,
    stop_reason: StopReason,
    cycle_error: ErrorRecord | None,
    started_at: str,
    finished_at: str,
    spend: SpendRollup | None,
    bench: BenchScore,
    langfuse_trace_id: str | None,
) -> CycleResult:
    picked = cycle.selection if cycle is not None else None
    picked_sp = cycle.selected_sp if cycle is not None else None
    # Round 0 is the reference the result is differenced against, never a search result.
    cycle_rounds = [rr for rr in cycle.rounds if rr.round > 0] if cycle is not None else []
    ds = cycle.difficulty.ruler if cycle is not None else None
    origin_lv: tuple[float, float] | None = None
    levels: list[tuple[float, float]] = []
    if cycle is not None:
        origin_lv, levels = parent_level_trajectory(
            _level_of(cycle.origin_round),
            [_level_of(rr) for rr in cycle_rounds],
            ds,
        )
    return CycleResult(
        rounds=cycle_rounds,
        n_rounds_after_origin=len(cycle_rounds),
        result_accuracy=picked.accuracy if picked is not None else None,
        result_round=picked.round if picked is not None else 0,
        origin=own_level(cycle.origin_round.results) if cycle is not None else None,
        origin_level=origin_lv[0] if origin_lv is not None else None,
        origin_level_se=origin_lv[1] if origin_lv is not None else None,
        round_levels=[t for t, _ in levels],
        round_level_ses=[se for _, se in levels],
        round_budget=cycle.config.optimization.max_rounds if cycle is not None else None,
        result_prompt_fields=picked_sp.prompt_fields if picked_sp else {},
        result_pipeline_params=picked_sp.pipeline_params if picked_sp else None,
        stop_reason=stop_reason,
        started_at=started_at,
        finished_at=finished_at,
        cycle_id=session.state.cycle_id,
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
    """``Cycle.selection``'s ``prompt_fields`` round-trip; ``result_prompt_fields`` renders shots in place of their ids."""
    if cycle is None:
        return None
    winner = cycle.selection
    campaign = session.store.campaigns.load_campaign(session.campaign_id)
    return build_prompt_export(
        winner,
        tool_version=APP_VERSION,
        campaign_id=session.campaign_id,
        cycle_id=session.state.cycle_id,
        dataset_name=campaign.dataset_name if campaign else (session.dataset_name or ""),
        dataset_hash=dataset_hash(session.samples),
        treatment=campaign.treatment if campaign else None,
        stop_reason=cycle_result.stop_reason,
        finished_at=cycle_result.finished_at,
        formula=formula,
        own=own_level(winner.results),
        framing=cycle.framing,
        demo=session.scoring.require_partition().demo,
        bench=cycle_result.bench,
    )


def _close_cycle(
    cycle: Cycle | None,
    session: Session,
    observers: RunObservers,
    *,
    stop_reason: StopReason,
    cycle_error: ErrorRecord | None,
    open_round: int | None,
    started_at: str,
    config: CampaignConfig,
    diag: bool,
    pause_cause: PauseCause,
) -> CycleResult:
    finished_at = utcnow_iso()
    # Before the result is built: a decision made after the last round has no next `persist_round`.
    if cycle is not None:
        flush_pending_decisions(cycle, session)
    banked = None if cycle is None else cycle.bench_passes
    bench = declare_line_bench(
        session, config, observers.callbacks, passes=banked, selecting=False, ending=stop_reason
    )
    if session.state.cycle_id:
        # A pause included: the next launch's clock is summed beside this one's.
        bank_campaign_result(
            session.store,
            session.hop,
            started_at=started_at,
            finished_at=finished_at,
            optimizer_phases=select_optimizer(config.optimization).phases,
        )
    cycle_result = _build_cycle_result(
        cycle,
        session,
        stop_reason=stop_reason,
        cycle_error=cycle_error,
        started_at=started_at,
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
        cycle=cycle,
        diag=diag,
        open_round=open_round,
        pause_cause=pause_cause,
    )
    return cycle_result


@dataclass
class _CycleOutcome:
    """``observers`` may have been REBUILT mid-run by fork-on-divergence; the driver keeps this one."""

    cycle_result: CycleResult
    fork: RebaseRequest | None
    observers: RunObservers


async def _run_single_cycle(
    prep: _PreparedRun,
    *,
    dataset: list[Sample],
    session: Session,
    observers: RunObservers,
    mode: RunMode,
    started_at: str,
) -> _CycleOutcome:
    origin = prep.origin
    campaign_config = prep.campaign_config
    cb = observers.callbacks
    pre_loop_cycle_id = session.state.cycle_id

    def forked_from() -> str | None:
        return pre_loop_cycle_id if pre_loop_cycle_id != session.state.cycle_id else None

    cycle: Cycle | None = None
    cancel_exc: asyncio.CancelledError | None = None
    book: SpendBook | None = None
    fork: RebaseRequest | None = None
    open_round: int | None = None
    pause_cause = PauseCause.INTERRUPT
    try:
        cycle = await init_optimization_loop(
            origin,
            dataset,
            campaign_config,
            cb=cb,
            no_divergence_check=mode.no_divergence_check,
            fork_on_divergence=mode.fork_on_divergence,
            cycle_id=session.state.cycle_id or None,
            resume_from_round_override=mode.resume_from_round_override,
            session=session,
        )

        if forked_from():
            observers = build_run_observers(
                session=session,
                campaign_config=campaign_config,
                resumed_from_round=session.state.resumed_from_round,
                forked_from=observers,
            )
            cb = observers.callbacks

        _arm_run_controls(session, observers, campaign_config)
        book = session.control.book
        if (
            campaign_config.bench_trigger != "manual"
            and session.scoring.require_partition().bench
            and origin.resolved_origin is not None
        ):
            # Its price is set aside so the selection's pass still fits under the search's ceiling.
            cycle.bench_passes = await bench_origin(
                session,
                origin.resolved_origin.to_job_search_point(
                    schema=session.pipeline_schema,
                    framing=cycle.framing,
                    demo=session.scoring.require_partition().demo,
                ),
                origin_id=origin.resolved_origin.id,
                spend=observers.dashboard.state.spend,
                cb=cb,
            )
            if cycle.bench_passes is not None and book.binds("bench"):
                book.set_aside(cycle.bench_passes.reserve_usd, cycle.bench_passes.reserve_tokens)
        declare_line_bench(
            session, campaign_config, cb, passes=cycle.bench_passes, selecting=True, ending=None
        )
        _declare_round_allowance(session, prep.step_rounds)
        stop_reason, cycle_error, fork, open_round, cancel_exc, stepped = await run_round_loop(
            cycle,
            dataset,
            campaign_config,
            session,
            cb,
            diag=mode.diag,
            halt_at_accuracy=prep.halt_at_accuracy,
        )
        if stepped:
            pause_cause = PauseCause.STEP
    except RUN_ENDS as exc:
        stop_reason, cycle_error = end_run_on(exc, session, where="before the round loop")
        # A cancellation still finalizes, then is re-raised past the finalize: it must reach the canceller.
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
            cycle.bench_passes = await bench_selection(
                session, cycle.rounds, framing=cycle.framing, banked=cycle.bench_passes, cb=cb
            )
        except (*RUN_STOPS, KeyboardInterrupt, asyncio.CancelledError) as exc:
            # Only a pause escapes the pass; the resume takes the pass again.
            stop_reason, cycle_error = run_stop_reason(exc), None
            if isinstance(exc, asyncio.CancelledError):
                cancel_exc = exc

    cycle_result = _close_cycle(
        cycle,
        session,
        observers,
        stop_reason=stop_reason,
        cycle_error=cycle_error,
        open_round=open_round,
        started_at=started_at,
        config=campaign_config,
        diag=mode.diag,
        pause_cause=PauseCause.CANCELLED if cancel_exc is not None else pause_cause,
    )
    # Ahead of the re-raise below: a cancellation is one of the interrupts that leaves an empty fork dir.
    if (parent_cycle_id := forked_from()) and cycle_result.n_rounds_after_origin == 0:
        cleanup_stub_fork_if_empty(
            campaign_store=session.store.campaigns,
            hop=session.hop,
            parent_cycle_id=parent_cycle_id,
        )

    if cancel_exc is not None:
        # The caught instance, not a fresh class — it carries the reason its raise site named.
        raise cancel_exc

    return _CycleOutcome(cycle_result=cycle_result, fork=fork, observers=observers)


def _mint_and_rebase_fork(
    prep: _PreparedRun,
    *,
    session: Session,
    observers: RunObservers,
    rebase_req: RebaseRequest,
    rebase_count: int,
) -> tuple[_PreparedRun, RunObservers]:
    """``config_overrides`` re-snapshot BEFORE the mint, so seed and in-process config agree."""
    parent_cycle_id = session.state.cycle_id
    seed: CycleSeed | None = None
    if rebase_req.config_overrides is not None:
        campaign_config = apply_config_overrides(prep.campaign_config, rebase_req.config_overrides)
        prep = replace(prep, campaign_config=campaign_config)
        # No `origin_prompt_fields`: a rebase replays its origin from the parent's round.
        seed = CycleSeed(config_overrides=rebase_req.config_overrides)
    new_cycle_id = mint_fork(
        campaign_store=session.store.campaigns,
        parent=CycleHop(campaign_id=session.campaign_id, cycle_id=parent_cycle_id),
        fork_from_round=rebase_req.fork_from_round,
        payload=ForkSpec(
            trigger=rebase_req.trigger,
            reason=rebase_req.reason,
            issued_by=rebase_req.issued_by,
            seed=seed,
        ),
    )
    session.state.cycle_id = new_cycle_id
    session.state.resumed_from_round = rebase_req.fork_from_round
    observers = build_run_observers(
        session=session,
        campaign_config=prep.campaign_config,
        resumed_from_round=rebase_req.fork_from_round,
        forked_from=observers,
    )
    logger.info(
        "Auto-rebase #%d/%d: %s → %s at round %d [trigger=%s, reason=%s]",
        rebase_count,
        MAX_AUTO_REBASES,
        parent_cycle_id,
        new_cycle_id,
        rebase_req.fork_from_round,
        rebase_req.trigger.value,
        rebase_req.reason,
    )
    return prep, observers


async def run_optimization(
    dataset: list[Sample],
    campaign_config: CampaignConfig,
    *,
    session: Session,
    observers: RunObservers,
    langfuse_session_id: str | None = None,
    mode: RunMode,
    limits: HeldLimits,
) -> CycleResult:
    """*observers* MUST be pre-built (ledger bound before origin); *dataset* is the whole bank."""
    try:
        return await _optimize(
            dataset,
            campaign_config,
            session=session,
            observers=observers,
            langfuse_session_id=langfuse_session_id,
            mode=mode,
            limits=limits,
        )
    finally:
        release_cycle(observers.cycle_dir)
        if session.state.cycle_id:
            release_cycle(session.store.campaigns.cycle_dir(session.hop))


async def _optimize(
    dataset: list[Sample],
    campaign_config: CampaignConfig,
    *,
    session: Session,
    observers: RunObservers,
    langfuse_session_id: str | None,
    mode: RunMode,
    limits: HeldLimits,
) -> CycleResult:
    started_at = utcnow_iso()
    campaign = session.store.campaigns.load_campaign(session.campaign_id)
    session.arm = None if campaign is None else campaign.arm
    if campaign is not None and (session.controlled or memory_scoped()):
        scope_memory_to_own_answers(
            scan_ledger_answers(
                CycleLayout(session.store.campaigns.cycle_dir(hop)).ledger
                for hop in session.store.campaigns.line(campaign.root_hop)
            )
        )
    partition = partition_bank(dataset, campaign_config.dataset_split)
    session.scoring.partition = partition
    dataset = list(partition.search)
    # Before this launch takes a cell: a re-read of a cell the campaign already priced prices nothing.
    session.state.priced_keys = scan_ledger_priced_keys(
        session.store.campaigns.campaign_cycle_ledgers(session.campaign_id)
    )
    bind_priced(session.state.priced_keys)
    refresh_rates_in_background()
    # Unconditional: the runner cannot know a child will recurse.
    publish_inner_spawn_context(session, campaign_config)
    # Round 0's cells need it too: their origin level is every candidate's baseline.
    refresh_inner_rulers(session, campaign_config, round_num=0)
    # Fresh per launch, keeping only the cycles this run measures FOR.
    session.control = RunControl(enclosing=session.control.enclosing)
    selected = select_optimizer(campaign_config.optimization)
    if mode.diag and round_plan(selected).controller is None:
        raise PayloadInvalidError(
            f"--diag shows what an optimizer's controller does at a round boundary, and "
            f"{selected.name} declares no controller node"
        )
    bind_optimizer(selected)
    set_determinism_clamp(campaign_config.optimization.determinism)
    try:
        prep = await _prepare_run(
            dataset,
            campaign_config,
            session=session,
            observers=observers,
            limits=limits,
            langfuse_session_id=langfuse_session_id,
        )
    except RUN_STOPS as stop:
        stop_reason, cycle_error = end_run_on(stop, session, where="in run prep")
        return _close_cycle(
            None,
            session,
            observers,
            stop_reason=stop_reason,
            cycle_error=cycle_error,
            open_round=None,
            started_at=started_at,
            config=campaign_config,
            diag=mode.diag,
            pause_cause=PauseCause.INTERRUPT,
        )
    except (KeyboardInterrupt, asyncio.CancelledError) as exc:
        # Prep sits outside `_run_single_cycle`'s try: undeclared, an interrupt here leaves a dead run `running`.
        declare_run_stop(
            session,
            StopReason.PAUSED,
            interrupted_by=(
                PauseCause.CANCELLED
                if isinstance(exc, asyncio.CancelledError)
                else PauseCause.INTERRUPT
            ),
        )
        observers.drain_all()
        raise
    rebase_count = 0
    while True:
        outcome = await _run_single_cycle(
            prep,
            dataset=dataset,
            session=session,
            observers=observers,
            mode=mode,
            started_at=started_at,
        )
        observers = outcome.observers
        cycle_result = outcome.cycle_result

        rebase_req = outcome.fork
        if (
            cycle_result.stop_reason != StopReason.REBASED
            or rebase_req is None
            or rebase_count >= MAX_AUTO_REBASES
            or session.state.cycle_id is None
        ):
            if rebase_req is not None and rebase_count >= MAX_AUTO_REBASES:
                logger.warning(
                    "Auto-rebase cap %d reached; ignoring further fork_proposals this session.",
                    MAX_AUTO_REBASES,
                )
            return cycle_result

        rebase_count += 1
        prep, observers = _mint_and_rebase_fork(
            prep,
            session=session,
            observers=observers,
            rebase_req=rebase_req,
            rebase_count=rebase_count,
        )


def _finalize_run(
    session: Session,
    observers: RunObservers,
    cycle_result: CycleResult,
    *,
    config: CampaignConfig,
    cycle: Cycle | None,
    diag: bool,
    open_round: int | None,
    pause_cause: PauseCause,
) -> None:
    stop_reason = cycle_result.stop_reason
    info = STOP_REASON_INFO[stop_reason]
    is_paused = info.outcome is StopOutcome.PAUSED
    # Both needed: the table says the reason leaves a partial round, the loop whether one was open.
    interrupted_round = open_round if info.halts_mid_round else None
    ceiling = armed_run_limits(
        Path(observers.cycle_dir), observers.dashboard.state.run_limits
    ).ceiling
    spend = run_spend_view(
        observers.dashboard.spend_metered(ceiling_meter(session.arm)),
        usd_cap=ceiling.usd,
        token_cap=ceiling.tokens,
    )
    if is_paused:
        # Declared where every paused exit converges, rather than trusting each raise site.
        declare_run_stop(
            session,
            stop_reason,
            interrupted_by=(
                PauseCause.BOUND if stop_reason is StopReason.PANEL_CUT else pause_cause
            ),
            spend=spend,
        )
    # A pause leaves the cycle ACTIVE: the PAUSED just declared must stay the last declaration read.
    if session.state.cycle_id and not is_paused:
        session.store.campaigns.bank_final(
            session.hop,
            interrupted_round=interrupted_round,
            final=CycleFinal(
                started_at=cycle_result.started_at,
                wall_clock=scan_ledger_wall_clock(
                    [CycleLayout(session.store.campaigns.cycle_dir(session.hop)).ledger],
                    started_at=cycle_result.started_at,
                    finished_at=cycle_result.finished_at,
                    optimizer_phases=select_optimizer(config.optimization).phases,
                ),
                **round_clocks(
                    cycle_result.rounds, accuracy_ceiling=config.accuracy_ceiling
                )._asdict(),
                prompt_hashes=select_optimizer(config.optimization).prompt_hashes(),
                origin_composite_fitness=(
                    None
                    if (origin := cycle_result.origin) is None or origin.composite is None
                    else origin.composite.value
                ),
                scorer_id=session.scoring.require_scorer().id,
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
                    session.scoring.require_scorer().per_cell, session.pipeline_schema
                )[0],
            ),
        )
    # After what it banked, before the digests that name the stop, and before the drain.
    if not is_paused:
        declare_run_stop(session, stop_reason, interrupted_by=pause_cause, spend=spend)
    if session.state.cycle_id and not is_paused:
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


__all__ = ["RunMode", "run_optimization"]
