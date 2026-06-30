"""``run_optimization`` — optimize-loop entry + teardown.

Wires origin → ``init_optimization_loop`` → ``run_round_loop`` → ``_finalize_run``.
Operator forks stamp L1-surface deltas on the fresh OSP; fork-on-divergence
rebuilds observers + re-seeds ``phase_ctx`` so RoundStartView keeps reading
parent's max_rounds + patience scalars."""

from __future__ import annotations

import asyncio
import logging
import traceback
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

from promptpotter.application.bootstrap import init_optimization_loop
from promptpotter.application.bootstrap.session import Session
from promptpotter.application.config import CampaignConfig
from promptpotter.application.optimization.cycle import Cycle
from promptpotter.application.optimization.escalation import apply_fork_payload_to_osp
from promptpotter.application.optimization.resume_and_fork.fork_siblings import (
    _mint_fork,
    cleanup_stub_fork_if_empty,
)
from promptpotter.application.origin import (
    CampaignOrigin,
    establish_campaign_origin,
)
from promptpotter.application.run_observers import (
    ForkInfo,
    RunObservers,
    build_run_observers,
)
from promptpotter.application.runner.inner_recursion import publish_inner_spawn_context
from promptpotter.application.runner.loop import run_round_loop
from promptpotter.application.runner.termination import BudgetGate
from promptpotter.application.scoring.formula import split_scoring_block
from promptpotter.domain.phases import StopReason
from promptpotter.domain.results import CycleError, CycleResult
from promptpotter.domain.run_records import CycleSeed, ForkSpec, LimitOverrides
from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import ScoringSpec
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.infrastructure.llm import set_abort_check
from promptpotter.infrastructure.llm.models import emit_error_record
from promptpotter.infrastructure.runtime_flags import clear_run_control_flags
from promptpotter.infrastructure.store.base import read_json_tolerant
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import ResumeDivergenceError

logger = logging.getLogger(__name__)

# Cap on auto-rebases per CLI invocation. L2/L3 emitting fork_proposal
# every fire would otherwise spiral; after this many in-process rebases
# we exit with the last cycle's stop_reason and let the operator
# re-invoke ``resume`` if they want to keep going.
MAX_AUTO_REBASES = 10


@dataclass(frozen=True)
class RunMode:
    """The launch-shape flags that select a run's behaviour, grouped so the
    runner seam and its callers pass one value instead of six loose booleans.

    Everything else ``run_optimization`` takes is run *content* (dataset,
    config, session, observers, origin, task_context) — these are the *mode*
    knobs the CLI/API flags map onto: divergence handling, fork-on-divergence,
    sweep vs full, diagnostic mode, the accuracy halt, and a resume offset.
    """

    no_divergence_check: bool = False
    fork_on_divergence: bool = False
    sweep: bool = False
    diag: bool = False
    halt_at_accuracy: float | None = None
    resume_from_round_override: int | None = None


def _build_budget_gate(
    observers: RunObservers,
    cycle_dir: Path,
    *,
    usd_cap: float | None,
    token_cap: int | None,
) -> BudgetGate | None:
    """Assemble the cycle's :class:`BudgetGate` from the two starting ceilings.

    A ceiling is armed iff its starting cap is non-``None``; if both are
    disarmed the gate itself is ``None`` (no budget halt). ``observers`` is
    bound here so the rebase loop's per-iteration observer rebuild can't leave
    the spent-probes reading a stale reference. The cap probes re-read
    ``.runtime/spend_cap.json`` (``{max_usd, max_tokens}``, written by the
    ``change-spend-budget`` applier) each tick, falling back to the starting
    cap when the file is absent / malformed — so a ceiling can be moved
    mid-flight without restarting the loop."""
    if usd_cap is None and token_cap is None:
        return None
    dashboard = observers.dashboard
    cap_path = cycle_dir / ".runtime" / "spend_cap.json"

    def _saved_caps() -> dict[str, Any]:
        data = read_json_tolerant(cap_path, {})
        return data if isinstance(data, dict) else {}

    def _usd_cap() -> float | None:
        value = _saved_caps().get("max_usd")
        return float(value) if isinstance(value, int | float) else usd_cap

    def _token_cap() -> int | None:
        value = _saved_caps().get("max_tokens")
        return int(value) if isinstance(value, int) else token_cap

    return BudgetGate(
        usd_spent=(lambda: dashboard.spend_total_used_usd) if usd_cap is not None else None,
        usd_cap=_usd_cap if usd_cap is not None else None,
        tokens_spent=(lambda: dashboard.spend_total_tokens) if token_cap is not None else None,
        tokens_cap=_token_cap if token_cap is not None else None,
    )


def _apply_limit_overrides(
    config: CampaignConfig,
    spend_budget_usd: float | None,
    limits: LimitOverrides,
) -> tuple[CampaignConfig, float | None]:
    """Snapshot the fork's effective `OptimizationConfig`: parent frozen config
    with the operator's reconciled overrides applied (absolute values; an absent
    knob inherits the parent). A new-cycle snapshot only — the parent config is
    never mutated. Reassigning the returned config at the runner seam propagates
    to every reader (loop ``max_rounds`` / patience, L1 ``pobb_epsilon``, the
    per-round subset selection, the ``INIT.enter`` display event). The reconciled
    ``spend_budget_usd`` also becomes the spend-cap probe's initial ceiling
    (``change-spend-budget`` can still move it at runtime). Selection-policy
    overrides (``per_round_resubset`` / ``online_reorder``) ride the nested
    ``mechanisms.selection`` so a fork can A/B a behaviour knob in isolation."""
    opt_updates: dict[str, Any] = {
        k: v
        for k, v in {
            "max_rounds": limits.max_rounds,
            "spend_budget_usd": limits.spend_budget_usd,
            "token_budget": limits.token_budget,
            "l1_patience": limits.l1_patience,
            "l2_patience": limits.l2_patience,
            "l3_patience": limits.l3_patience,
            "pobb_epsilon": limits.pobb_epsilon,
        }.items()
        if v is not None
    }
    sel_updates = {
        k: v
        for k, v in {
            "per_round_resubset": limits.per_round_resubset,
            "online_reorder": limits.online_reorder,
        }.items()
        if v is not None
    }
    if not opt_updates and not sel_updates:
        return config, spend_budget_usd
    if sel_updates:
        mech = config.optimization.mechanisms
        opt_updates["mechanisms"] = mech.model_copy(
            update={"selection": mech.selection.model_copy(update=sel_updates)}
        )
    new_config = config.model_copy(
        update={"optimization": config.optimization.model_copy(update=opt_updates)}
    )
    effective_spend = (
        limits.spend_budget_usd if limits.spend_budget_usd is not None else spend_budget_usd
    )
    return new_config, effective_spend


def _read_cycle_seed(session: Session) -> CycleSeed | None:
    """Read this cycle's declared-at-mint seed, or ``None`` for an unseeded run.

    The cycle_id is known (active-pointer / override, not hashed) and set on
    ``session.state`` before the runner seam, so the lookup is non-circular with
    cycle-id derivation. Set for an operator-steered fork or a campaign-from-origin
    root mint; ``None`` otherwise."""
    if not session.state.cycle_id:
        return None
    return session.store.campaigns.read_cycle_seed(session.campaign_id, session.state.cycle_id)


@dataclass(frozen=True)
class _PreparedRun:
    """Resolved run inputs from :func:`_prepare_run` — the straight-line prep
    (seed reconciliation + C0 origin + scoring/task_context resolution) done
    once before the rebase loop. ``campaign_config`` / ``spend_budget_usd`` are
    re-emitted because a cycle seed may reconcile new run limits onto them."""

    origin: CampaignOrigin
    campaign_config: CampaignConfig
    spend_budget_usd: float | None
    scoring_spec: ScoringSpec
    task_context: TaskDecomposition


async def _prepare_run(
    dataset: list[Sample],
    campaign_config: CampaignConfig,
    *,
    session: Session,
    observers: RunObservers,
    origin: CampaignOrigin | None,
    task_context: TaskDecomposition | dict[str, Any] | None,
    spend_budget_usd: float | None,
) -> _PreparedRun:
    """Resolve seed overlay + run limits, establish C0, and normalize scoring +
    task_context — the once-per-launch prep ahead of the rebase loop."""
    cb = observers.callbacks

    # A fresh launch supersedes any prior run-control intent: drop a consumed
    # pause flag left on this cycle's `.runtime/` by an earlier paused run, else
    # a stale pause.flag pauses this very resume on its first poll (a paused
    # cycle could never be resumed). Once, here at the seam every launch funnels
    # through — before the pause_check hook binds in the rebase loop below.
    if session.state.cycle_id:
        clear_run_control_flags(
            session.store.campaigns.cycle_dir(session.campaign_id, session.state.cycle_id)
            / ".runtime"
        )

    # Cycle seed: the chosen searchpoint, declared at mint, lives at
    # `.overrides/seed.json` (read-once-at-bootstrap, keyed by the known cycle_id —
    # set before this seam by the CLI resume, the API start-run launchers, and the
    # campaign-from-origin mint). It re-homes the origin (`origin_prompt_fields`)
    # and layers its `pipeline_overlay` ON TOP of the dataset overlay
    # (seed > dataset > backend). Read here — the single runner seam every launch
    # path funnels through — not threaded through each launcher + every
    # `configure_and_apply_pipeline` caller.
    seed = _read_cycle_seed(session)
    if seed is not None and seed.pipeline_overlay:
        merged = dict(session.pipeline_params or {})
        for node, cfg in seed.pipeline_overlay.items():
            base = merged.get(node)
            if isinstance(base, dict) and isinstance(cfg, dict):
                merged[node] = {**base, **cfg}
            else:
                # A non-dict value (e.g. the reserved top-level ``steps`` list)
                # can't be spread — assign it directly rather than crash the
                # per-node merge with a TypeError.
                merged[node] = cfg
        session.pipeline_params = merged
    if seed is not None:
        # Reconcile the seed's run limits (rounds / spend / patience / epsilon)
        # onto a fresh config snapshot — the loop starts at round 1 and stops
        # after the reconciled max_rounds. Reassign before any downstream call.
        campaign_config, spend_budget_usd = _apply_limit_overrides(
            campaign_config, spend_budget_usd, seed.limit_overrides
        )

    if origin is None:
        # Establish C0 through the single origin seam: a no-edit operator fork inherits
        # its branch-point candidate's recorded accuracy (skipping the re-score, which
        # would re-roll under a nondeterministic backend); everything else scores it.
        origin = await establish_campaign_origin(
            session, dataset, campaign_config, seed=seed, listener=cb
        )
        if observers.display is not None and hasattr(observers.display, "set_origin"):
            observers.display.set_origin(origin.origin_acc)

    if isinstance(task_context, TaskDecomposition):
        resolved_task_context = task_context
    elif isinstance(task_context, dict):
        resolved_task_context = TaskDecomposition.from_dict(task_context)
    else:
        resolved_task_context = TaskDecomposition()

    return _PreparedRun(
        origin=origin,
        campaign_config=campaign_config,
        spend_budget_usd=spend_budget_usd,
        scoring_spec=split_scoring_block(campaign_config.scoring),
        task_context=resolved_task_context,
    )


def _build_cycle_result(
    cycle: Cycle | None,
    origin: CampaignOrigin,
    session: Session,
    *,
    stop_reason: StopReason,
    cycle_error: CycleError | None,
    started_at: str,
    finished_at: str,
) -> CycleResult:
    """Assemble the terminal :class:`CycleResult`. ``cycle is None`` is the
    init-crash fallback (cycle_id was minted upstream so the run still finalizes);
    the cycle-derived fields then read their empty defaults.

    Both ``winner_*`` read from ``best_sp`` (the BEST round's frozen point) so the
    prompt fields and pipeline_params describe the SAME round — ``cycle.opt_sp`` is
    overwritten each round by absorb_round, so reading prompts off it would pair the
    best params with the last round's text."""
    best_sp = cycle.tracking.best_sp if cycle is not None else None
    return CycleResult(
        rounds=cycle.rounds if cycle is not None else [],
        n_rounds=len(cycle.rounds) if cycle is not None else 0,
        best_accuracy=cycle.tracking.best_accuracy if cycle is not None else 0.0,
        best_round=cycle.tracking.best_round if cycle is not None else 0,
        origin_accuracy=origin.origin_acc,
        winner_prompt_fields=(best_sp.prompt_fields or {}) if best_sp else {},
        winner_pipeline_params=best_sp.pipeline_params if best_sp else None,
        stop_reason=stop_reason,
        started_at=started_at,
        finished_at=finished_at,
        cycle_id=session.state.cycle_id,
        session_id=session.session_id or None,
        resumed_from_round=session.state.resumed_from_round,
        error=cycle_error,
    )


async def run_optimization(
    dataset: list[Sample],
    campaign_config: CampaignConfig,
    *,
    session: Session,
    observers: RunObservers,
    origin: CampaignOrigin | None = None,
    experiment_id: str | None = None,
    task_context: TaskDecomposition | dict[str, Any] | None = None,
    langfuse_session_id: str | None = None,
    mode: RunMode | None = None,
    fork_payload: ForkSpec | None = None,
    spend_budget_usd: float | None = None,
) -> CycleResult:
    """End-to-end optimization. *observers* MUST be pre-built (ledger bound before origin).
    *origin* omitted ⇒ scored as phase 0 (CLI); supplied ⇒ reused (notebook path)."""
    mode = mode or RunMode()
    started_at = utcnow_iso()
    # Publish this cycle as the spawn context for any L4 recursion: a child that
    # uses the ``promptpotter`` connector reads it to root its sandbox at THIS
    # cycle's ``.runtime/inner/`` + find the inner benchmark. Unconditional (the
    # runner can't know a child will recurse) + re-entrant (each level publishes
    # its own); a no-op until the cycle_id is set.
    publish_inner_spawn_context(session)
    prep = await _prepare_run(
        dataset,
        campaign_config,
        session=session,
        observers=observers,
        origin=origin,
        task_context=task_context,
        spend_budget_usd=spend_budget_usd,
    )
    origin = prep.origin
    campaign_config = prep.campaign_config
    spend_budget_usd = prep.spend_budget_usd
    scoring_spec = prep.scoring_spec
    resolved_task_context = prep.task_context
    cb = observers.callbacks

    rebase_count = 0
    while True:
        pre_loop_cycle_id = session.state.cycle_id

        # Outer try/except: init-phase crashes (stale OSP rejected by extra="forbid", etc.) land in CRASHED with stashed traceback.
        cycle: Cycle | None = None
        try:
            cycle = await init_optimization_loop(
                origin,
                dataset,
                campaign_config,
                cb=cb,
                task_context=resolved_task_context,
                scoring_formula=scoring_spec.per_sample,
                scoring_round_formula=scoring_spec.per_round,
                scorer_id=scoring_spec.scorer_id,
                no_divergence_check=mode.no_divergence_check,
                fork_on_divergence=mode.fork_on_divergence,
                langfuse_session_id=langfuse_session_id,
                cycle_id=session.state.cycle_id or None,
                resume_from_round_override=mode.resume_from_round_override,
                experiment_id=experiment_id or "",
                session=session,
                started_at=started_at,
            )

            # Operator forks (sweep, rebase) stamp L1-surface deltas; triggers without deltas skip.
            if fork_payload is not None and fork_payload.l1_layout is not None:
                apply_fork_payload_to_osp(cycle.opt_sp, fork_payload)

            # Fork-on-divergence: rebuild observers around the fork's own ledger.
            forked = (
                pre_loop_cycle_id
                and session.state.cycle_id
                and pre_loop_cycle_id != session.state.cycle_id
            )
            if forked and pre_loop_cycle_id:
                # Carry phase_ctx across the rebuild — INIT.enter (max_rounds, patience, formulas)
                # fired on the parent callbacks and won't re-fire (else RoundStartView reads zeros on every forked round).
                parent_phase_ctx = observers.callbacks._phase_ctx
                observers = build_run_observers(
                    session=session,
                    campaign_config=campaign_config,
                    dataset=dataset,
                    display=observers.display,
                    resumed_from_round=session.state.resumed_from_round,
                    origin_accuracy=origin.origin_acc,
                    fork=ForkInfo(parent_cycle_id=pre_loop_cycle_id),
                )
                observers.callbacks._phase_ctx = parent_phase_ctx
                cb = observers.callbacks

            # The BudgetGate's spent-probes read LiveDashboardView's clean
            # accessors (spend_total_used_usd / spend_total_tokens): the dashboard
            # is the sole owner of the spend rollup, so the gate goes through the
            # projection that already accumulates the records — not a parallel
            # reader. `observers` is bound in the builder so the rebase loop's
            # next-iteration rebuild can't leave it reading a stale ref. The cap
            # probes re-read `.runtime/spend_cap.json` each tick so the
            # `change-spend-budget` command can move a ceiling mid-flight.
            cycle_dir_for_probe = (
                session.store.campaigns.cycle_dir(session.campaign_id, session.state.cycle_id)
                if session.state.cycle_id
                else Path()
            )
            # Control-local hooks (pause) bind HERE — the single runner seam
            # every launch path funnels through — not at the entry points. The
            # CLI used to set these in new.py/resume.py, which left the API
            # launcher's runs (mint / start-run) unable to pause: the flag was
            # written but never polled. Binding per rebase/fork iteration also
            # tracks a fork's own cycle dir (the entry-point version bound once
            # and went stale across forks).
            if session.state.cycle_id:
                runtime_dir = cycle_dir_for_probe / ".runtime"
                skip_flag = runtime_dir / "skip.flag"
                session.pause_check = (runtime_dir / "pause.flag").is_file
                # Let a pause break a long rate-limit countdown mid-wait — the one
                # blocking seam that otherwise ignores the pause channel. Same
                # predicate, bound into the per-task ContextVar the wait polls.
                set_abort_check(session.pause_check)
                # Skip is one-shot: poll the flag, then the loop deletes it the
                # instant it fires so exactly one searchpoint is cut.
                session.skip_check = skip_flag.is_file
                session.skip_consume = partial(skip_flag.unlink, missing_ok=True)
            stop_reason, cycle_error = await run_round_loop(
                cycle,
                dataset,
                campaign_config,
                session,
                cb,
                sweep=mode.sweep,
                diag=mode.diag,
                halt_at_accuracy=mode.halt_at_accuracy,
                budget_gate=_build_budget_gate(
                    observers,
                    cycle_dir_for_probe,
                    usd_cap=spend_budget_usd
                    if spend_budget_usd is not None
                    else campaign_config.optimization.spend_budget_usd,
                    token_cap=campaign_config.optimization.token_budget,
                ),
            )
        except (KeyboardInterrupt, asyncio.CancelledError) as exc:
            cause = (
                "user-initiated"
                if isinstance(exc, KeyboardInterrupt)
                else "programmatic cancellation"
            )
            logger.warning("Optimization paused before round loop entered (%s).", cause)
            stop_reason = StopReason.PAUSED
            cycle_error = None
        except ResumeDivergenceError as exc:
            # Operator-recoverable; fix is ``--fork-on-divergence``.
            message = str(exc) or type(exc).__name__
            kind = type(exc).__name__
            emit_error_record(kind=kind, message=message, stop_reason="DIVERGED")
            logger.warning("Resume halted on divergence:\n%s", exc)
            stop_reason = StopReason.DIVERGED
            cycle_error = CycleError(kind=kind, message=message)
        except Exception as exc:
            tb = traceback.format_exc()
            session.state.crash_traceback = tb
            message = str(exc) or type(exc).__name__
            kind = type(exc).__name__
            emit_error_record(kind=kind, message=message, stop_reason="CRASHED", traceback=tb)
            logger.exception("Optimization crashed before round loop entered.")
            stop_reason = StopReason.CRASHED
            cycle_error = CycleError(kind=kind, message=message, traceback=tb)

        finished_at = utcnow_iso()
        cycle_result = _build_cycle_result(
            cycle,
            origin,
            session,
            stop_reason=stop_reason,
            cycle_error=cycle_error,
            started_at=started_at,
            finished_at=finished_at,
        )
        langfuse_trace_id = _finalize_run(session, observers, cycle_result, sweep=mode.sweep)
        if langfuse_trace_id is not None:
            cycle_result = cycle_result.model_copy(update={"langfuse_trace_id": langfuse_trace_id})

        # Stub-fork cleanup: if this run forked during init but never completed a round, delete the
        # empty dir so interrupts between fork-mint and round-1 don't accumulate stubs.
        forked_in_this_run = (
            pre_loop_cycle_id
            and session.state.cycle_id
            and pre_loop_cycle_id != session.state.cycle_id
        )
        if forked_in_this_run and cycle_result.n_rounds == 0:
            cleanup_stub_fork_if_empty(
                campaign_store=session.store.campaigns,
                campaign_id=session.campaign_id,
                tenant_id=session.store.tenant_id,
                session_id=session.session_id or "",
                cycle_id=session.state.cycle_id,
                parent_cycle_id=pre_loop_cycle_id,
            )

        # Auto-rebase: cycle exited with REBASED + cycle stashed a rebase request →
        # mint the fork now (old cycle is fully finalized), rebuild observers, loop.
        rebase_req = cycle.rebase_request if cycle is not None else None
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

        parent_cycle_id = session.state.cycle_id
        new_cycle_id = _mint_fork(
            campaign_store=session.store.campaigns,
            campaign_id=session.campaign_id,
            tenant_id=session.store.tenant_id,
            session_id=session.session_id or "",
            parent_cycle_id=parent_cycle_id,
            fork_from_round=rebase_req.fork_from_round,
            payload=ForkSpec(
                trigger=rebase_req.trigger,
                reason=rebase_req.reason,
                issued_by=rebase_req.issued_by,
            ),
        )
        session.state.cycle_id = new_cycle_id
        session.state.resumed_from_round = rebase_req.fork_from_round
        parent_phase_ctx = observers.callbacks._phase_ctx
        observers = build_run_observers(
            session=session,
            campaign_config=campaign_config,
            dataset=dataset,
            display=observers.display,
            resumed_from_round=rebase_req.fork_from_round,
            origin_accuracy=origin.origin_acc,
            fork=ForkInfo(parent_cycle_id=parent_cycle_id),
        )
        observers.callbacks._phase_ctx = parent_phase_ctx
        cb = observers.callbacks
        rebase_count += 1
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


def _finalize_run(
    session: Session,
    observers: RunObservers,
    cycle_result: CycleResult,
    *,
    sweep: bool = False,
) -> str | None:
    """Mark cycle finished, fold summary into index.json::final, render log.md, drain projections.

    Returns the Langfuse trace id from the terminal ``end_campaign`` emit (``None`` when
    no tracing bridge is active) so the caller can stamp it onto the returned ``CycleResult``.
    """
    stop_reason = cycle_result.stop_reason
    is_paused = stop_reason == StopReason.PAUSED
    is_crashed = stop_reason == StopReason.CRASHED
    is_render_error = stop_reason == StopReason.RENDER_ERROR
    is_optimizer_timeout = stop_reason == StopReason.OPTIMIZER_TIMEOUT
    # All four reasons leave the round partial. Render-error stashes a traceback like crash does;
    # optimizer-timeout is graceful (cause is in the log); a pause exits mid-round but the cycle
    # stays non-terminal and resumable.
    halted_mid_round = is_paused or is_crashed or is_render_error or is_optimizer_timeout
    has_traceback = is_crashed or is_render_error
    emitter = observers.dashboard
    # A pause is an operator interrupt that exits the worker but leaves the cycle ACTIVE and
    # resumable — NOT terminal. Skip every terminal-marking write (mark_finished /
    # mark_campaign_finished / mark_stopped) so index.json keeps no `finished_at` and
    # derive_run_phase keeps reading the PAUSED the loop already declared off `pause.flag`.
    # The partial round is still drained (below) so the operator sees where it paused.
    if session.state.cycle_id and not is_paused:
        # Active round at teardown — surfaces on `interrupted_round` so the operator sees which
        # round is partial without diffing the on-disk tree (works for crash too; traceback is the
        # discriminator).
        interrupted_round = int(observers.callbacks._current_round) if halted_mid_round else None
        # Active exception is gone from sys.exc_info() by now — the except clause stashed the
        # formatted traceback on session.state.crash_traceback before returning.
        crash_traceback = session.state.crash_traceback if has_traceback else None
        # index.json::status is the precise terminal reason — "active" until now, then the raw
        # StopReason value (no lossy collapse to "completed"). Operator-facing label + outcome
        # derive from the single STOP_REASON_INFO table; never re-encoded per surface.
        cycle_status = str(stop_reason)
        # index.json::final — terminal-summary namespace the `potter-l1-meta-campaign` skill gates
        # on; review.md + variant leaderboard read it for the frozen verdict.
        from promptpotter.application.optimization.dispatch.llm_call import (
            compute_optimizer_prompt_hashes,
        )

        rounds = cycle_result.rounds
        rounds_to_95 = next((r.round for r in rounds if r.accuracy >= 0.95), None)
        final_block: dict[str, Any] = {
            "stop_reason": stop_reason,
            "final_accuracy": cycle_result.best_accuracy,
            "rounds_to_95": rounds_to_95,
            "prompt_hashes": compute_optimizer_prompt_hashes(),
            "origin_composite_fitness": (rounds[0].matched_origin_composite if rounds else 0.0),
            "mode": "sweep" if sweep else "full",
            # The winner artifact, serialized for the disk readers: log.md's FinalWinnerView
            # (writers.py) fetches these from `final`.
            "winner_prompt_fields": cycle_result.winner_prompt_fields,
            "winner_pipeline_params": cycle_result.winner_pipeline_params,
        }
        session.store.campaigns.mark_finished(
            session.campaign_id,
            session.state.cycle_id,
            status=cycle_status,
            stop_reason=stop_reason,
            best_accuracy=cycle_result.best_accuracy,
            best_round=cycle_result.best_round,
            n_rounds=cycle_result.n_rounds,
            finished_at=cycle_result.finished_at,
            interrupted_round=interrupted_round,
            crash_traceback=crash_traceback,
            final=final_block,
        )
        if session.campaign_id:
            session.store.campaigns.mark_campaign_finished(
                session.campaign_id,
                status=cycle_status,
                finished_at=cycle_result.finished_at,
            )
    # Drain AFTER mark_stopped so dashboard.json's stopped state is in place before audit settles.
    # `_halted_mid_round` threads `"interrupted": true` into partial round_NNNN.json — true
    # for both Ctrl+C and uncaught-exception teardowns. The operator-facing
    # ``dashboard.json::error`` block is owned by ``_handle_error_record`` (sole
    # writer) and was already populated at the ``emit_error_record`` site.
    if emitter is not None and not is_paused:
        emitter.mark_stopped(str(stop_reason or ""))
    observers.audit._halted_mid_round = halted_mid_round
    observers.drain_all()

    obs = session.state.obs
    langfuse_trace_id: str | None = None
    if obs:
        langfuse_trace_id = obs.end_campaign(
            session.state.tracing_campaign_id,
            best_accuracy=cycle_result.best_accuracy,
            n_rounds=cycle_result.n_rounds,
            stop_reason=stop_reason,
            best_round=cycle_result.best_round,
        )
    return langfuse_trace_id


__all__ = ["RunMode", "run_optimization"]
