"""Round loop — generate → score → escalate → stop.

The single async loop ``run_round_loop`` is the heart of every cycle.
Sweep / diag mode short-circuits after one scored round; ``halt_at_accuracy``
and ``max_spend_usd`` are sweep-toolkit halts checked after every clean
round.
"""

from __future__ import annotations

import asyncio
import logging
import traceback
from collections.abc import Callable

from promptpotter.application.bootstrap.session import Session
from promptpotter.application.config import CampaignConfig
from promptpotter.application.optimization.cycle import Cycle
from promptpotter.application.optimization.dispatch.hub import InjectionRenderError
from promptpotter.application.optimization.l1 import execute_round
from promptpotter.application.run_observers import RunCallbacks
from promptpotter.application.runner.round import (
    close_round,
    escalate_or_stop,
    post_round,
)
from promptpotter.application.runner.sweep import run_sweep_generation_only
from promptpotter.domain.phases import (
    CampaignPhase,
    StopLoop,
    StopReason,
    emit_phase,
)
from promptpotter.domain.run_records import PhaseRecord
from promptpotter.domain.sample import Sample

logger = logging.getLogger(__name__)


HARD_CAP: int = 100
"""Absolute upper bound on round_num — guards against runaway loops if
``max_rounds`` is None and L2/L3 keep firing without convergence."""


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
    max_spend_usd: float | None = None,
    spend_probe: Callable[[], float] | None = None,
) -> StopReason:
    """Round loop: generate → score → escalate → stop. sweep/diag halt after round 2.

    ``halt_at_accuracy`` / ``max_spend_usd`` are sweep-toolkit halts
    checked after every clean round: best_accuracy ≥ target halts with
    ``TARGET_HIT``; cumulative cycle spend ≥ ceiling halts with
    ``MAX_SPEND``. ``spend_probe`` returns the current cycle USD spend
    (typically bound to the LiveDashboardView's in-memory state); when
    omitted, ``max_spend_usd`` has no effect.
    """
    opt = config.optimization
    # ``resumed_from_round`` is next-L1-round-to-run (fresh = 1). ``clean_rounds``
    # tracks lifetime L1 rounds completed; origin (round 0) is not counted.
    round_num = session.state.resumed_from_round
    clean_rounds = max(session.state.resumed_from_round - 1, 0)
    max_rounds = opt.max_rounds or 999

    try:
        while clean_rounds < max_rounds and round_num < HARD_CAP:
            is_probe = cycle.probe_next_round
            if is_probe:
                round_eval_data = [s for s in dataset if s.query in cycle.warned_queries]
                round_checks = None
            else:
                # The full bank — execute_round's CAT picker narrows it to
                # the per-round sp_budget_ttest eval subset.
                round_eval_data = session.scoring.scoring_set
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
                round_eval_data,
                cb,
                degradation_checks=round_checks,
                skip_critique=sweep,
            )
            trial_dict = cycle.absorb_round(round_result, round_num)

            if cycle.axes and len(cycle.rounds) >= 2:
                cycle.axes.record_flips_from_rounds(cycle.rounds, round_num)

            if is_probe:
                cycle.probe_next_round = False
                await close_round(
                    cycle, round_result, trial_dict, round_num, session, cb, is_probe=True
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
                await close_round(cycle, round_result, trial_dict, round_num, session, cb)
                if signal.routes_to_optimizer:
                    await escalate_or_stop(cycle, config, session, round_num, cb)
                elif signal.is_abort:
                    raise StopLoop(StopReason.ABORT)
                if session.state.cycle_id:
                    session.store.campaigns.delete_round_candidates(
                        session.backend_id,
                        session.state.cycle_id,
                        round_num + 1,
                    )
                emit_phase(cb.on_phase, CampaignPhase.ESCALATION, "exit", round=round_num)
                round_num += 1
                continue

            await post_round(cycle, round_result, trial_dict, round_num, config, session, cb)
            round_num += 1
            clean_rounds += 1

            if halt_at_accuracy is not None and cycle.tracking.best_accuracy >= halt_at_accuracy:
                return StopReason.TARGET_HIT
            if (
                max_spend_usd is not None
                and spend_probe is not None
                and spend_probe() >= max_spend_usd
            ):
                return StopReason.MAX_SPEND

            if sweep and clean_rounds >= 1:
                await run_sweep_generation_only(cycle, session, cb, round_num)
                return StopReason.SWEEP_COMPLETE

            if diag and clean_rounds >= 1:
                # Force L2 on round-1 evidence (bypass stall), then peek round 2
                # with L2 overrides. round_num was incremented after post_round.
                await _force_l2(cycle, config, session, round_num - 1, cb)
                await run_sweep_generation_only(
                    cycle, session, cb, round_num, label="diag_gen_only"
                )
                return StopReason.DIAG_COMPLETE

        return StopReason.HARD_CAP if round_num >= HARD_CAP else StopReason.MAX_ROUNDS

    except StopLoop as sl:
        return sl.reason
    except (KeyboardInterrupt, asyncio.CancelledError) as exc:
        # Distinguish user-initiated Ctrl+C from a programmatic cancellation
        # (e.g. a backend timeout wrapped as CancelledError further down the
        # await chain). Both end the same way, but the operator wants to
        # know which one fired before they trust the next run.
        cause = (
            "user-initiated"
            if isinstance(exc, KeyboardInterrupt)
            else ("programmatic cancellation")
        )
        logger.warning("Optimization interrupted at round %d (%s).", round_num, cause)
        return StopReason.INTERRUPTED
    except InjectionRenderError:
        # An injection renderer raised — code drift (a renamed data-model
        # field, usually), not an LLM mistake. Stash the traceback like a
        # crash so the failing injection + cause land on
        # index.json::final.crash_traceback, but return a distinct reason:
        # the operator fixes a renderer (or reruns with
        # --ignore-render-errors), they don't debug a generic crash.
        session.state.crash_traceback = traceback.format_exc()
        logger.exception(
            "Optimization halted at round %d — an injection renderer failed. "
            "Fix the renderer and resume, or `resume --ignore-render-errors`.",
            round_num,
        )
        return StopReason.RENDER_ERROR
    except TimeoutError:
        # An optimizer LLM call blew its total wall-clock deadline twice
        # (see llm_call._chat_under_deadline). Not a crash, not a renderer
        # bug — the provider stalled mid-stream. Graceful, operator-
        # recoverable halt: a plain `resume` re-fires the call from the
        # same round. No traceback stashed — the cause is the deadline,
        # already in the warn-level log.
        logger.warning(
            "Optimization halted at round %d — an optimizer LLM call exceeded "
            "its deadline twice. Resume to retry.",
            round_num,
        )
        return StopReason.OPTIMIZER_TIMEOUT
    except Exception:
        # Per application/CLAUDE.md ("escalation flows via return value, not
        # exception"), the loop must NOT re-raise — return a typed stop
        # reason. logger.exception emits the full traceback to stderr so
        # the operator sees the cause; we also stash the formatted
        # traceback on session.state so _finalize_run can stamp it onto
        # ``index.json::final.crash_traceback`` (sys.exc_info is cleared
        # by the time _finalize_run runs).
        session.state.crash_traceback = traceback.format_exc()
        logger.exception("Optimization crashed at round %d.", round_num)
        return StopReason.CRASHED


__all__ = ["HARD_CAP", "run_round_loop"]
