"""Round 0's one door (`open_origin_round`): the origin is walked inside it, a tripped gate halts,
and fixing the backend then resuming re-measures the origin's lost cells (`walk_round_zero`) with
no re-mint."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, NamedTuple

from promptpotter.application.bench.cycle import Cycle
from promptpotter.application.bench.node_context import walk_origin
from promptpotter.application.bench.resume_and_fork.decisions import record_decision
from promptpotter.application.bench.resume_and_fork.repair import origin_only
from promptpotter.application.initialization.session import Session
from promptpotter.application.optimizers.nodes import RoundContext
from promptpotter.application.origin import lock_criterion
from promptpotter.application.run_phase_control import budget_stop
from promptpotter.application.runner.bench import bench_origin, declare_line_bench
from promptpotter.application.runner.round import (
    emit_origin_round,
    execute_round,
    post_round,
    run_init,
)
from promptpotter.application.runner.termination import (
    RUN_ENDS,
    end_run_on,
    origin_gate_tripped,
)
from promptpotter.application.scoring.passes import measure_noise_floor
from promptpotter.domain.campaign_config import CampaignConfig
from promptpotter.domain.cell_fold import repairable_holes
from promptpotter.domain.phases import STOP_REASON_INFO, StopLoop, StopReason
from promptpotter.domain.records.error import ErrorRecord
from promptpotter.domain.records.run_records import (
    OriginGatePass,
    RebaseRequest,
    RoundEnteredRecord,
)
from promptpotter.infrastructure.llm.spend_book import require_spend_book
from promptpotter.infrastructure.llm.telemetry import at_round

if TYPE_CHECKING:
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.spend import SpendRollup
    from promptpotter.infrastructure.ledger import CycleEventLog

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
    ledger: CycleEventLog,
) -> LoopEnd:
    """Round 1 on, past round 0's door."""
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

            with at_round(round_num):
                open_round = round_num
                ledger.append(RoundEnteredRecord(round=round_num))

                # The cap's half only; the controller's is known once the round is scored (`execute_round`).
                is_final_round = cap is not None and clean_rounds + 1 >= cap

                walked = await execute_round(
                    cycle, round_num, ledger, is_final_round=is_final_round
                )
                open_round = None
                cut = walked.cut
                await post_round(
                    cycle,
                    walked,
                    round_num,
                    session,
                    ledger,
                    is_final_round=is_final_round or cut is not None,
                )
                clean_rounds += 1
                # A hold refusal cuts a round while the ceiling still reads clear, so the cut speaks first.
                tripped = cut or budget_stop(require_spend_book("a round boundary"))
                if tripped is not None:
                    return LoopEnd(tripped)
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


async def open_origin_round(
    cycle: Cycle,
    dataset: list[Sample],
    ledger: CycleEventLog,
    *,
    spend: SpendRollup,
    resumed_ending: StopReason | None,
) -> LoopEnd | None:
    """Round 0's one door: the members initialize, the origin is walked (`walk_round_zero`), and
    until an L1 round closes every launch asks the gate, unless round 0 banked a pass. A tripped
    gate halts the launch, holding nothing; a launch resuming the gate's halt (*resumed_ending*)
    still unhealthy is the operator's override. The gate decides before the bench spends, so a
    launch it halts runs no pass; an origin it lets through is benched before its close (a
    replayed origin closed on the ledger already). ``None`` proceeds into round 1. *spend* is the
    live rollup the bench pass counts its own delta on."""
    session = cycle.session
    open_round: int | None = None
    try:
        run_init(cycle, ledger)
        if session.state.resumed_from_round > 1:
            await bench_line_origin(cycle, ledger, spend)
            return None
        open_round = 0
        await walk_round_zero(cycle, dataset, ledger)
        mode = cycle.config.optimization.origin_gate
        gated = (
            not origin_gate_passed(session)
            and origin_gate_tripped(cycle.origin_round.health, mode) is not None
        )
        if not gated:
            await bench_line_origin(cycle, ledger, spend)
        if cycle.origin_round.at_offset is None:
            await emit_origin_round(cycle, session, ledger)
            replicates = cycle.config.origin_replicates
            if not gated and replicates is not None:
                origin = cycle.origin_round.head
                assert origin is not None, "round 0 ends on its origin"
                await measure_noise_floor(session, cycle.points.job(origin.individual), replicates)
        open_round = None
        if not gated:
            return None
        if resumed_ending is not StopReason.ORIGIN_GATE:
            health = cycle.origin_round.health
            logger.error(
                "Origin gate (%s): round 0 is %s (%s) — halted. %s",
                mode,
                health.grade,
                health.last_error or "no error recorded",
                STOP_REASON_INFO[StopReason.ORIGIN_GATE].next_step,
            )
            return LoopEnd(StopReason.ORIGIN_GATE)
        # Resuming an origin the gate halted, still unhealthy, is the operator's override.
        pass_origin_gate(ledger, by="resume")
        await bench_line_origin(cycle, ledger, spend)
    except RUN_ENDS as exc:
        return loop_end_on(exc, cycle, session, round_num=0, open_round=open_round)
    return None


async def walk_round_zero(cycle: Cycle, dataset: list[Sample], ledger: CycleEventLog) -> None:
    """Round 0's ONE walk, inside its door and past the divergence check, so a resume it refuses
    pays nothing. An unclosed origin is walked: a seated one on its scoring set, one whose rows
    another launch measured (inherited, or a fork cut at round 0) on its own cells, the archive
    replaying all but the errored. A closed origin no round has read re-walks only what it lost.
    The criterion locks at the walk's close (`origin.py::lock_criterion`)."""
    session = cycle.session
    origin, head = cycle.origin_round, cycle.origin_round.head
    assert origin.election is not None and head is not None, "round 0 ends on its origin"
    rows = origin.rows(origin.election.ended_on)
    holes, unsent = repairable_holes(rows), max(0, head.expected_samples - len(rows))
    if origin.at_offset is not None:
        standing = session.store.campaigns.standing_rounds(session.hop, moment=None)
        if not origin_only(cycle.rounds, standing) or not (holes or unsent):
            return
        logger.warning(
            "Resume re-measures the origin's %d lost cell(s) and the %d its walk never sent; "
            "round 0 closes again on them.",
            holes,
            unsent,
        )
    elif not head.expected_samples:
        return
    held = {cell.key for cell in rows}
    pool = session.state.scoring.partition.search
    cells = cycle.origin_cells(dataset) if unsent else [s for s in pool if s.key in held]
    if not cells:
        return
    read = await walk_origin(RoundContext(cycle=cycle, round_num=0, ledger=ledger), cells)
    if origin.at_offset is None:
        read = lock_criterion(cycle, read, ledger)
    cycle.seat_origin(read, origin.optimizer_state)


async def bench_line_origin(cycle: Cycle, ledger: CycleEventLog, spend: SpendRollup) -> None:
    """The origin's bench pass, its price set aside so the selection's pass still fits under the
    search's ceiling, then the line's bench declared."""
    session, config = cycle.session, cycle.config
    origin = cycle.origin_round.head
    assert origin is not None, "round 0 ends on its origin"
    if config.bench_trigger != "manual" and session.state.scoring.partition.bench:
        book = require_spend_book("the bench's set-aside")
        cycle.bench_passes = await bench_origin(
            session,
            cycle.points.job(origin.individual),
            origin_id=origin.individual.id,
            spend=spend,
            ledger=ledger,
        )
        if cycle.bench_passes is not None and book.binds("bench"):
            book.set_aside(cycle.bench_passes.reserve_usd, cycle.bench_passes.reserve_tokens)
    declare_line_bench(
        session, config, ledger, passes=cycle.bench_passes, selecting=True, ending=None
    )


def pass_origin_gate(ledger: CycleEventLog, *, by: str) -> None:
    """The operator's override, a decision of round 0: every later launch of the cycle reads it."""
    record_decision(ledger, OriginGatePass(by=by), "proceed", node=None, round=0)


def origin_gate_passed(session: Session) -> bool:
    standing = session.store.campaigns.standing_rounds(session.hop, moment=None)
    return any(isinstance(d.inputs_ref, OriginGatePass) for d in standing.decisions.get(0, ()))
