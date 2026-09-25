"""``run_optimization`` — optimize-loop entry + teardown. Fork-on-divergence rebuilds observers
and re-seeds ``phase_ctx``, so RoundStartView keeps reading the parent's limits."""

from __future__ import annotations

import asyncio
import logging
import traceback
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from typing import Any

from promptpotter.application.campaign_config import CampaignConfig
from promptpotter.application.initialization.loop_start import init_optimization_loop
from promptpotter.application.initialization.session import Session
from promptpotter.application.intelligence.exploration import parent_level_trajectory
from promptpotter.application.optimization.cycle import Cycle
from promptpotter.application.optimization.dispatch.llm_call.prompts import (
    compute_optimizer_prompt_hashes,
    load_optimizer_set_overrides,
    set_determinism_clamp,
    set_optimizer_prompt_overrides,
)
from promptpotter.application.optimization.resume_and_fork.fork_siblings import (
    _mint_fork,
    cleanup_stub_fork_if_empty,
)
from promptpotter.application.origin import (
    CampaignOrigin,
    establish_campaign_origin,
)
from promptpotter.application.pipeline_resolve import apply_node_overlay
from promptpotter.application.run_observers import (
    ForkInfo,
    RunObservers,
    build_run_observers,
    run_limits_from,
)
from promptpotter.application.run_phase_control import declare_run_phase
from promptpotter.application.runner.inner.ruler import refresh_inner_rulers
from promptpotter.application.runner.inner.spawn_context import publish_inner_spawn_context
from promptpotter.application.runner.loop import run_round_loop, set_round_cap
from promptpotter.application.runner.output import write_log_md
from promptpotter.application.runner.round import flush_pending_decisions
from promptpotter.application.runner.termination import RUN_STOPS, BudgetGate, run_stop_reason
from promptpotter.application.scoring.evaluators import resolve_cell_formula
from promptpotter.application.scoring.formula import split_scoring_block
from promptpotter.application.scoring.query_loop import FlightGauge
from promptpotter.config.settings import APP_VERSION
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.export import PromptExport, build_prompt_export
from promptpotter.domain.launch_limits import HeldLimits
from promptpotter.domain.phases import STOP_REASON_INFO, RunPhase, StopOutcome, StopReason
from promptpotter.domain.pipeline_overlay import (
    overlay_sets_model_outside_allowed,
    permitted_models_from_narrowing,
)
from promptpotter.domain.results import CycleResult, RoundResult, round_clocks
from promptpotter.domain.ruler import AbilityReading
from promptpotter.domain.run_records import (
    ConfigOverrides,
    CycleSeed,
    ErrorRecord,
    ForkSpec,
    RebaseRequest,
)
from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import ScoringSpec
from promptpotter.domain.spend import BudgetChange, SpendCeilings, SpendRollup
from promptpotter.infrastructure.llm.pricing import refresh_rates_in_background
from promptpotter.infrastructure.llm.rate_limit import get_abort_check, set_abort_check
from promptpotter.infrastructure.llm.spend_book import SpendBook
from promptpotter.infrastructure.llm.telemetry import emit_error_record
from promptpotter.infrastructure.runtime_flags import (
    clear_run_control_flags,
    read_run_limits_mirror,
    read_sample_lookahead,
    spend_sample_lookahead,
    write_run_limits_mirror,
)
from promptpotter.infrastructure.store.campaign_store.ledger_scan import scan_ledger_wall_clock
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import ResumeDivergenceError
from promptpotter.shared.hashing import dataset_hash

logger = logging.getLogger(__name__)

# Cap on auto-rebases per CLI invocation, so L2/L3 emitting `fork_proposal` every fire cannot
# spiral. PER LEVEL, not per run: an inner campaign gets its own budget, multiplying the
# wall-time envelope `OUTER_SAMPLE_WALL_S_PER_ROUND` bounds — size either one by reading both.
MAX_AUTO_REBASES = 10


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


def _build_budget_gate(
    observers: RunObservers,
    cycle_dir: Path,
    *,
    usd_cap: float | None,
    token_cap: int | None,
) -> BudgetGate:
    """**Always armed**, because a run's ceiling is not settled at launch: the probes re-read
    ``.runtime/run_limits.json`` each tick, so ``change-run-limits`` can bind a run that declared
    nothing. Returning no gate for a launch with no starting caps is what let that command ack
    ``applied`` against a ceiling that could never trip — set by the operator, served to the
    webapp, enforced by nothing. An unset arm still costs nothing: the book skips a ``None`` cap.
    The book it arms is what admits every call the run sends."""
    dashboard = observers.dashboard

    def _usd_cap() -> float | None:
        saved = read_run_limits_mirror(cycle_dir).usd
        return saved if saved is not None else usd_cap

    def _token_cap() -> int | None:
        saved = read_run_limits_mirror(cycle_dir).tokens
        return saved if saved is not None else token_cap

    # Seeded from the rollup the resume folded, then fed by the ledger itself.
    book = SpendBook(
        usd_cap=_usd_cap,
        tokens_cap=_token_cap,
        usd_spent=dashboard.spend_total_used_usd,
        tokens_spent=dashboard.spend_total_tokens,
    )
    observers.arm_spend_book(book)
    return BudgetGate(book=book)


def _arm_run_controls(
    session: Session,
    observers: RunObservers,
    campaign_config: CampaignConfig,
) -> BudgetGate:
    """The one place the ceiling and the pause/budget flags are ARMED, over the cycle dir the run is
    actually in — re-called per rebase/fork, which mints a different one. Both gate cadences (round
    boundary, and the per-sample checkpoint `query_loop` no-ops on a `None`) ride one gate, so a
    ceiling moved mid-flight moves both. Must precede round 0, which spends before any round loop.
    """
    # `Path()` where no cycle exists yet keeps the pollers total rather than optional.
    cycle_dir = session.store.campaigns.cycle_dir(session.hop) if session.state.cycle_id else Path()
    _bind_run_controls(session, cycle_dir)
    session.flight = FlightGauge(observers.callbacks.on_flight)
    # A provider's pushback moves inside a cell, where the phase's loop may be blocked on the
    # cells out — so the backpressure itself says when the reading moved.
    session.backend_client.backpressure.on_change = session.flight.touch
    gate = _build_budget_gate(
        observers,
        cycle_dir,
        usd_cap=campaign_config.optimization.spend_budget_usd,
        token_cap=campaign_config.optimization.token_budget,
    )
    session.budget_tripped = gate.tripped
    session.spend_used = lambda: gate.book.usd_spent
    return gate


def _apply_config_overrides(
    config: CampaignConfig,
    overrides: ConfigOverrides,
) -> CampaignConfig:
    """Snapshot the fork's effective config — parent frozen config plus absolute overrides, never a
    mutation. Reassigning it at the runner seam is what propagates to every reader."""
    # No budget arm here: a seed's budget is one layer of the run's declaration, composed and
    # ADMITTED before launch (`jobs/quota.py::declare_run_ceiling`). Applied here too it
    # would be a second composition — and an auto-rebase's overrides would move a ceiling the
    # account never admitted.
    opt_updates: dict[str, Any] = {
        k: v
        for k, v in {
            "max_rounds": overrides.max_rounds,
            "l1_patience": overrides.l1_patience,
            "l2_patience": overrides.l2_patience,
            "l3_patience": overrides.l3_patience,
            "pobb_epsilon": overrides.pobb_epsilon,
            "schema_field_rename": overrides.schema_field_rename,
        }.items()
        if v is not None
    }
    sel_updates = {
        k: v
        for k, v in {
            "per_round_resubset": overrides.per_round_resubset,
        }.items()
        if v is not None
    }
    # `scoring` sits on CampaignConfig itself, not under `optimization` — the one override whose
    # home is the outer model, so it rides its own bucket rather than being folded into a nested
    # copy that would silently drop it.
    top_updates: dict[str, Any] = {"scoring": overrides.scoring} if overrides.scoring else {}
    if not opt_updates and not sel_updates and not top_updates:
        return config
    if sel_updates:
        mech = config.optimization.mechanisms
        opt_updates["mechanisms"] = mech.model_copy(
            update={"selection": mech.selection.model_copy(update=sel_updates)}
        )
    if opt_updates:
        top_updates["optimization"] = config.optimization.model_copy(update=opt_updates)
    return config.model_copy(update=top_updates)


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
    scoring_spec: ScoringSpec
    halt_at_accuracy: float | None


def _bind_run_controls(session: Session, cycle_dir: Path) -> None:
    """The ONE binding seam for run control, called per cycle the run touches. It binds BEFORE origin
    scoring — the longest interruptible phase — or the operator's only way out is killing the process."""
    if not session.state.cycle_id:
        return
    layout = CycleLayout(cycle_dir)
    skip_flag = layout.skip_flag
    own_pause = layout.pause_flag.is_file
    # An inner cycle is an INSTRUMENT of its spawner and must not outlive a stop request on it.
    # It gets its own sandbox dir, whose pause flag nobody writes, so alone it would run to
    # completion while the outer sat "pausing" — COMPOSE the inherited predicate, never
    # overwrite it. Top-level cycles inherit nothing, so this is exactly `own_pause` for them.
    # The parent comes off the SESSION, never `get_abort_check()`: this runs again per rebase
    # in the SAME task, so the ContextVar holds the predicate this task bound last time round —
    # composing against that chains one per rebase, keeping retired forks' flags live in it.
    inherited = session.inherited_pause_check
    if inherited is None:
        session.pause_check = own_pause
    else:
        # Bound to a local: a closure captures the NAME, so the narrowing above does not reach
        # inside the lambda and the composed predicate would read as possibly-``None``.
        parent_abort = inherited
        session.pause_check = lambda: own_pause() or parent_abort()
    # Also bound into the ContextVar the rate-limit countdown polls — the one blocking seam
    # that otherwise ignores the pause channel.
    set_abort_check(session.pause_check)
    # One-shot: the loop deletes the flag the instant it fires, so exactly one searchpoint is cut.
    session.skip_check = skip_flag.is_file
    session.skip_consume = partial(skip_flag.unlink, missing_ok=True)
    # Deliberately NOT composed with the inherited predicate the way `pause_check` is: a stop
    # must reach the instrument, but inheriting a THROUGHPUT setting would let one arming
    # multiply concurrency at every nested level at once.
    session.sample_lookahead_check = partial(read_sample_lookahead, cycle_dir)
    session.sample_lookahead_consume = partial(spend_sample_lookahead, cycle_dir)


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
        campaign_config = _apply_config_overrides(campaign_config, seed.config_overrides)
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

    # LAST, after the seed's other knobs: the held ceiling is the one number the reservation, this
    # config and the dashboard all carry, composed and admitted before launch.
    campaign_config = _set_held_ceiling(campaign_config, limits.ceiling)
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
        if standing.ceiling != limits.operator:
            campaigns.write_run_limits(session.hop, limits.operator, rounds=rounds)
        else:
            write_run_limits_mirror(launch_cycle_dir, limits.operator, rounds=rounds)

    # After the mirror, so the gate's probes read the same ceiling the config carries, and
    # before the origin pass, which spends without one otherwise.
    _arm_run_controls(session, observers, campaign_config)

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
    if observers.display is not None and hasattr(observers.display, "set_origin"):
        observers.display.set_origin(origin.report.accuracy)

    return _PreparedRun(
        origin=origin,
        campaign_config=campaign_config,
        scoring_spec=split_scoring_block(campaign_config.scoring),
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
) -> CycleResult:
    """Assemble the terminal :class:`CycleResult`; ``cycle is None`` is the init-crash fallback, and
    ``origin is None`` a stop inside origin scoring. Both ``winner_*`` read ``best_sp``, since
    ``cycle.opt_sp`` is overwritten every round."""
    best_sp = cycle.tracking.best_sp if cycle is not None else None
    # Round 0 is the reference the whole result is differenced against, carried beside it as
    # ``origin_accuracy`` / ``origin_level``. Counting it as a search result would credit the
    # outer loop with the floor it started from.
    cycle_rounds = [rr for rr in cycle.rounds if rr.round > 0] if cycle is not None else []
    ds = cycle.ruler if cycle is not None else None
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
        n_l1_rounds=len(cycle_rounds),
        best_accuracy=cycle.tracking.best_accuracy if cycle is not None else None,
        best_round=cycle.tracking.best_round if cycle is not None else 0,
        origin_accuracy=origin.report.accuracy if origin is not None else None,
        origin_composite_fitness=(
            cycle.origin_round.composite_fitness if cycle is not None else None
        ),
        origin_level=origin_lv[0] if origin_lv is not None else None,
        origin_level_se=origin_lv[1] if origin_lv is not None else None,
        round_parent_levels=[t for t, _ in levels],
        round_parent_level_ses=[se for _, se in levels],
        # An unlimited `max_rounds` declares no budget, which is what this field's 0 means.
        round_budget=(cycle.config.optimization.max_rounds or 0) if cycle is not None else 0,
        winner_prompt_fields=best_sp.prompt_fields if best_sp else {},
        winner_pipeline_params=best_sp.pipeline_params if best_sp else None,
        stop_reason=stop_reason,
        started_at=started_at,
        finished_at=finished_at,
        cycle_id=session.state.cycle_id,
        session_id=session.session_id or None,
        resumed_from_round=session.state.resumed_from_round,
        spend=spend,
        error=cycle_error,
    )


def _winning_round(cycle: Cycle | None, result: CycleResult) -> RoundResult | None:
    """The round the composite high-water names — **round 0 included**, because a campaign nothing
    beat still has a winner and it is the origin. Read off ``cycle.rounds``, which holds round 0 at
    index 0, rather than ``result.rounds``, which drops it: the origin is the reference the result
    is differenced against, so counting it as a search result would credit the loop with its floor.

    It is also the round whose ``prompt_fields`` round-trip. ``CycleResult.winner_prompt_fields``
    is the wire-side projection — it has already flattened ``few_shot_examples`` into a rendered
    ``few_shot_block``, which ``from_prompt_fields`` cannot restore and ``extra="forbid"`` rejects.
    """
    if cycle is None:
        return None
    return next((rr for rr in cycle.rounds if rr.round == result.best_round), None)


def _export_artifact(
    session: Session,
    cycle_result: CycleResult,
    winner: RoundResult | None,
    *,
    formula: str | None,
) -> PromptExport | None:
    """``None`` when no round ever closed: there is no measured prompt to hand a consumer, and an
    artifact whose whole point is a fitness with provenance may not carry an unmeasured one."""
    if winner is None:
        return None
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
        optimizer_prompt_hash=campaign.optimizer_prompt_hash if campaign else "",
        stop_reason=str(cycle_result.stop_reason),
        finished_at=cycle_result.finished_at,
        formula=formula,
        origin_accuracy=cycle_result.origin_accuracy,
        origin_composite_fitness=cycle_result.origin_composite_fitness,
    )


def _close_cycle(
    cycle: Cycle | None,
    origin: CampaignOrigin | None,
    session: Session,
    observers: RunObservers,
    *,
    stop_reason: StopReason,
    cycle_error: ErrorRecord | None,
    started_at: str,
    accuracy_ceiling: float | None,
    diag: bool,
) -> CycleResult:
    """The one terminal path, for a stop raised in the round loop and one raised in run init."""
    finished_at = utcnow_iso()
    # Before the result is built: a decision made after the last round closed has no next
    # `persist_round` to carry it, and every stop reason lands here.
    if cycle is not None:
        flush_pending_decisions(cycle, session)
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
    )
    langfuse_trace_id = _finalize_run(
        session,
        observers,
        cycle_result,
        accuracy_ceiling=accuracy_ceiling,
        winner=_winning_round(cycle, cycle_result),
        diag=diag,
    )
    if langfuse_trace_id is not None:
        cycle_result = cycle_result.model_copy(update={"langfuse_trace_id": langfuse_trace_id})
    return cycle_result


@dataclass
class _CycleOutcome:
    """One cycle run to completion. The observers may have been REBUILT mid-run by fork-on-divergence,
    so the driver keeps this live reference rather than the one it passed in."""

    cycle_result: CycleResult
    cycle: Cycle | None
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

    cycle: Cycle | None = None
    cancel_exc: asyncio.CancelledError | None = None
    try:
        cycle = await init_optimization_loop(
            origin,
            dataset,
            campaign_config,
            cb=cb,
            scoring_formula=prep.scoring_spec.per_sample,
            scoring_cell_formula=prep.scoring_spec.per_cell,
            scorer_id=prep.scoring_spec.scorer_id,
            no_divergence_check=mode.no_divergence_check,
            fork_on_divergence=mode.fork_on_divergence,
            langfuse_session_id=langfuse_session_id,
            cycle_id=session.state.cycle_id or None,
            resume_from_round_override=mode.resume_from_round_override,
            session=session,
            started_at=started_at,
        )

        # Fork-on-divergence: rebuild observers around the fork's own ledger.
        forked = (
            pre_loop_cycle_id
            and session.state.cycle_id
            and pre_loop_cycle_id != session.state.cycle_id
        )
        if forked and pre_loop_cycle_id:
            # Carry phase_ctx across the rebuild: INIT.enter fired on the parent callbacks and
            # will not re-fire, so without it RoundStartView reads zeros on every forked round.
            parent_phase_ctx = observers.callbacks._phase_ctx
            observers = build_run_observers(
                session=session,
                campaign_config=campaign_config,
                dataset=dataset,
                display=observers.display,
                resumed_from_round=session.state.resumed_from_round,
                origin_accuracy=origin.report.accuracy,
                fork=ForkInfo(parent_cycle_id=pre_loop_cycle_id),
            )
            observers.callbacks._phase_ctx = parent_phase_ctx
            cb = observers.callbacks

        # The gate probes go through the dashboard, which already owns the spend rollup, rather
        # than a parallel reader; `observers` is bound in the builder so the rebase loop's
        # rebuild cannot leave it on a stale ref.
        budget_gate = _arm_run_controls(session, observers, campaign_config)
        stop_reason, cycle_error = await run_round_loop(
            cycle,
            dataset,
            campaign_config,
            session,
            cb,
            diag=mode.diag,
            halt_at_accuracy=prep.halt_at_accuracy,
            stop_after_rounds=mode.stop_after_rounds,
            budget_gate=budget_gate,
        )
    except RUN_STOPS as stop:
        stop_reason = run_stop_reason(stop)
        cycle_error = None
    except KeyboardInterrupt:
        logger.warning("Optimization paused before round loop entered (user-initiated).")
        stop_reason = StopReason.PAUSED
        cycle_error = None
    except asyncio.CancelledError as exc:
        # Where a terminal Ctrl+C lands (`asyncio.Runner` cancels the main task first) and
        # where an inner campaign cancelled by its outer sample deadline lands. It still
        # finalizes — the cycle's state must reach disk exactly as a pause does — but it must
        # ALSO reach the canceller, so it is re-raised past the finalize below: answering a
        # cancellation with a return is what made the L4 sample deadline unenforceable.
        cancel_exc = exc
        logger.warning(
            "Optimization cancelled (%s); finalizing as paused (resumable).",
            session.state.cycle_id or "no cycle",
        )
        stop_reason = StopReason.PAUSED
        cycle_error = None
    except ResumeDivergenceError as exc:
        # Operator-recoverable; fix is ``--fork-on-divergence``.
        message = str(exc) or type(exc).__name__
        kind = type(exc).__name__
        logger.warning("Resume halted on divergence:\n%s", exc)
        stop_reason = StopReason.DIVERGED
        cycle_error = emit_error_record(kind=kind, message=message, stop_reason="DIVERGED")
    except Exception as exc:
        tb = traceback.format_exc()
        session.state.crash_traceback = tb
        message = str(exc) or type(exc).__name__
        kind = type(exc).__name__
        logger.exception("Optimization crashed before round loop entered.")
        stop_reason = StopReason.CRASHED
        cycle_error = emit_error_record(
            kind=kind, message=message, stop_reason="CRASHED", traceback=tb
        )

    cycle_result = _close_cycle(
        cycle,
        origin,
        session,
        observers,
        stop_reason=stop_reason,
        cycle_error=cycle_error,
        started_at=started_at,
        accuracy_ceiling=campaign_config.accuracy_ceiling,
        diag=mode.diag,
    )
    # A fork that never completed a round leaves an empty dir. Ahead of the re-raise below,
    # because a cancellation is one of the interrupts that produces one.
    forked_in_this_run = (
        pre_loop_cycle_id and session.state.cycle_id and pre_loop_cycle_id != session.state.cycle_id
    )
    if forked_in_this_run and cycle_result.n_l1_rounds == 0:
        cleanup_stub_fork_if_empty(
            campaign_store=session.store.campaigns,
            hop=session.hop,
            parent_cycle_id=pre_loop_cycle_id,
        )

    if cancel_exc is not None:
        # The caught instance, not a fresh class — it carries the reason its raise site named.
        raise cancel_exc

    return _CycleOutcome(cycle_result=cycle_result, cycle=cycle, observers=observers)


def _mint_and_rebase_fork(
    prep: _PreparedRun,
    *,
    session: Session,
    observers: RunObservers,
    dataset: list[Sample],
    rebase_req: RebaseRequest,
    rebase_count: int,
) -> tuple[_PreparedRun, RunObservers]:
    """Mint the auto-rebase fork and rebuild observers around the new cycle's ledger. A rebase
    carrying ``config_overrides`` re-snapshots BEFORE the mint, so seed and in-process config agree."""
    parent_cycle_id = session.state.cycle_id
    seed: CycleSeed | None = None
    if rebase_req.config_overrides is not None:
        campaign_config = _apply_config_overrides(prep.campaign_config, rebase_req.config_overrides)
        prep = replace(prep, campaign_config=campaign_config)
        # No `origin_prompt_fields`: a rebase replays its origin from the parent's round, so it
        # has no C0 provenance to stamp.
        seed = CycleSeed(config_overrides=rebase_req.config_overrides)
    new_cycle_id = _mint_fork(
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
    parent_phase_ctx = observers.callbacks._phase_ctx
    observers = build_run_observers(
        session=session,
        campaign_config=prep.campaign_config,
        dataset=dataset,
        display=observers.display,
        resumed_from_round=rebase_req.fork_from_round,
        origin_accuracy=prep.origin.report.accuracy,
        fork=ForkInfo(parent_cycle_id=parent_cycle_id),
    )
    observers.callbacks._phase_ctx = parent_phase_ctx
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
    before origin)."""
    started_at = utcnow_iso()
    # Every launch path reaches here; bolted onto one entry point instead, it leaves the others
    # pricing off whatever table shipped. No-op on a fresh cache.
    refresh_rates_in_background()
    # Unconditional (the runner cannot know a child will recurse) and re-entrant (each level
    # publishes its own); a no-op until the cycle_id is set.
    publish_inner_spawn_context(session, campaign_config)
    # Round 0's cells need it too: their origin level is the baseline every candidate is
    # differenced against.
    refresh_inner_rulers(session, campaign_config, round_num=0)
    # Read here, before anything binds, so it is still a parent's and not our own.
    session.inherited_pause_check = get_abort_check()
    # Bound through the same per-node override channel the inner runner uses — task-isolated,
    # so an outer binding and the inner mutations of the cycles it spawns never collide. An
    # empty set is a no-op and must NOT clear an inner runner's already-bound mutations.
    if campaign_config.optimization.optimizer_set:
        set_optimizer_prompt_overrides(
            load_optimizer_set_overrides(campaign_config.optimization.optimizer_set)
        )
    # UNCONDITIONAL, unlike the set above: this task may be an inner cell carrying the outer
    # campaign's pin in its context copy, and a cell measures under its own panel's clamp or none.
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
        # Origin scoring stops on the round loop's channel — the budget gate, an unreachable
        # backend, a spent provider account — so it ends on the round loop's path, never a crash.
        return _close_cycle(
            None,
            None,
            session,
            observers,
            stop_reason=run_stop_reason(stop),
            cycle_error=None,
            started_at=started_at,
            accuracy_ceiling=campaign_config.accuracy_ceiling,
            diag=mode.diag,
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

        rebase_req = outcome.cycle.rebase_request if outcome.cycle is not None else None
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
            dataset=dataset,
            rebase_req=rebase_req,
            rebase_count=rebase_count,
        )


def _finalize_run(
    session: Session,
    observers: RunObservers,
    cycle_result: CycleResult,
    *,
    accuracy_ceiling: float | None,
    winner: RoundResult | None = None,
    diag: bool,
) -> str | None:
    """Returns the Langfuse trace id from the terminal ``end_campaign`` emit (``None`` when
    no tracing bridge is active) so the caller can stamp it onto the returned ``CycleResult``.
    """
    stop_reason = cycle_result.stop_reason
    # Read off the canonical table, never re-derived here: a private or-chain has no
    # exhaustiveness check, and silently missed the two reasons the budget gate raises from
    # INSIDE the scoring phase, writing a partial round with no `interrupted` marker.
    info = STOP_REASON_INFO[StopReason(stop_reason)]
    is_paused = info.outcome is StopOutcome.PAUSED
    halted_mid_round = info.halts_mid_round
    has_traceback = info.has_traceback
    if is_paused:
        # DECLARE the pause at the one point every paused exit converges on, rather than
        # trusting each raise site. Skipping the terminal writes below leaves `derive_run_phase`
        # only two ways to read `paused` — the flag or a declaration — and Ctrl+C inside the
        # round loop sets neither, so it falls through to freshness, returns DETACHED, and the
        # reaper stamps `producer_vanished` on a cycle its owner deliberately cancelled.
        # Idempotent at the projection, so a checkpoint that already declared it pays nothing.
        declare_run_phase(session, RunPhase.PAUSED)
    # A pause leaves the cycle ACTIVE and resumable, so every terminal-marking write is skipped
    # and `index.json` keeps no `finished_at` for `derive_run_phase` to read past the PAUSED
    # just declared. The partial round is still drained below.
    if session.state.cycle_id and not is_paused:
        interrupted_round = int(observers.callbacks._current_round) if halted_mid_round else None
        # The active exception is gone from `sys.exc_info()` by now; the except clause stashed
        # the formatted traceback before returning.
        crash_traceback = session.state.crash_traceback if has_traceback else None
        # The precise terminal reason, with no lossy collapse to "completed" — the
        # operator-facing label and outcome derive from STOP_REASON_INFO, never per surface.
        cycle_status = str(stop_reason)

        rounds = cycle_result.rounds
        round_formula = resolve_cell_formula(
            session.scoring.scorer_cell_formula, session.pipeline_schema
        )[0]
        # The run's own two endpoints, which `output.py::from_disk_log` reads off THIS block to
        # build the digest's status view — the campaign index carries no other copy of `started_at`.
        wall_clock = scan_ledger_wall_clock(
            CycleLayout(session.store.campaigns.cycle_dir(session.hop)).ledger,
            started_at=cycle_result.started_at,
            finished_at=cycle_result.finished_at,
        )
        final_block: dict[str, Any] = {
            "stop_reason": stop_reason,
            "started_at": cycle_result.started_at,
            "finished_at": cycle_result.finished_at,
            # WHERE that span went, folded from the chronology and banked because the records it
            # is read from are compactable and no round document carries a timestamp.
            "wall_clock": wall_clock.model_dump(),
            # Spread rather than re-spelled, so the served key IS the field a reader greps for —
            # and every clock names its own question, because a bare round count on this block
            # is what gets quoted as the result. Seconds are the `wall_clock.round_ended_s` entry
            # under the same round number, never a second copy banked beside it.
            **round_clocks(rounds, accuracy_ceiling=accuracy_ceiling)._asdict(),
            "prompt_hashes": compute_optimizer_prompt_hashes(),
            # On the origin's OWN samples — never `rounds[0].matched_parent_composite`, which
            # is round 1's winner's matched floor on a different sample basis.
            "origin_composite_fitness": cycle_result.origin_composite_fitness,
            # The formula EVERY number above was computed under, resolved by the same call the
            # dashboard makes: one resolution, now four readers — the export names it too, since
            # a fitness handed to another program without its formula is a number, not a result.
            "scorer_cell_formula": round_formula,
            "mode": "diag" if diag else "full",
            # Basis: the COMPOSITE-fitness high-water SP — the engine's adoption objective —
            # which may name a different round than the index's top-level
            # `best_accuracy`/`best_round`. "How good did it get" reads those top-level fields,
            # so there is deliberately no accuracy scalar duplicated here.
            "winner_prompt_fields": cycle_result.winner_prompt_fields,
            "winner_pipeline_params": cycle_result.winner_pipeline_params,
        }
        session.store.campaigns.mark_finished(
            session.hop,
            status=cycle_status,
            stop_reason=stop_reason,
            finished_at=cycle_result.finished_at,
            interrupted_round=interrupted_round,
            crash_traceback=crash_traceback,
            final=final_block,
            export=_export_artifact(session, cycle_result, winner, formula=round_formula),
        )
        write_log_md(session)
    # Declared BEFORE the drain, so dashboard.json's stopped state is in place before the audit
    # settles. `_halted_mid_round` threads `"interrupted": true` into a partial round file. The
    # append reaches the projection through the same door every other fact does — it is a
    # subscriber — so there is no second path to keep in step with this one.
    if not is_paused:
        declare_run_phase(session, RunPhase.TERMINAL, stop_reason=str(stop_reason or ""))
    observers.audit._halted_mid_round = halted_mid_round
    observers.drain_all()

    obs = session.state.obs
    langfuse_trace_id: str | None = None
    if obs:
        langfuse_trace_id = obs.end_campaign(
            session.state.tracing_campaign_id,
            best_accuracy=cycle_result.best_accuracy,
            n_l1_rounds=cycle_result.n_l1_rounds,
            stop_reason=stop_reason,
            best_round=cycle_result.best_round,
        )
    return langfuse_trace_id


__all__ = ["RunMode", "run_optimization"]
