"""``run_optimization`` — optimize-loop entry + teardown.

Wires origin → ``init_optimization_loop`` → ``run_round_loop`` → ``_finalize_run``.
Operator forks stamp L1-surface deltas on the fresh OSP; fork-on-divergence
rebuilds observers + re-seeds ``phase_ctx`` so RoundStartView keeps reading
parent's max_rounds + patience scalars."""

from __future__ import annotations

import asyncio
import json
import logging
import traceback
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
from promptpotter.application.runner.loop import run_round_loop
from promptpotter.application.runner.termination import BudgetGate
from promptpotter.application.scoring.formula import split_scoring_block
from promptpotter.domain.phases import StopReason
from promptpotter.domain.results import CycleError, CycleResult
from promptpotter.domain.run_records import ForkSpec, LimitOverrides, OperatorForkOverride
from promptpotter.domain.sample import Sample
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.infrastructure.llm.models import emit_error_record
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import ResumeDivergenceError

logger = logging.getLogger(__name__)

# Cap on auto-rebases per CLI invocation. L2/L3 emitting fork_proposal
# every fire would otherwise spiral; after this many in-process rebases
# we exit with the last cycle's stop_reason and let the operator
# re-invoke ``resume`` if they want to keep going.
MAX_AUTO_REBASES = 10


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
        if cap_path.is_file():
            try:
                data = json.loads(cap_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception:
                logger.warning(
                    "spend_cap.json at %s unreadable; falling back to starting caps",
                    cap_path,
                )
        return {}

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
    """Snapshot the fork's effective run limits: parent frozen config with the
    operator's reconciled overrides applied (absolute values; an absent knob
    inherits the parent). A new-cycle snapshot only — the parent config is never
    mutated. Reassigning the returned config at the runner seam propagates the
    limits to every reader (loop ``max_rounds`` / patience, L1 ``pobb_epsilon``,
    the ``INIT.enter`` display event). The reconciled ``spend_budget_usd`` also
    becomes the spend-cap probe's initial ceiling (``change-spend-budget`` can
    still move it at runtime)."""
    opt_updates = {
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
    if not opt_updates:
        return config, spend_budget_usd
    new_config = config.model_copy(
        update={"optimization": config.optimization.model_copy(update=opt_updates)}
    )
    effective_spend = (
        limits.spend_budget_usd if limits.spend_budget_usd is not None else spend_budget_usd
    )
    return new_config, effective_spend


def _read_fork_seed(session: Session) -> OperatorForkOverride | None:
    """Read this cycle's declared-at-fork seed, or ``None`` for a non-steered run.

    The fork cycle_id is known (active-pointer / override, not hashed) and set on
    ``session.state`` before the runner seam, so the lookup is non-circular with
    cycle-id derivation."""
    if not session.state.cycle_id:
        return None
    return session.store.campaigns.read_fork_seed(session.campaign_id, session.state.cycle_id)


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
    resume_from_round_override: int | None = None,
    no_divergence_check: bool = False,
    fork_on_divergence: bool = False,
    sweep: bool = False,
    diag: bool = False,
    fork_payload: ForkSpec | None = None,
    halt_at_accuracy: float | None = None,
    spend_budget_usd: float | None = None,
) -> CycleResult:
    """End-to-end optimization. *observers* MUST be pre-built (ledger bound before origin).
    *origin* omitted ⇒ scored as phase 0 (CLI); supplied ⇒ reused (notebook path)."""
    started_at = utcnow_iso()
    cb = observers.callbacks

    # Operator-steered fork: the edited searchpoint, declared at fork time, lives
    # at `.overrides/seed.json` (read-once-at-bootstrap, keyed by the known fork
    # cycle_id — set before this seam by both the CLI resume and API start-run
    # launchers). It re-homes the origin (`origin_prompt_fields`) and layers its
    # `pipeline_overlay` ON TOP of the dataset overlay (seed > dataset > backend).
    # Read here — the single runner seam every launch path funnels through — not
    # threaded through each launcher + every `configure_and_apply_pipeline` caller.
    fork_seed = _read_fork_seed(session)
    if fork_seed is not None and fork_seed.pipeline_overlay:
        merged = dict(session.pipeline_params or {})
        for node, cfg in fork_seed.pipeline_overlay.items():
            merged[node] = {**merged.get(node, {}), **cfg}
        session.pipeline_params = merged
    if fork_seed is not None:
        # Reconcile the fork's run limits (rounds / spend / patience / epsilon)
        # onto a fresh config snapshot — the loop starts at round 1 and stops
        # after the reconciled max_rounds. Reassign before any downstream call.
        campaign_config, spend_budget_usd = _apply_limit_overrides(
            campaign_config, spend_budget_usd, fork_seed.limit_overrides
        )

    if origin is None:
        # Establish C0 through the single origin seam: a no-edit operator fork inherits
        # its branch-point candidate's recorded accuracy (skipping the re-score, which
        # would re-roll under a nondeterministic backend); everything else scores it.
        origin = await establish_campaign_origin(
            session, dataset, campaign_config, fork_seed=fork_seed, listener=cb
        )
        if observers.display is not None and hasattr(observers.display, "set_origin"):
            observers.display.set_origin(origin.origin_acc)

    scoring_spec = split_scoring_block(campaign_config.scoring)

    if isinstance(task_context, TaskDecomposition):
        resolved_task_context = task_context
    elif isinstance(task_context, dict):
        resolved_task_context = TaskDecomposition.from_dict(task_context)
    else:
        resolved_task_context = TaskDecomposition()

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
                no_divergence_check=no_divergence_check,
                fork_on_divergence=fork_on_divergence,
                langfuse_session_id=langfuse_session_id,
                cycle_id=session.state.cycle_id or None,
                resume_from_round_override=resume_from_round_override,
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
            # Control-local hooks (pause/stop) bind HERE — the single runner
            # seam every launch path funnels through — not at the entry points.
            # The CLI used to set these in new.py/resume.py, which left the API
            # launcher's runs (mint / start-run) unable to pause or stop: the
            # flags were written but never polled. Binding per rebase/fork
            # iteration also tracks a fork's own cycle dir (the entry-point
            # version bound once and went stale across forks).
            if session.state.cycle_id:
                runtime_dir = cycle_dir_for_probe / ".runtime"
                session.stop_check = (runtime_dir / "stop.flag").is_file
                session.pause_check = (runtime_dir / "pause.flag").is_file
            stop_reason, cycle_error = await run_round_loop(
                cycle,
                dataset,
                campaign_config,
                session,
                cb,
                sweep=sweep,
                diag=diag,
                halt_at_accuracy=halt_at_accuracy,
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
            logger.warning("Optimization interrupted before round loop entered (%s).", cause)
            stop_reason = StopReason.INTERRUPTED
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
        # Init-crash fallback: cycle_id was minted upstream, so mark_finished can still stamp final with traceback.
        if cycle is not None:
            best_sp = cycle.tracking.best_sp
            cycle_result = CycleResult(
                rounds=cycle.rounds,
                n_rounds=len(cycle.rounds),
                best_accuracy=cycle.tracking.best_accuracy,
                best_round=cycle.tracking.best_round,
                origin_accuracy=origin.origin_acc,
                winner_prompt_fields=cycle.opt_sp.prompt_field_dict() if best_sp else {},
                winner_pipeline_params=best_sp.pipeline_params if best_sp else None,
                stop_reason=stop_reason,
                started_at=started_at,
                finished_at=finished_at,
                cycle_id=session.state.cycle_id,
                session_id=session.session_id or None,
                resumed_from_round=session.state.resumed_from_round,
                error=cycle_error,
            )
        else:
            cycle_result = CycleResult(
                rounds=[],
                n_rounds=0,
                best_accuracy=0.0,
                best_round=0,
                origin_accuracy=origin.origin_acc,
                winner_prompt_fields={},
                winner_pipeline_params=None,
                stop_reason=stop_reason,
                started_at=started_at,
                finished_at=finished_at,
                cycle_id=session.state.cycle_id,
                session_id=session.session_id or None,
                resumed_from_round=session.state.resumed_from_round,
                error=cycle_error,
            )
        _finalize_run(session, observers, cycle_result, sweep=sweep)

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
) -> None:
    """Mark cycle finished, fold summary into index.json::final, render log.md, drain projections."""
    stop_reason = cycle_result.stop_reason
    is_interrupted = stop_reason == StopReason.INTERRUPTED
    is_crashed = stop_reason == StopReason.CRASHED
    is_render_error = stop_reason == StopReason.RENDER_ERROR
    is_optimizer_timeout = stop_reason == StopReason.OPTIMIZER_TIMEOUT
    # All four reasons leave the round partial. Render-error stashes a traceback like crash does;
    # optimizer-timeout is graceful (cause is in the log).
    halted_mid_round = is_interrupted or is_crashed or is_render_error or is_optimizer_timeout
    has_traceback = is_crashed or is_render_error
    emitter = observers.dashboard
    if session.state.cycle_id:
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
    if emitter is not None:
        emitter.mark_stopped(str(stop_reason or ""))
    observers.audit._halted_mid_round = halted_mid_round
    observers.drain_all()

    obs = session.state.obs
    if obs:
        obs.end_campaign(
            session.state.tracing_campaign_id,
            best_accuracy=cycle_result.best_accuracy,
            n_rounds=cycle_result.n_rounds,
            stop_reason=stop_reason,
            best_round=cycle_result.best_round,
        )


__all__ = ["run_optimization"]
