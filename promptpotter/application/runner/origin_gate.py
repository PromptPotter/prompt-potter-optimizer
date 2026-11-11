"""Round 0's one door (`open_origin_round`): a tripped gate halts, and fixing the backend then
resuming re-measures the origin's lost cells (`repair.py::rewalk_lost_origin`) with no re-mint."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from promptpotter.application.bench.resume_and_fork.decisions import record_decision
from promptpotter.application.bench.resume_and_fork.repair import rewalk_lost_origin
from promptpotter.application.runner.bench import bench_selection
from promptpotter.application.runner.campaign_result import bench_origin, declare_line_bench
from promptpotter.application.runner.loop import LoopEnd, loop_end_on
from promptpotter.application.runner.round import emit_origin_round, run_init
from promptpotter.application.runner.termination import RUN_ENDS, origin_gate_tripped
from promptpotter.domain.phases import STOP_REASON_INFO, StopReason
from promptpotter.domain.run_records import BenchCheckpointKind
from promptpotter.infrastructure.llm.spend_book import require_spend_book
from promptpotter.infrastructure.llm.telemetry import at_round

if TYPE_CHECKING:
    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.run_callbacks import RunCallbacks
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.spend import SpendRollup

logger = logging.getLogger(__name__)


async def open_origin_round(
    cycle: Cycle,
    dataset: list[Sample],
    cb: RunCallbacks,
    *,
    spend: SpendRollup,
    diagnosing: bool,
) -> LoopEnd | None:
    """Round 0's one door: the members initialize, a re-measured origin re-walks its holes, and
    until an L1 round closes every launch asks the gate, unless round 0 banked a pass. A tripped
    gate halts the launch, holding nothing; resumed still unhealthy, the resume is the operator's
    override. The gate decides before the bench spends, so a launch it halts runs no pass; an
    origin it lets through is benched before its close (a replayed origin closed on the ledger
    already). ``None`` proceeds into round 1. *spend* is the live rollup the bench pass counts its
    own delta on."""
    session = cycle.session
    open_round: int | None = None
    try:
        run_init(cycle, cb)
        if diagnosing or session.state.resumed_from_round > 1:
            await bench_line_origin(cycle, cb, spend)
            return None
        open_round = 0
        await rewalk_lost_origin(cycle, dataset, cb)
        mode = cycle.config.optimization.origin_gate
        gated = (
            not origin_gate_passed(session)
            and origin_gate_tripped(cycle.origin_round.health, mode) is not None
        )
        if not gated:
            await bench_line_origin(cycle, cb, spend)
        if cycle.origin_round.at_offset is None:
            await emit_origin_round(cycle, session, cb)
        open_round = None
        if not gated:
            return None
        if session.store.campaigns.resumed_ending(session.hop) is not StopReason.ORIGIN_GATE:
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
        pass_origin_gate(cb, by="resume")
        await bench_line_origin(cycle, cb, spend)
    except RUN_ENDS as exc:
        return loop_end_on(exc, cycle, session, round_num=0, open_round=open_round)
    return None


async def bench_line_origin(cycle: Cycle, cb: RunCallbacks, spend: SpendRollup) -> None:
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
            cb=cb,
        )
        if cycle.bench_passes is not None and book.binds("bench"):
            book.set_aside(cycle.bench_passes.reserve_usd, cycle.bench_passes.reserve_tokens)
        if (
            cycle.bench_passes is not None
            and config.bench_trigger == "each_round"
            and len(cycle.rounds) > 1
        ):
            # The selection a resumed line stands on, graded where its line holds no pass:
            # a re-enacting fork's floor, read today as the round it re-runs will be.
            with at_round(cycle.rounds[-1].round):
                cycle.bench_passes = await bench_selection(
                    session, cycle.rounds, points=cycle.points, banked=cycle.bench_passes, cb=cb
                )
    declare_line_bench(session, config, cb, passes=cycle.bench_passes, selecting=True, ending=None)


def pass_origin_gate(cb: RunCallbacks, *, by: str) -> None:
    """The operator's override, a decision of round 0: every later launch of the cycle reads it."""
    record_decision(
        cb.ledger,
        BenchCheckpointKind.ORIGIN_GATE_PASS,
        {},
        "proceed",
        node=None,
        data={"by": by},
        round=0,
    )


def origin_gate_passed(session: Session) -> bool:
    standing = session.store.campaigns.standing_rounds(session.hop, moment=None)
    return any(
        d.kind == BenchCheckpointKind.ORIGIN_GATE_PASS for d in standing.decisions.get(0, ())
    )
