"""``run_optimization`` — optimize-loop entry + teardown. Fork-on-divergence rebuilds observers
and re-seeds ``phase_ctx``, so RoundStartView keeps reading the parent's limits."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from pathlib import Path

from promptpotter.application.bench.cycle import Cycle
from promptpotter.application.bench.resume_and_fork.fork_siblings import (
    cleanup_stub_fork_if_empty,
    mint_fork,
)
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
    bound_optimizer,
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
    run_limits_from,
)
from promptpotter.application.run_phase_control import RunControl, declare_run_phase
from promptpotter.application.runner.bench import (
    bench_selection,
    headline,
    nothing_held_out,
)
from promptpotter.application.runner.campaign_result import bank_campaign_result, bench_origin
from promptpotter.application.runner.inner.ruler import refresh_inner_rulers
from promptpotter.application.runner.inner.spawn_context import publish_inner_spawn_context
from promptpotter.application.runner.loop import run_round_loop, set_round_cap
from promptpotter.application.runner.output import write_log_md, write_review_md
from promptpotter.application.runner.round import flush_pending_decisions
from promptpotter.application.runner.termination import (
    RUN_ENDS,
    RUN_STOPS,
    end_run_on,
    run_stop_reason,
)
from promptpotter.application.scoring.evaluators import resolve_cell_formula
from promptpotter.application.scoring.query_loop import FlightGauge
from promptpotter.application.views.ingress import run_spend_view
from promptpotter.config.settings import APP_VERSION
from promptpotter.domain.bench import BenchPasses, BenchScore, partition_bank
from promptpotter.domain.campaign import ceiling_meter
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.export import PromptExport, build_prompt_export
from promptpotter.domain.launch_limits import HeldLimits, refuse_arm_halt
from promptpotter.domain.measurement_provenance import RunSource
from promptpotter.domain.phases import STOP_REASON_INFO, RunPhase, StopOutcome, StopReason
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
    BudgetChange,
    CeilingMeter,
    SpendCeilings,
    SpendRollup,
    declare_ceiling,
)
from promptpotter.infrastructure.llm.pricing import refresh_rates_in_background
from promptpotter.infrastructure.llm.rate_limit import get_abort_check, set_abort_check
from promptpotter.infrastructure.llm.spend_book import SpendBook
from promptpotter.infrastructure.llm.telemetry import bind_priced
from promptpotter.infrastructure.runtime_flags import (
    clear_run_control_flags,
    read_reserve_mirror,
    read_run_limits_mirror,
    write_run_limits_mirror,
)
from promptpotter.infrastructure.store.archive_queries import scope_memory_to_own_runs
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    scan_ledger_priced_keys,
    scan_ledger_run_ids,
    scan_ledger_wall_clock,
)
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.hashing import dataset_hash

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunMode:
    """The launch-shape flags that select a run's behaviour, grouped so the runner seam takes one
    value instead of six loose booleans. Everything else it takes is run CONTENT."""

    no_divergence_check: bool = False
    fork_on_divergence: bool = False
    diag: bool = False
    resume_from_round_override: int | None = None
    # Manual `step-round`: advance exactly this many rounds then halt at the round boundary,
    # overriding the configured ceiling — for a delegate that cannot fire an autonomous run.
    stop_after_rounds: int | None = None


def _arm_spend_book(
    observers: RunObservers,
    cycle_dir: Path | None,
    *,
    declared: SpendCeilings,
    meters: CeilingMeter,
    reserve: SpendCeilings,
) -> SpendBook:
    """**Always armed**, because a run's ceiling is not settled at launch: the probes re-read
    ``.runtime/run_limits.json`` each tick, so ``change-run-limits`` can bind a run that declared
    nothing. Returning no book for a launch with no starting caps is what let that command ack
    ``applied`` against a ceiling that could never trip — set by the operator, served to the
    webapp, enforced by nothing. An unset arm still costs nothing: the book skips a ``None`` cap.
    The book it arms is what admits every call the run sends."""
    dashboard = observers.dashboard
    spent = dashboard.spend_metered(meters)

    # The mirror carries only the arms moved since launch, so each reading is the launch's own
    # with those laid over it — one layering, the one admission composed the ceiling with. No
    # cycle is no mirror: nothing can have moved an arm.
    def _ceiling() -> SpendCeilings:
        if cycle_dir is None:
            return declared
        return declare_ceiling(declared, read_run_limits_mirror(cycle_dir).ceiling)

    def _reserve() -> SpendCeilings:
        if cycle_dir is None:
            return reserve
        return declare_ceiling(reserve, read_reserve_mirror(cycle_dir))

    # Seeded from the rollup the resume folded, then fed by the ledger itself.
    book = SpendBook(
        usd_cap=lambda: _ceiling().usd,
        tokens_cap=lambda: _ceiling().tokens,
        usd_reserve=lambda: _reserve().usd,
        tokens_reserve=lambda: _reserve().tokens,
        meters=meters,
        usd_spent=spent.metered_usd,
        tokens_spent=spent.metered_tokens,
    )
    observers.arm_spend_book(book)
    return book


def _arm_run_controls(
    session: Session,
    observers: RunObservers,
    campaign_config: CampaignConfig,
) -> None:
    """The one place the ceiling and the operator's flags are ARMED, over the cycle dir the run is
    actually in — re-called per rebase/fork, which mints a different one. It precedes origin
    scoring, the longest interruptible phase and one that spends before any round loop."""
    cycle_dir = session.store.campaigns.cycle_dir(session.hop) if session.state.cycle_id else None
    session.flight = FlightGauge(observers.callbacks.on_flight)
    # A provider's pushback moves inside a cell, where the phase's loop may be blocked on the
    # cells out — so the backpressure itself says when the reading moved.
    session.backend_client.backpressure.on_change = session.flight.touch
    book = _arm_spend_book(
        observers,
        cycle_dir,
        declared=SpendCeilings(
            campaign_config.optimization.spend_budget_usd,
            campaign_config.optimization.token_budget,
        ),
        meters=ceiling_meter(session.arm),
        reserve=session.reserve,
    )
    session.control = replace(session.control, cycle_dir=cycle_dir, book=book)
    # The rate-limit countdown polls it too — the one blocking seam that otherwise ignores a pause.
    set_abort_check(session.control.pause_requested)


def _read_cycle_seed(session: Session) -> CycleSeed | None:
    """This cycle's declared-at-mint seed, or ``None`` when unseeded. The cycle_id is already on
    ``session.state``, so the lookup is non-circular with cycle-id derivation."""
    if not session.state.cycle_id:
        return None
    return session.store.campaigns.read_cycle_seed(session.hop)


@dataclass(frozen=True)
class _PreparedRun:
    """Resolved run inputs — the straight-line prep done once before the rebase loop.
    ``campaign_config`` re-emits because a seed may reconcile new limits.

    There is deliberately no ``spend_budget_usd`` beside it: the held ceiling is SET INTO
    ``campaign_config.optimization`` by ``_prepare_run``, so one value both halts the run and
    reaches every reader. Held separately, the cap that halted was invisible — ``run_limits`` in
    ``dashboard.json`` reported the campaign's declared default while a different number bound.
    ``halt_at_accuracy`` rides here instead because no config knob holds it."""

    origin: CampaignOrigin
    campaign_config: CampaignConfig
    halt_at_accuracy: float | None


def _set_held_ceiling(config: CampaignConfig, ceiling: SpendCeilings) -> CampaignConfig:
    """SET the run's budget arms to the ceiling it HOLDS — never a ``min`` against the config.
    The config's knob is already one layer of that ceiling (`jobs/quota.py::declare_run_ceiling`),
    so bounding by it again here pins every launch at or under the knob, whatever it declared."""
    return config.model_copy(
        update={
            "optimization": config.optimization.model_copy(
                update={"spend_budget_usd": ceiling.usd, "token_budget": ceiling.tokens}
            )
        }
    )


async def _prepare_run(
    dataset: list[Sample],
    campaign_config: CampaignConfig,
    *,
    session: Session,
    observers: RunObservers,
    limits: HeldLimits,
) -> _PreparedRun:
    cb = observers.callbacks
    if session.controlled:
        refuse_arm_halt(limits.halt_at_accuracy)

    # A fresh launch supersedes any prior run-control intent: a stale `pause.flag` would pause
    # this very resume on its first poll, so a paused cycle could never be resumed. Binding
    # after it makes the origin pass below pausable like every other phase.
    launch_cycle_dir: Path | None = None
    if session.state.cycle_id:
        launch_cycle_dir = session.store.campaigns.cycle_dir(session.hop)
        clear_run_control_flags(launch_cycle_dir)

    # Read HERE — the single runner seam every launch path funnels through — never threaded
    # through each launcher. Precedence is seed > dataset > backend.
    seed = _read_cycle_seed(session)
    if seed is not None and seed.pipeline_overlay:
        session.pipeline_params = apply_node_overlay(
            session.pipeline_params or {}, seed.pipeline_overlay, session.pipeline_schema
        )
    if seed is not None:
        # Onto a FRESH config snapshot, reassigned before any downstream call.
        campaign_config = apply_cycle_seed(campaign_config, seed)
        if (
            overlay_sets_model_outside_allowed(
                seed.pipeline_overlay,
                permitted_models_from_narrowing(campaign_config.optimizer_narrowing),
            )
            and session.state.cycle_id
        ):
            # Steering the model OUTSIDE what the node permits (nothing declared = nothing
            # sanctioned) is the ADR-0005 babysit act. Stamped here because the mint seam could
            # not — the index is created at init. A PERMITTED steer reaches this seam and is clean.
            session.store.campaigns.mark_human_intervened(
                session.hop,
                kind="disallowed_model_override",
                at=utcnow_iso(),
            )
            session.human_intervened = True

    # LAST, after the seed's other knobs: the held ceiling is the one number this config and the
    # dashboard both carry, composed and admitted before launch.
    campaign_config = _set_held_ceiling(campaign_config, limits.ceiling)
    session.reserve = limits.reserve
    # A standing round cap outranks the seed's, as the standing spend ceiling does; it admits
    # nothing, so it is set here rather than composed with the budget before launch.
    campaigns = session.store.campaigns
    standing = campaigns.read_run_limits(session.hop) if launch_cycle_dir is not None else None
    rounds = None if standing is None else standing.rounds
    if rounds is not None:
        campaign_config = set_round_cap(campaign_config, rounds.max_rounds)
    # Stamped before origin scoring, which is where the operator spends the longest stretch of the
    # run. The stamp is the READOUT; the enforcement is the arming further down, and only both
    # together mean "the ceiling holds".
    observers.dashboard.stamp_run_limits(run_limits_from(campaign_config))
    if (
        launch_cycle_dir is not None
        and standing is not None
        and (limits.operator != BudgetChange(None, None) or rounds is not None)
    ):
        # The operator's arms are the cycle's STANDING ceiling, so a plain relaunch declares them
        # again rather than falling back to the knob. Held values, not the request: the gate
        # prefers the mirror over the config, so landing more than was admitted would let the run
        # escape its own admission. A launch that moved nothing re-lands the swept mirror alone.
        unmoved = BudgetChange(None, None)
        if standing.ceiling != limits.operator:
            campaigns.write_run_limits(session.hop, limits.operator, rounds=rounds, reserve=unmoved)
        else:
            write_run_limits_mirror(
                launch_cycle_dir, limits.operator, rounds=rounds, reserve=unmoved
            )

    # After the mirror, so the book's probes read the same ceiling the config carries, and
    # before the origin pass, which spends without one otherwise.
    _arm_run_controls(session, observers, campaign_config)

    # Once per session, under the config the seed reconciled: every origin branch hands
    # `Cycle.start` a graded report, and every rebase reuses this scorer.
    populate_session_scoring(session, campaign_config, source=RunSource.ORIGIN)
    # Round 0 IS a round, so it is declared like any other: `_CURRENT_ROUND` must be bound
    # for everything the origin pass spawns, or every origin measurement stamps `None`.
    cb.set_round(0)
    # The single origin seam. A no-edit operator fork inherits its branch-point candidate's
    # recorded accuracy rather than re-rolling it under a nondeterministic backend.
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
    )


def _level_of(rr: RoundResult) -> AbilityReading | None:
    """A round's frontier reading, or ``None`` if never fit. WHOLE — θ, its precision and the scale
    travel together, because a level with no scale cannot be differenced against another."""
    if rr.ability is None or rr.ability.se is None:
        return None
    return rr.ability


def _build_cycle_result(
    cycle: Cycle | None,
    origin: CampaignOrigin | None,
    session: Session,
    *,
    stop_reason: StopReason,
    cycle_error: ErrorRecord | None,
    started_at: str,
    finished_at: str,
    spend: SpendRollup | None,
    bench: BenchScore | None,
) -> CycleResult:
    """Assemble the terminal :class:`CycleResult`; ``cycle is None`` is the init-crash fallback, and
    ``origin is None`` a stop inside origin scoring. Every ``result_*`` names ``Cycle.selection``,
    the pick the optimizer declared and the bench grades."""
    picked = cycle.selection if cycle is not None else None
    picked_sp = cycle.selected_sp if cycle is not None else None
    # Round 0 is the reference the whole result is differenced against, carried beside it as
    # ``origin_accuracy`` / ``origin_level``. Counting it as a search result would credit the
    # outer loop with the floor it started from.
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
        origin_accuracy=origin.report.accuracy if origin is not None else None,
        origin_composite_fitness=(
            cycle.origin_round.composite_fitness if cycle is not None else None
        ),
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
        session_id=session.session_id or None,
        resumed_from_round=session.state.resumed_from_round,
        spend=spend,
        error=cycle_error,
        bench=bench,
    )


def _export_artifact(
    session: Session,
    cycle_result: CycleResult,
    cycle: Cycle | None,
    *,
    formula: str | None,
) -> PromptExport | None:
    """``None`` when no cycle started: there is no measured prompt to hand a consumer, and an
    artifact whose whole point is a fitness with provenance may not carry an unmeasured one. The
    round it projects is ``Cycle.selection``, whose ``prompt_fields`` round-trip where
    ``result_prompt_fields`` — the wire side, shots rendered in place of their ids — cannot."""
    if cycle is None:
        return None
    winner = cycle.selection
    # `campaign.json` is the one owner of both — every other surface derives from it, and a
    # second copy here would be one more thing to re-sync.
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
        origin_accuracy=cycle_result.origin_accuracy,
        origin_composite_fitness=cycle_result.origin_composite_fitness,
        framing=cycle.framing,
        demo=session.scoring.require_partition().demo,
        bench=cycle_result.bench,
    )


def _close_cycle(
    cycle: Cycle | None,
    origin: CampaignOrigin | None,
    session: Session,
    observers: RunObservers,
    *,
    stop_reason: StopReason,
    cycle_error: ErrorRecord | None,
    open_round: int | None,
    started_at: str,
    config: CampaignConfig,
    diag: bool,
    bench: BenchScore | None,
    banked: BenchPasses | None,
) -> CycleResult:
    """The one terminal path, for a stop raised in the round loop and one raised in run init.
    *bench* is the reading of *banked*, the passes the campaign's result holds from here on."""
    finished_at = utcnow_iso()
    # Before the result is built: a decision made after the last round closed has no next
    # `persist_round` to carry it, and every stop reason lands here.
    if cycle is not None:
        flush_pending_decisions(cycle, session)
    if session.state.cycle_id:
        # A pause included: the next launch's clock is summed beside this one's.
        bank_campaign_result(
            session.store,
            session.hop,
            started_at=started_at,
            finished_at=finished_at,
            optimizer_phases=bound_optimizer().phases,
            bench=banked,
        )
    cycle_result = _build_cycle_result(
        cycle,
        origin,
        session,
        stop_reason=stop_reason,
        cycle_error=cycle_error,
        started_at=started_at,
        finished_at=finished_at,
        # In-memory, not the debounced ``dashboard.json``: at finalize the live rollup is
        # already complete.
        spend=observers.dashboard.state.spend,
        bench=bench,
    )
    langfuse_trace_id = _finalize_run(
        session,
        observers,
        cycle_result,
        config=config,
        cycle=cycle,
        diag=diag,
        open_round=open_round,
    )
    if langfuse_trace_id is not None:
        cycle_result = cycle_result.model_copy(update={"langfuse_trace_id": langfuse_trace_id})
    return cycle_result


@dataclass
class _CycleOutcome:
    """One cycle run to completion. The observers may have been REBUILT mid-run by fork-on-divergence,
    so the driver keeps this live reference rather than the one it passed in."""

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
    langfuse_session_id: str | None,
    started_at: str,
) -> _CycleOutcome:
    """Run ONE cycle end-to-end: init → round loop → finalize, wrapped in the crash handlers that
    land a broken bring-up in CRASHED / DIVERGED / PAUSED. Loop-free — auto-rebase is the caller's."""
    origin = prep.origin
    campaign_config = prep.campaign_config
    cb = observers.callbacks
    pre_loop_cycle_id = session.state.cycle_id

    def forked_from() -> str | None:
        """The cycle this run forked off, once run init has minted a fork under it."""
        return pre_loop_cycle_id if pre_loop_cycle_id != session.state.cycle_id else None

    cycle: Cycle | None = None
    cancel_exc: asyncio.CancelledError | None = None
    book: SpendBook | None = None
    banked: BenchPasses | None = None
    unheld: BenchScore | None = None
    fork: RebaseRequest | None = None
    open_round: int | None = None
    try:
        cycle = await init_optimization_loop(
            origin,
            dataset,
            campaign_config,
            cb=cb,
            no_divergence_check=mode.no_divergence_check,
            fork_on_divergence=mode.fork_on_divergence,
            langfuse_session_id=langfuse_session_id,
            cycle_id=session.state.cycle_id or None,
            resume_from_round_override=mode.resume_from_round_override,
            session=session,
            started_at=started_at,
        )

        # Fork-on-divergence: rebuild observers around the fork's own ledger.
        if forked_from():
            observers = build_run_observers(
                session=session,
                campaign_config=campaign_config,
                resumed_from_round=session.state.resumed_from_round,
                forked_from=observers,
            )
            cb = observers.callbacks

        # The book is seeded off the dashboard, which already owns the spend rollup, rather
        # than a parallel reader; `observers` is bound in the builder so the rebase loop's
        # rebuild cannot leave it on a stale ref.
        _arm_run_controls(session, observers, campaign_config)
        book = session.control.book
        if session.scoring.require_partition().bench and origin.resolved_origin is not None:
            # The reference, sent once per line however many launches resume it; its price is set
            # aside so the selection's pass still fits under the ceiling the search spends against.
            banked = await bench_origin(
                session,
                origin.resolved_origin.to_job_search_point(
                    base_pipeline_params=session.pipeline_params or None,
                    schema=session.pipeline_schema,
                    framing=cycle.framing,
                    demo=session.scoring.require_partition().demo,
                ),
                started_at=started_at,
                optimizer_phases=bound_optimizer().phases,
                spend=observers.dashboard.state.spend,
                cb=cb,
            )
            if banked is not None and book.binds("bench"):
                book.set_aside(banked.reserve_usd, banked.reserve_tokens)
        elif not session.scoring.require_partition().bench:
            unheld = nothing_held_out(cb, scorer_id=session.scoring.scorer_id)
        stop_reason, cycle_error, fork, open_round, cancel_exc = await run_round_loop(
            cycle,
            dataset,
            campaign_config,
            session,
            cb,
            diag=mode.diag,
            halt_at_accuracy=prep.halt_at_accuracy,
            stop_after_rounds=mode.stop_after_rounds,
        )
    except RUN_ENDS as exc:
        stop_reason, cycle_error = end_run_on(exc, session, where="before the round loop")
        # Where a terminal Ctrl+C lands (`asyncio.Runner` cancels the main task first) and
        # where an inner campaign cancelled by its outer sample deadline lands. It still
        # finalizes — the cycle's state must reach disk exactly as a pause does — but it must
        # ALSO reach the canceller, so it is re-raised past the finalize below: answering a
        # cancellation with a return is what made the L4 sample deadline unenforceable. This arm
        # is the one struck before the round loop; the loop hands back its own with the round.
        if isinstance(exc, asyncio.CancelledError):
            cancel_exc = exc

    bench: BenchScore | None = unheld
    if (
        cycle is not None
        and book is not None
        and banked is not None
        and STOP_REASON_INFO[stop_reason].grades_selection
    ):
        book.set_aside(0.0, 0)
        try:
            banked = await bench_selection(cycle, session, banked=banked, cb=cb)
            bench = headline(cb, session, banked)
        except (*RUN_STOPS, KeyboardInterrupt, asyncio.CancelledError) as exc:
            # Only a pause escapes the pass; it keeps the cycle resumable, and the resume takes
            # the pass again.
            stop_reason, cycle_error = run_stop_reason(exc), None
            if isinstance(exc, asyncio.CancelledError):
                cancel_exc = exc

    cycle_result = _close_cycle(
        cycle,
        origin,
        session,
        observers,
        stop_reason=stop_reason,
        cycle_error=cycle_error,
        open_round=open_round,
        started_at=started_at,
        config=campaign_config,
        diag=mode.diag,
        bench=bench,
        banked=banked,
    )
    # A fork that never completed a round leaves an empty dir. Ahead of the re-raise below,
    # because a cancellation is one of the interrupts that produces one.
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
    """Mint the auto-rebase fork and rebuild observers around the new cycle's ledger. A rebase
    carrying ``config_overrides`` re-snapshots BEFORE the mint, so seed and in-process config agree."""
    parent_cycle_id = session.state.cycle_id
    seed: CycleSeed | None = None
    if rebase_req.config_overrides is not None:
        campaign_config = apply_config_overrides(prep.campaign_config, rebase_req.config_overrides)
        prep = replace(prep, campaign_config=campaign_config)
        # No `origin_prompt_fields`: a rebase replays its origin from the parent's round, so it
        # has no C0 provenance to stamp.
        seed = CycleSeed(config_overrides=rebase_req.config_overrides)
    new_cycle_id = mint_fork(
        campaign_store=session.store.campaigns,
        parent=CycleHop(campaign_id=session.campaign_id, cycle_id=parent_cycle_id),
        session_id=session.session_id or "",
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
    """End-to-end optimization, origin scoring included. *observers* MUST be pre-built (ledger bound
    before origin). *dataset* is the whole bank; everything below this seam reads only the part of
    it the declared split leaves to the search."""
    started_at = utcnow_iso()
    campaign = session.store.campaigns.load_campaign(session.campaign_id)
    session.arm = None if campaign is None else campaign.arm
    if campaign is not None and session.controlled:
        # Its MEMORY is what its own line filed — resumed off every ledger on it, grown per run.
        scope_memory_to_own_runs(
            scan_ledger_run_ids(
                CycleLayout(session.store.campaigns.cycle_dir(hop)).ledger
                for hop in session.store.campaigns.line(campaign.root_hop)
            )
        )
    partition = partition_bank(dataset, campaign_config.dataset_split)
    session.scoring.partition = partition
    dataset = list(partition.search)
    # Before this launch takes a cell: every cycle of the campaign priced its cells once, and a
    # re-read of one — a resume, a fork, a parent re-scored each round — prices nothing again.
    # Its optimizer and judge calls ride the same set, so a replayed call is priced once too.
    session.state.priced_keys = scan_ledger_priced_keys(
        session.store.campaigns.campaign_cycle_ledgers(session.campaign_id)
    )
    bind_priced(session.state.priced_keys)
    # Every launch path reaches here; bolted onto one entry point instead, it leaves the others
    # pricing off whatever table shipped. No-op on a fresh cache.
    refresh_rates_in_background()
    # Unconditional (the runner cannot know a child will recurse) and re-entrant (each level
    # publishes its own); a no-op until the cycle_id is set.
    publish_inner_spawn_context(session, campaign_config)
    # Round 0's cells need it too: their origin level is the baseline every candidate is
    # differenced against.
    refresh_inner_rulers(session, campaign_config, round_num=0)
    # An L4 spawner's stop, read ONCE and before anything binds: each rebase re-arms in this same
    # task, where the ContextVar by then holds the predicate the last cycle bound.
    session.control = RunControl(enclosing_pause=get_abort_check())
    # Both per task, so an inner cell binds its own optimizer and clamp over the outer
    # campaign's copies in its context — a cell measures under its own panel's or none.
    bind_optimizer(select_optimizer(campaign_config.optimization))
    set_determinism_clamp(campaign_config.optimization.determinism)
    try:
        prep = await _prepare_run(
            dataset,
            campaign_config,
            session=session,
            observers=observers,
            limits=limits,
        )
    except RUN_STOPS as stop:
        # Origin scoring stops on the round loop's channel — the spend ceiling, an unreachable
        # backend, a spent provider account — so it ends on the round loop's path, never a crash.
        stop_reason, cycle_error = end_run_on(stop, session, where="in run prep")
        return _close_cycle(
            None,
            None,
            session,
            observers,
            stop_reason=stop_reason,
            cycle_error=cycle_error,
            open_round=None,
            started_at=started_at,
            config=campaign_config,
            diag=mode.diag,
            bench=None,
            banked=None,
        )
    except (KeyboardInterrupt, asyncio.CancelledError):
        # Prep is the only phase outside `_run_single_cycle`'s try, and the longest. An
        # interrupt escaping here declares no phase and drains nothing, so `dashboard.json`
        # keeps `declared_phase: "running"` and every reader that trusts the declaration —
        # `paused` is the one thing derivation cannot re-derive — reports a dead run as healthy.
        declare_run_phase(session, RunPhase.PAUSED)
        observers.drain_all()
        raise
    # Run one cycle to completion; if it finalized REBASED with a stashed request under the
    # cap, mint the fork and run the next cycle on it. Every other stop reason returns.
    rebase_count = 0
    while True:
        outcome = await _run_single_cycle(
            prep,
            dataset=dataset,
            session=session,
            observers=observers,
            mode=mode,
            langfuse_session_id=langfuse_session_id,
            started_at=started_at,
        )
        observers = outcome.observers  # may have been rebuilt by fork-on-divergence
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
) -> str | None:
    """Returns the Langfuse trace id from the terminal ``end_campaign`` emit (``None`` when
    no tracing bridge is active) so the caller can stamp it onto the returned ``CycleResult``.
    *open_round* is the round the stop left unabsorbed, ``None`` for a stop at a boundary.
    """
    stop_reason = cycle_result.stop_reason
    info = STOP_REASON_INFO[stop_reason]
    is_paused = info.outcome is StopOutcome.PAUSED
    # Two facts, both needed: the table says whether this reason leaves a PARTIAL round on disk,
    # the loop says whether a round was open when it struck — a budget stop at a clean boundary
    # interrupts nothing.
    interrupted_round = open_round if info.halts_mid_round else None
    has_traceback = info.has_traceback
    limits = observers.dashboard.state.run_limits
    spend = run_spend_view(
        observers.dashboard.spend_metered(ceiling_meter(session.arm)),
        usd_cap=limits.spend_budget_usd if limits else None,
        token_cap=limits.token_budget if limits else None,
    )
    if is_paused:
        # DECLARE the pause at the one point every paused exit converges on, rather than
        # trusting each raise site. Skipping the terminal writes below leaves `derive_run_phase`
        # only two ways to read `paused` — the flag or a declaration — and Ctrl+C inside the
        # round loop sets neither, so it falls through to freshness, returns DETACHED, and the
        # reaper stamps `producer_vanished` on a cycle its owner deliberately cancelled.
        declare_run_phase(session, RunPhase.PAUSED, spend=spend)
    # A pause leaves the cycle ACTIVE and resumable, so every terminal-marking write is skipped
    # and `index.json` keeps no `finished_at` for `derive_run_phase` to read past the PAUSED
    # just declared. The partial round is still drained below.
    if session.state.cycle_id and not is_paused:
        # The active exception is gone from `sys.exc_info()` by now; the except clause stashed
        # the formatted traceback before returning.
        crash_traceback = session.state.crash_traceback if has_traceback else None

        session.store.campaigns.mark_finished(
            session.hop,
            stop_reason=stop_reason,
            finished_at=cycle_result.finished_at,
            interrupted_round=interrupted_round,
            crash_traceback=crash_traceback,
            final=CycleFinal(
                started_at=cycle_result.started_at,
                wall_clock=scan_ledger_wall_clock(
                    [CycleLayout(session.store.campaigns.cycle_dir(session.hop)).ledger],
                    started_at=cycle_result.started_at,
                    finished_at=cycle_result.finished_at,
                    optimizer_phases=bound_optimizer().phases,
                ),
                **round_clocks(
                    cycle_result.rounds, accuracy_ceiling=config.accuracy_ceiling
                )._asdict(),
                prompt_hashes=bound_optimizer().prompt_hashes(),
                origin_composite_fitness=cycle_result.origin_composite_fitness,
                scorer_id=session.scoring.scorer_id,
                mode="diag" if diag else "full",
                result_round=cycle_result.result_round,
                result_prompt_fields=cycle_result.result_prompt_fields,
                result_pipeline_params=cycle_result.result_pipeline_params,
            ),
            # The formula every exported number was computed under, resolved by the same call
            # run init stamps the index with.
            export=_export_artifact(
                session,
                cycle_result,
                cycle,
                formula=resolve_cell_formula(
                    session.scoring.scorer_cell_formula, session.pipeline_schema
                )[0],
            ),
        )
        write_log_md(session, config)
        # Re-rendered once `final` is banked: the round-close render could not carry the bench
        # score, which is taken after the last round closes.
        if cycle is not None:
            write_review_md(session, cycle)
    # Declared BEFORE the drain, so dashboard.json's stopped state is in place before the audit
    # settles; the append reaches the projection as a subscriber, the door every other fact takes.
    if not is_paused:
        declare_run_phase(session, RunPhase.TERMINAL, stop_reason=stop_reason, spend=spend)
    observers.drain_all(interrupted=interrupted_round is not None)

    obs = session.state.obs
    langfuse_trace_id: str | None = None
    if obs:
        langfuse_trace_id = obs.end_campaign(
            session.state.tracing_campaign_id,
            result_accuracy=cycle_result.result_accuracy,
            n_rounds_after_origin=cycle_result.n_rounds_after_origin,
            stop_reason=stop_reason,
            result_round=cycle_result.result_round,
        )
    return langfuse_trace_id


__all__ = ["RunMode", "run_optimization"]
