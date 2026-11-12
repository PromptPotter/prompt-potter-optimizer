from __future__ import annotations

import asyncio
import logging
from typing import NamedTuple

from promptpotter.application.bench.cycle import Cycle
from promptpotter.application.bench.node_context import NodeContext
from promptpotter.application.initialization.session import Session
from promptpotter.application.optimizers.nodes import Controller, RoundContext
from promptpotter.application.run_callbacks import RunCallbacks
from promptpotter.application.run_phase_control import budget_stop
from promptpotter.application.runner.round import execute_round, post_round
from promptpotter.application.runner.termination import RUN_ENDS, end_run_on, target_tripped
from promptpotter.domain.campaign_config import CampaignConfig
from promptpotter.domain.phases import ErrorRecord, StopLoop, StopReason
from promptpotter.domain.run_records import RebaseRequest
from promptpotter.infrastructure.llm.spend_book import require_spend_book
from promptpotter.infrastructure.llm.telemetry import at_round

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


def round_bounds(session: Session, config: CampaignConfig) -> tuple[CampaignConfig, int | None]:
    """The config under the standing budget, and the round a stepped launch pauses at."""
    standing = session.store.campaigns.read_standing_budget(session.hop)
    budget = config.optimization.budget.over(standing.budget)
    if budget != config.optimization.budget:
        config = config.model_copy(
            update={"optimization": config.optimization.model_copy(update={"budget": budget})}
        )
    return config, standing.pause_at_round


async def run_round_loop(
    cycle: Cycle,
    config: CampaignConfig,
    session: Session,
    cb: RunCallbacks,
    *,
    diagnosing: Controller | None = None,
) -> LoopEnd:
    """Round 1 on, past round 0's door. *diagnosing* is ``--diag``'s controller: the run closes the round it resumes on and stops."""
    round_num = session.state.resumed_from_round
    clean_rounds = max(session.state.resumed_from_round - 1, 0)
    open_round: int | None = None

    try:
        while True:
            # SET on the config every reader holds, not merely compared: a moved cap reads one way.
            bounded, pause_at = round_bounds(session, config)
            if bounded is not config:
                config = cycle.config = bounded
            cap = config.optimization.budget.rounds
            if cap is not None and clean_rounds >= cap:
                return LoopEnd(StopReason.ROUNDS_BUDGET)
            # Off the rounds on record, so a resume counts the arms its priors raced.
            if sum(len(rr.panel) for rr in cycle.rounds) >= HARD_CAP_ARMS:
                return LoopEnd(StopReason.HARD_CAP)
            if session.state.control.pause_requested():
                return LoopEnd(StopReason.PAUSED)

            if pause_at is not None and clean_rounds >= pause_at:
                return LoopEnd(StopReason.PAUSED, stepped=True)

            logger.debug(
                "Round %d (clean=%d/%s, parent=%s)", round_num, clean_rounds, cap, cycle.parent_fold
            )

            # Through the boundary checks: a --diag controller reads the round just closed.
            with at_round(round_num):
                open_round = round_num
                cb.on_round_entered(round_num)

                # The cap's half only; the controller's is known once the round is scored (`execute_round`).
                is_final_round = cap is not None and clean_rounds + 1 >= cap

                walked = await execute_round(cycle, round_num, cb, is_final_round=is_final_round)
                open_round = None
                cut = walked.cut
                await post_round(
                    cycle,
                    walked,
                    round_num,
                    session,
                    cb,
                    is_final_round=is_final_round or cut is not None,
                )
                clean_rounds += 1

                target_stop = target_tripped(cycle, config.optimization.halt_at_fitness)
                if target_stop is not None:
                    return LoopEnd(target_stop)
                # A hold refusal cuts a round while the ceiling still reads clear, so the cut speaks first.
                tripped = cut or budget_stop(require_spend_book("a round boundary"))
                if tripped is not None:
                    return LoopEnd(tripped)

                if diagnosing is not None:
                    diagnosed = RoundContext(cycle=cycle, round_num=round_num, callbacks=cb)
                    await diagnosing.diagnose(NodeContext(diagnosed, diagnosing))
                    with at_round(round_num + 1):
                        await diagnosing.show_round(
                            RoundContext(cycle=cycle, round_num=round_num + 1, callbacks=cb)
                        )
                    return LoopEnd(StopReason.DIAG_COMPLETE)
            round_num += 1

    except RUN_ENDS as exc:
        return loop_end_on(exc, cycle, session, round_num=round_num, open_round=open_round)


def loop_end_on(
    exc: BaseException, cycle: Cycle, session: Session, *, round_num: int, open_round: int | None
) -> LoopEnd:
    """A run end raised inside a round, round 0's door's included, read as the loop's end."""
    stop_reason, error = end_run_on(exc, session, where=f"at round {round_num}")
    # Before the finalize reads a selection or banks a decision: the stop may sit inside a round.
    cycle.unwind_unclosed()
    return LoopEnd(
        stop_reason,
        error,
        fork=exc.fork if isinstance(exc, StopLoop) else None,
        interrupted_round=open_round,
        # Handed back, never swallowed: `runner/entry.py` re-raises it past the finalize.
        cancelled=exc if isinstance(exc, asyncio.CancelledError) else None,
    )
