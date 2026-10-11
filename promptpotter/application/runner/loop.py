from __future__ import annotations

import asyncio
import logging
from typing import NamedTuple

from promptpotter.application.bench.cycle import Cycle
from promptpotter.application.bench.node_context import NodeContext
from promptpotter.application.campaign_config import CampaignConfig
from promptpotter.application.initialization.session import Session
from promptpotter.application.optimizers.nodes import RoundContext
from promptpotter.application.run_callbacks import RunCallbacks
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
from promptpotter.domain.run_records import ErrorRecord, RebaseRequest
from promptpotter.domain.sample import Sample

logger = logging.getLogger(__name__)


# Counted in ARMS, not rounds: a one-child optimizer gets as many proposals as a five-arm one.
HARD_CAP_ARMS: int = 500


class LoopEnd(NamedTuple):
    """``interrupted_round`` is ``None`` when the stop landed at a boundary; ``cancelled`` is still owed to its asker."""

    stop_reason: StopReason
    error: ErrorRecord | None = None
    fork: RebaseRequest | None = None
    interrupted_round: int | None = None
    cancelled: asyncio.CancelledError | None = None
    stepped: bool = False


def set_round_cap(config: CampaignConfig, max_rounds: int | None) -> CampaignConfig:
    return config.model_copy(
        update={"optimization": config.optimization.model_copy(update={"max_rounds": max_rounds})}
    )


def _round_bounds(session: Session, config: CampaignConfig) -> tuple[int | None, int | None]:
    if not session.state.cycle_id:
        return config.optimization.max_rounds, None
    standing = session.store.campaigns.read_run_limits(session.hop)
    cap = config.optimization.max_rounds if standing.rounds is None else standing.rounds.max_rounds
    return cap, standing.pause_at_round


async def run_round_loop(
    cycle: Cycle,
    dataset: list[Sample],
    config: CampaignConfig,
    session: Session,
    cb: RunCallbacks,
    *,
    diag: bool = False,
    halt_at_accuracy: float | None = None,
) -> LoopEnd:
    opt = config.optimization
    round_num = session.state.resumed_from_round
    clean_rounds = max(session.state.resumed_from_round - 1, 0)
    open_round: int | None = None

    try:
        # Until an L1 round closes, EVERY launch re-closes round 0: a stopped run's file passed no gate.
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
            # SET on the config every reader holds, not merely compared: a moved cap reads one way.
            cap, pause_at = _round_bounds(session, config)
            if cap != config.optimization.max_rounds:
                config = cycle.config = set_round_cap(config, cap)
            if cap is not None and clean_rounds >= cap:
                return LoopEnd(StopReason.MAX_ROUNDS)
            # Off the rounds on record, so a resume counts the arms its priors raced.
            if sum(len(rr.candidate_scores) for rr in cycle.rounds) >= HARD_CAP_ARMS:
                return LoopEnd(StopReason.HARD_CAP)
            if session.control.pause_requested():
                return LoopEnd(StopReason.PAUSED)

            if pause_at is not None and clean_rounds >= pause_at:
                return LoopEnd(StopReason.PAUSED, stepped=True)

            logger.debug(
                "Round %d (clean=%d/%s, acc=%s)",
                round_num,
                clean_rounds,
                cap,
                cycle.tracking.current_accuracy,
            )

            cb.set_round(round_num)
            open_round = round_num
            cb.on_round_entered(round_num)
            # BEFORE the round spawns its arms, so they share a scale that absorbed the last round's cells.
            refresh_inner_rulers(session, config, round_num=round_num)

            # The cap's half only; the controller's is known once the round is scored (`execute_round`).
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
                controller = round_plan(cycle.optimizer).controller
                assert controller is not None, "a --diag launch with no controller is refused"
                await controller.diagnose(
                    NodeContext(
                        RoundContext(cycle=cycle, round_num=round_num - 1, callbacks=cb),
                        controller.name,
                    )
                )
                return LoopEnd(StopReason.DIAG_COMPLETE)

    except RUN_ENDS as exc:
        stop_reason, error = end_run_on(exc, session, where=f"at round {round_num}")
        return LoopEnd(
            stop_reason,
            error,
            fork=exc.fork if isinstance(exc, StopLoop) else None,
            interrupted_round=open_round,
            # Handed back, never swallowed: `runner/entry.py` re-raises it past the finalize.
            cancelled=exc if isinstance(exc, asyncio.CancelledError) else None,
        )


__all__ = ["HARD_CAP_ARMS", "LoopEnd", "run_round_loop", "set_round_cap"]
