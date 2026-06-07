"""Round loop — generate → score → escalate → stop. Sweep/diag short-circuit after one round;
``halt_at_accuracy`` + ``budget_gate`` are sweep-toolkit halts checked every clean round.

Pause cooperation: at each iteration top the loop polls ``session.pause_check``
(``.runtime/pause.flag`` per cycle dir, written by the ``pause-cycle`` command
applier) and blocks until the flag clears. ``session.stop_check`` always wins —
stop-during-pause exits cleanly via ``StopReason.INTERRUPTED``.

Budget cooperation: ``budget_gate`` (see ``runner/termination.py``) re-reads its
USD + token caps every clean round, so the ``change-spend-budget`` command can
raise / lower a ceiling mid-flight without restarting the loop."""

from __future__ import annotations

import asyncio
import logging
import traceback

from promptpotter.application.bootstrap.session import Session
from promptpotter.application.config import CampaignConfig
from promptpotter.application.optimization.cycle import Cycle
from promptpotter.application.optimization.dispatch.hub import InjectionRenderError
from promptpotter.application.optimization.l1 import execute_round
from promptpotter.application.run_observers import RunCallbacks
from promptpotter.application.run_phase_control import pause_gate
from promptpotter.application.runner.round import (
    close_round,
    escalate_or_stop,
    post_round,
)
from promptpotter.application.runner.sweep import run_sweep_generation_only
from promptpotter.application.runner.termination import BudgetGate
from promptpotter.domain.phases import (
    CampaignPhase,
    StopLoop,
    StopReason,
    emit_phase,
)
from promptpotter.domain.results import CycleError
from promptpotter.domain.run_records import PhaseRecord
from promptpotter.domain.sample import Sample
from promptpotter.infrastructure.llm.models import emit_error_record

logger = logging.getLogger(__name__)


HARD_CAP: int = 100  # runaway-loop guard for max_rounds=None + non-converging L2/L3


async def _force_l2(
    cycle: Cycle,
    config: CampaignConfig,
    session: Session,
    round_num: int,
    cb: RunCallbacks,
) -> None:
    """Force L2 (bypass stall counter) — diag-mode bridge to round-2 peek."""
    await escalate_or_stop(cycle, config, session, round_num, cb)


async def run_round_loop(
    cycle: Cycle,
    dataset: list[Sample],
    config: CampaignConfig,
    session: Session,
    cb: RunCallbacks,
    *,
    sweep: bool = False,
    diag: bool = False,
    halt_at_accuracy: float | None = None,
    budget_gate: BudgetGate | None = None,
) -> tuple[StopReason, CycleError | None]:
    """Round loop. sweep/diag halt after round 2. ``halt_at_accuracy`` → TARGET_HIT;
    ``budget_gate.tripped()`` → SPEND_BUDGET / TOKEN_BUDGET (omit ⇒ no budget halt).

    The gate re-reads its caps every clean round so the ``change-spend-budget``
    command (``.runtime/spend_cap.json``) can mutate a ceiling mid-flight.

    Returns ``(stop_reason, error)`` — ``error`` is set on ``RENDER_ERROR`` /
    ``CRASHED`` so the caller can populate ``CycleResult.error`` without
    re-reading the ledger. The ``ErrorRecord`` is appended to the canonical
    ledger via ``emit_error_record`` at the same time."""
    opt = config.optimization
    # resumed_from_round = next L1 round (fresh=1); clean_rounds = lifetime L1 completed (origin not counted).
    round_num = session.state.resumed_from_round
    clean_rounds = max(session.state.resumed_from_round - 1, 0)
    max_rounds = opt.max_rounds or 999

    try:
        while clean_rounds < max_rounds and round_num < HARD_CAP:
            # Pause cooperation: block at the round boundary while the operator
            # has the pause flag set. The per-sample loop (run_query_loop) holds
            # the same gate, so a mid-round pause lands within one sample; this
            # boundary gate covers the single-LLM-call phases (generate / L2 /
            # L3) that have no inner loop. Stop always wins.
            if await pause_gate(session):
                return StopReason.INTERRUPTED, None

            is_probe = cycle.probe_next_round
            if is_probe:
                round_scoring_data = [s for s in dataset if s.query in cycle.warned_queries]
                round_checks = None
            else:
                # Full bank — execute_round's adaptive queue mechanism narrows it to sp_budget_ttest per round.
                round_scoring_data = session.scoring.scoring_set
                round_checks = session.scoring.degradation_checks

            logger.debug(
                "Round %d (clean=%d/%d, acc=%.3f, stall=%d/%d%s)",
                round_num,
                clean_rounds,
                max_rounds,
                cycle.tracking.current_accuracy,
                cycle.escalation.l1_stall_count,
                opt.l1_patience,
                ", PROBE" if is_probe else "",
            )

            cb.set_round(round_num)
            ledger = session.state.ledger
            assert ledger is not None, (
                "build_run_observers must bind state.ledger before the round loop"
            )
            ledger.append(PhaseRecord(phase="round", event="enter", round=round_num))

            round_result = await execute_round(
                cycle,
                round_num,
                round_scoring_data,
                cb,
                degradation_checks=round_checks,
                skip_critique=sweep,
            )
            round_payload = cycle.absorb_round(round_result, round_num)

            if cycle.axes and len(cycle.rounds) >= 2:
                cycle.axes.record_flips_from_rounds(cycle.rounds, round_num)

            if is_probe:
                cycle.probe_next_round = False
                await close_round(
                    cycle, round_result, round_payload, round_num, session, cb, is_probe=True
                )
                await escalate_or_stop(cycle, config, session, round_num, cb)
                round_num += 1
                clean_rounds += 1
                continue

            if round_result.escalation_signal:
                signal = round_result.escalation_signal
                emit_phase(
                    cb.on_phase,
                    CampaignPhase.ESCALATION,
                    "enter",
                    round=round_num,
                    check_name=signal.check_name,
                    target=signal.target,
                    degraded_rate=signal.check_result.get("degraded_rate"),
                    warning_types=signal.check_result.get("warning_types"),
                )
                await close_round(cycle, round_result, round_payload, round_num, session, cb)
                if signal.routes_to_optimizer:
                    await escalate_or_stop(cycle, config, session, round_num, cb)
                elif signal.is_abort:
                    raise StopLoop(StopReason.ABORT)
                if session.state.cycle_id:
                    session.store.campaigns.delete_round_candidates(
                        session.campaign_id,
                        session.state.cycle_id,
                        round_num + 1,
                    )
                emit_phase(cb.on_phase, CampaignPhase.ESCALATION, "exit", round=round_num)
                round_num += 1
                continue

            await post_round(cycle, round_result, round_payload, round_num, config, session, cb)
            round_num += 1
            clean_rounds += 1

            if halt_at_accuracy is not None and cycle.tracking.best_accuracy >= halt_at_accuracy:
                return StopReason.TARGET_HIT, None
            if budget_gate is not None:
                budget_stop = budget_gate.tripped()
                if budget_stop is not None:
                    return budget_stop, None

            if sweep and clean_rounds >= 1:
                await run_sweep_generation_only(cycle, session, cb, round_num)
                return StopReason.SWEEP_COMPLETE, None

            if diag and clean_rounds >= 1:
                # Force L2 on R1 evidence; peek R2 with L2 overrides.
                await _force_l2(cycle, config, session, round_num - 1, cb)
                await run_sweep_generation_only(
                    cycle, session, cb, round_num, label="diag_gen_only"
                )
                return StopReason.DIAG_COMPLETE, None

        return (StopReason.HARD_CAP if round_num >= HARD_CAP else StopReason.MAX_ROUNDS), None

    except StopLoop as sl:
        return sl.reason, None
    except (KeyboardInterrupt, asyncio.CancelledError) as exc:
        # Distinguish Ctrl+C from programmatic cancellation; same outcome, operator wants to know which fired.
        cause = (
            "user-initiated"
            if isinstance(exc, KeyboardInterrupt)
            else ("programmatic cancellation")
        )
        logger.warning("Optimization interrupted at round %d (%s).", round_num, cause)
        return StopReason.INTERRUPTED, None
    except InjectionRenderError as exc:
        # Renderer raised (code drift) — distinct from CRASHED so the operator can pinpoint a broken renderer.
        tb = traceback.format_exc()
        session.state.crash_traceback = tb
        message = str(exc) or type(exc).__name__
        kind = type(exc).__name__
        emit_error_record(kind=kind, message=message, stop_reason="RENDER_ERROR", traceback=tb)
        logger.exception(
            "Optimization halted at round %d — an injection renderer failed. "
            "Fix the renderer and resume.",
            round_num,
        )
        return StopReason.RENDER_ERROR, CycleError(kind=kind, message=message, traceback=tb)
    except TimeoutError:
        # Optimizer LLM blew deadline twice (provider stalled mid-stream); plain ``resume`` re-fires.
        logger.warning(
            "Optimization halted at round %d — an optimizer LLM call exceeded "
            "its deadline twice. Resume to retry.",
            round_num,
        )
        return StopReason.OPTIMIZER_TIMEOUT, None
    except Exception as exc:
        # Escalation flows via return value, not exception; stash traceback for ``_finalize_run`` (sys.exc_info dead by then).
        tb = traceback.format_exc()
        session.state.crash_traceback = tb
        message = str(exc) or type(exc).__name__
        kind = type(exc).__name__
        emit_error_record(kind=kind, message=message, stop_reason="CRASHED", traceback=tb)
        logger.exception("Optimization crashed at round %d.", round_num)
        return StopReason.CRASHED, CycleError(kind=kind, message=message, traceback=tb)


__all__ = ["HARD_CAP", "run_round_loop"]
