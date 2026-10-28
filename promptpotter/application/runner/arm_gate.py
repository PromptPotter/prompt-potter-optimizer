"""The round's arms are the operator's answer: a kept draft passes untouched, an arm of theirs edits the parent."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from promptpotter.application.bench.node_context import NodeContext, bank_proposals
from promptpotter.application.run_observers import declare_gate, declare_running
from promptpotter.application.runner.operator_hold import hold_for_operator
from promptpotter.domain.phases import StopLoop, StopReason
from promptpotter.domain.results import round_document_digest

if TYPE_CHECKING:
    from promptpotter.application.optimizers.nodes import RoundContext
    from promptpotter.domain.arms import Proposal
    from promptpotter.domain.campaign_config import ArmGateMode

logger = logging.getLogger(__name__)


def arm_gate_mode(ctx: RoundContext) -> ArmGateMode:
    return ctx.cycle.config.optimization.arm_gate


async def operator_arms(ctx: RoundContext, drafts: list[Proposal]) -> list[Proposal]:
    """Holds once per round: an answer already banked against this parent IS the round's arms."""
    session = ctx.cycle.session
    seat = ctx.cycle.optimizer.plan.seat
    authoring = NodeContext(ctx, seat)
    rounds = ctx.cycle.rounds
    answered = session.store.campaigns.round_submission(session.hop, ctx.round_num)
    if answered is not None and rounds and answered.consumed == round_document_digest(rounds[-1]):
        return list(answered.proposals)
    logger.warning(
        "Arm gate: round %d holds for the operator's arms (%d draft(s)). Answer with "
        "`python -m promptpotter submit-arms --arms <arms.yaml>`.",
        ctx.round_num,
        len(drafts),
    )
    declare_gate(session, "arms", drafts=drafts)

    def _take() -> list[Proposal] | None:
        standing = session.state.control.standing_submission()
        if standing is None:
            return None
        command_id, asked = standing
        arms = [
            *(drafts[idx] for idx in asked.keep),
            *(seat.author(authoring, ctx.cycle.parent, arm) for arm in asked.arms),
        ]
        # Recorded and banked BEFORE the ack: a producer lost between them re-reads the answer.
        session.record_intervention("submit-arms")
        bank_proposals(ctx, arms, submitted=True)
        session.state.control.take_submission(command_id, arms=len(arms))
        return arms

    arms = await hold_for_operator(session, _take, node=seat.name, round_num=ctx.round_num)
    if arms is None:
        raise StopLoop(StopReason.PAUSED)
    declare_running(session)
    return arms
