"""Round loop — one manifest walk per round, then the controller's boundary. Pause, budget and the
round cap are polled EVERY clean round, so ``pause-cycle`` exits resumably and
``change-run-limits`` moves a ceiling mid-flight without a restart."""

from __future__ import annotations

import asyncio
import logging
from typing import NamedTuple

from promptpotter.application.bench.cycle import Cycle
from promptpotter.application.campaign_config import CampaignConfig
from promptpotter.application.initialization.session import Session
from promptpotter.application.optimizers.nodes import RoundContext
from promptpotter.application.run_observers import RunCallbacks
from promptpotter.application.runner.inner.ruler import refresh_inner_rulers
from promptpotter.application.runner.origin_gate import run_origin_gate
from promptpotter.application.runner.round import (
    emit_origin_round,
    execute_round,
    post_round,
    round_plan,
)
from promptpotter.application.runner.termination import (
    RUN_ENDS,
    end_run_on,
    origin_gate_tripped,
    target_tripped,
)
from promptpotter.domain.phases import StopLoop, StopReason
from promptpotter.domain.run_records import ErrorRecord, PhaseRecord, RebaseRequest
from promptpotter.domain.sample import Sample
from promptpotter.infrastructure.runtime_flags import read_run_limits_mirror

logger = logging.getLogger(__name__)


# Runaway-loop guard for a run neither a round cap nor its controller stops, counted in ARMS raced
# rather than rounds: a one-child optimizer gets as many proposals as a five-arm one.
HARD_CAP_ARMS: int = 500


class LoopEnd(NamedTuple):
    """How the round loop ended: the stop, the error a crash left (so the caller need not re-read
    the ledger), the fork a rebase asks for, the round the stop left open — ``None`` when it
    landed at a boundary — and the cancellation the caller still owes its asker."""

    stop_reason: StopReason
    error: ErrorRecord | None = None
    fork: RebaseRequest | None = None
    interrupted_round: int | None = None
    cancelled: asyncio.CancelledError | None = None


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
) -> LoopEnd:
    """The round loop. The spend ceiling and the round cap are re-read every clean round, so
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
    open_round: int | None = None

    try:
        # Until an L1 round closes, every launch closes round 0 from the origin IT measured and
        # gates on that verdict — a round-0 file left by a stopped run has passed no gate.
        if not diag and clean_rounds == 0:
            open_round = 0
            await emit_origin_round(cycle, session, cb)
            open_round = None
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
            if session.control.pause_requested():
                return LoopEnd(StopReason.PAUSED)

            # `step-cycle` boundary: once this invocation has advanced its allotted
            # rounds, auto-pause through the same resumable stop as an operator pause.
            if (
                stop_after_rounds is not None
                and clean_rounds - clean_rounds_at_start >= stop_after_rounds
            ):
                return LoopEnd(StopReason.PAUSED)

            logger.debug(
                "Round %d (clean=%d/%s, acc=%s)",
                round_num,
                clean_rounds,
                cap,
                cycle.tracking.current_accuracy,
            )

            cb.set_round(round_num)
            open_round = round_num
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

            round_result, cut = await execute_round(
                cycle, round_num, dataset, cb, is_final_round=is_final_round
            )
            open_round = None
            await post_round(
                cycle,
                round_result,
                round_num,
                session,
                cb,
                is_final_round=is_final_round or cut is not None,
            )
            round_num += 1
            clean_rounds += 1

            target_stop = target_tripped(cycle, halt_at_accuracy)
            if target_stop is not None:
                return LoopEnd(target_stop)
            # A hold refusal cuts a round while the ceiling still reads clear, so the cut speaks first.
            budget_stop = cut or session.control.budget_tripped()
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

    except RUN_ENDS as exc:
        stop_reason, error = end_run_on(exc, session, where=f"at round {round_num}")
        return LoopEnd(
            stop_reason,
            error,
            fork=exc.fork if isinstance(exc, StopLoop) else None,
            interrupted_round=open_round,
            # Handed back, never answered: only here is the round it cut known, and
            # `runner/entry.py` re-raises it past the finalize so it still reaches its asker.
            cancelled=exc if isinstance(exc, asyncio.CancelledError) else None,
        )


__all__ = ["HARD_CAP_ARMS", "LoopEnd", "run_round_loop", "set_round_cap"]
