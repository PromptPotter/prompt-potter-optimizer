"""Round loop — one manifest walk per round, then the controller's boundary. Pause, budget and the
round cap are polled EVERY clean round, so ``pause-cycle`` exits resumably and
``change-run-limits`` moves a ceiling mid-flight without a restart."""

from __future__ import annotations

import logging
import traceback
from typing import NamedTuple

from promptpotter.application.bench.cycle import Cycle
from promptpotter.application.campaign_config import CampaignConfig
from promptpotter.application.initialization.session import Session
from promptpotter.application.optimizers.nodes import RoundContext
from promptpotter.application.run_observers import RunCallbacks
from promptpotter.application.run_phase_control import declare_run_phase, pause_requested
from promptpotter.application.runner.inner.ruler import refresh_inner_rulers
from promptpotter.application.runner.origin_gate import run_origin_gate
from promptpotter.application.runner.round import (
    emit_origin_round,
    execute_round,
    post_round,
    round_plan,
)
from promptpotter.application.runner.termination import (
    RUN_STOPS,
    BudgetGate,
    origin_gate_tripped,
    run_stop_reason,
    target_tripped,
)
from promptpotter.domain.phases import (
    RunPhase,
    StopLoop,
    StopReason,
)
from promptpotter.domain.run_records import ErrorRecord, PhaseRecord, RebaseRequest
from promptpotter.domain.sample import Sample
from promptpotter.infrastructure.llm.telemetry import emit_error_record
from promptpotter.infrastructure.runtime_flags import read_run_limits_mirror
from promptpotter.shared.errors import PromptCompositionError

logger = logging.getLogger(__name__)


# Runaway-loop guard for a run neither a round cap nor its controller stops, counted in ARMS raced
# rather than rounds: a one-child optimizer gets as many proposals as a five-arm one.
HARD_CAP_ARMS: int = 500


class LoopEnd(NamedTuple):
    """How the round loop ended: the stop, the error a crash left (so the caller need not re-read
    the ledger), and the fork a rebase asks for."""

    stop_reason: StopReason
    error: ErrorRecord | None = None
    fork: RebaseRequest | None = None


def set_round_cap(config: CampaignConfig, max_rounds: int | None) -> CampaignConfig:
    return config.model_copy(
        update={"optimization": config.optimization.model_copy(update={"max_rounds": max_rounds})}
    )


def _armed_round_cap(session: Session, config: CampaignConfig) -> int | None:
    armed = (
        read_run_limits_mirror(session.store.campaigns.cycle_dir(session.hop)).rounds
        if session.state.cycle_id
        else None
    )
    return config.optimization.max_rounds if armed is None else armed.max_rounds


async def run_round_loop(
    cycle: Cycle,
    dataset: list[Sample],
    config: CampaignConfig,
    session: Session,
    cb: RunCallbacks,
    *,
    diag: bool = False,
    halt_at_accuracy: float | None = None,
    stop_after_rounds: int | None = None,
    budget_gate: BudgetGate,
) -> LoopEnd:
    """The round loop. The budget gate and the round cap are re-read every clean round, so
    ``change-run-limits`` moves either mid-flight."""
    opt = config.optimization
    # resumed_from_round = next L1 round (fresh=1); clean_rounds = lifetime L1 completed (origin not counted).
    round_num = session.state.resumed_from_round
    clean_rounds = max(session.state.resumed_from_round - 1, 0)
    # `step-cycle`: advance exactly this many rounds in place then auto-pause (stays
    # resumable, so the operator can step again). Bounded by rounds completed THIS
    # invocation (delta off `clean_rounds`), reusing the pause stop below rather than
    # the configured ceiling.
    clean_rounds_at_start = clean_rounds

    try:
        # Until an L1 round closes, every launch closes round 0 from the origin IT measured and
        # gates on that verdict — a round-0 file left by a stopped run has passed no gate.
        if not diag and clean_rounds == 0:
            await emit_origin_round(cycle, session, cb)
            if origin_gate_tripped(cycle.origin_round.health, opt.origin_gate) is not None:
                gate_stop = await run_origin_gate(
                    cycle, dataset, config, session, cb, opt.origin_gate
                )
                if gate_stop is not None:
                    return LoopEnd(gate_stop)

        while True:
            # Set on the config every reader holds (the optimizer's "round N of M", the result's
            # round budget), not merely compared, so a cap moved mid-flight reads one way.
            cap = _armed_round_cap(session, config)
            if cap != config.optimization.max_rounds:
                config = cycle.config = set_round_cap(config, cap)
            if cap is not None and clean_rounds >= cap:
                return LoopEnd(StopReason.MAX_ROUNDS)
            # Off the rounds on record, so a resume counts the arms its priors raced.
            if sum(len(rr.candidate_scores) for rr in cycle.rounds) >= HARD_CAP_ARMS:
                return LoopEnd(StopReason.HARD_CAP)
            # Pause cooperation: exit cleanly at the round boundary when the
            # operator set the pause flag. The scoring phase (run_walks)
            # checks the same predicate, so a mid-round pause lands once the
            # calls already sent have; this boundary check covers the single-LLM-call phases
            # (generate / L2 / L3) that have no inner loop. The cycle stays
            # resumable — `_finalize_run` skips terminal marking on PAUSED.
            if pause_requested(session):
                declare_run_phase(session, RunPhase.PAUSED)
                return LoopEnd(StopReason.PAUSED)

            # `step-cycle` boundary: once this invocation has advanced its allotted
            # rounds, auto-pause through the same resumable stop as an operator pause.
            if (
                stop_after_rounds is not None
                and clean_rounds - clean_rounds_at_start >= stop_after_rounds
            ):
                declare_run_phase(session, RunPhase.PAUSED)
                return LoopEnd(StopReason.PAUSED)

            logger.debug(
                "Round %d (clean=%d/%s, acc=%s)",
                round_num,
                clean_rounds,
                cap,
                cycle.tracking.current_accuracy,
            )

            cb.set_round(round_num)
            ledger = session.state.ledger
            assert ledger is not None, (
                "build_run_observers must bind state.ledger before the round loop"
            )
            ledger.append(PhaseRecord(phase="round", event="enter", round=round_num))
            # One batch at the boundary, so the arms this round spawns share a scale that already
            # absorbed the last round's cells.
            refresh_inner_rulers(session, config, round_num=round_num)

            # The calendar cap's half of "no round will follow this one". The controller's half
            # can only be known after the round is scored, so `execute_round` asks it there.
            is_final_round = cap is not None and clean_rounds + 1 >= cap

            round_result = await execute_round(
                cycle, round_num, dataset, cb, is_final_round=is_final_round
            )
            cycle.absorb_round(round_result)
            await post_round(
                cycle,
                round_result,
                round_num,
                session,
                cb,
                budget_gate,
                is_final_round=is_final_round,
            )
            round_num += 1
            clean_rounds += 1

            target_stop = target_tripped(cycle, halt_at_accuracy)
            if target_stop is not None:
                return LoopEnd(target_stop)
            budget_stop = budget_gate.tripped()
            if budget_stop is not None:
                return LoopEnd(budget_stop)

            if diag and clean_rounds >= 1:
                # The controller acts on round 1's evidence and shows round 2's proposals.
                controller = round_plan(cycle.optimizer).controller
                if controller is not None:
                    await controller.diagnose(
                        RoundContext(cycle=cycle, round_num=round_num - 1, callbacks=cb)
                    )
                return LoopEnd(StopReason.DIAG_COMPLETE)

    except RUN_STOPS as stop:
        return LoopEnd(
            run_stop_reason(stop), fork=stop.fork if isinstance(stop, StopLoop) else None
        )
    except KeyboardInterrupt as exc:
        # The PAUSE FLAG's stop (`scoring/search_point_scorer.py`), not the terminal's — a
        # Ctrl+C arrives as ``CancelledError`` and lands in `runner/entry.py`. Which is also why
        # one is not caught here: a cancellation is our own machinery, and must reach its asker.
        logger.warning(
            "Optimization paused at round %d (%s).", round_num, str(exc) or "user-initiated"
        )
        return LoopEnd(StopReason.PAUSED)
    except PromptCompositionError as exc:
        # Distinct from CRASHED — the composition is at fault, not the search — and a HALT: a node
        # handed no subject still answers, confidently, and every instrument downstream reads green.
        tb = traceback.format_exc()
        session.state.crash_traceback = tb
        message = str(exc) or type(exc).__name__
        kind = type(exc).__name__
        logger.exception(
            "Optimization halted at round %d — the optimizer prompt could not be composed. "
            "Fix the composition and resume.",
            round_num,
        )
        return LoopEnd(
            StopReason.RENDER_ERROR,
            emit_error_record(kind=kind, message=message, stop_reason="RENDER_ERROR", traceback=tb),
        )
    except TimeoutError:
        # Optimizer LLM blew deadline twice (provider stalled mid-stream); plain ``resume`` re-fires.
        logger.warning(
            "Optimization halted at round %d — an optimizer LLM call exceeded "
            "its deadline twice. Resume to retry.",
            round_num,
        )
        return LoopEnd(StopReason.OPTIMIZER_TIMEOUT)
    except Exception as exc:
        # Escalation flows via return value, not exception; stash traceback for ``_finalize_run`` (sys.exc_info dead by then).
        tb = traceback.format_exc()
        session.state.crash_traceback = tb
        message = str(exc) or type(exc).__name__
        kind = type(exc).__name__
        logger.exception("Optimization crashed at round %d.", round_num)
        return LoopEnd(
            StopReason.CRASHED,
            emit_error_record(kind=kind, message=message, stop_reason="CRASHED", traceback=tb),
        )


__all__ = ["HARD_CAP_ARMS", "LoopEnd", "run_round_loop", "set_round_cap"]
