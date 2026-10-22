"""The round's arms are the operator's answer: a kept draft passes untouched, an arm of theirs edits the parent."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from promptpotter.application.bench.node_context import NodeContext, bank_proposals
from promptpotter.application.optimizers.nodes import OperatorSeat
from promptpotter.application.run_observers import declare_gate, declare_running
from promptpotter.application.runner.operator_hold import hold_for_operator
from promptpotter.domain.opt_search_point import Locus, loci_of
from promptpotter.domain.phases import StopLoop, StopReason
from promptpotter.domain.results import round_document_digest
from promptpotter.domain.search_point import SHOTS_KEY
from promptpotter.domain.strict_model import StrictModel

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import ArmGateMode
    from promptpotter.application.optimizers.nodes import RoundContext
    from promptpotter.domain.command_kinds import SubmittedArm
    from promptpotter.domain.results import Proposal

logger = logging.getLogger(__name__)


_NODE = OperatorSeat.name


def authored_arm(seat: NodeContext[StrictModel], arm: SubmittedArm) -> Proposal:
    # An undeclared node or field, or a shot the seat does not draw, fails at `NodeContext.child`.
    shots = {Locus(node, SHOTS_KEY): keys for node, keys in arm.shots.items()}
    return seat.child(
        [seat.parent],
        overlay=arm.pipeline_overlay,
        prompt_loci={**loci_of(arm.prompt_fields), **shots},
        changes_description=arm.changes_description,
    )


def arm_gate_mode(ctx: RoundContext) -> ArmGateMode:
    """``off`` for a round that lands nowhere: one with no cycle has no ledger to be answered on."""
    if not ctx.cycle.session.state.cycle_id:
        return "off"
    return ctx.cycle.config.optimization.arm_gate


async def operator_arms(ctx: RoundContext, drafts: list[Proposal]) -> list[Proposal]:
    """Holds once per round: an answer already banked against this parent IS the round's arms."""
    session = ctx.cycle.session
    seat: NodeContext[StrictModel] = NodeContext(ctx, ctx.cycle.optimizer.plan.seat)
    rounds = ctx.cycle.rounds
    answered = session.store.campaigns.round_submission(session.hop, ctx.round_num)
    if answered is not None and rounds and answered.consumed == round_document_digest(rounds[-1]):
        session.human_intervened = True
        return list(answered.proposals)
    logger.warning(
        "Arm gate: round %d holds for the operator's arms (%d draft(s)). Answer with "
        "`python -m promptpotter submit-arms --file <arms.yaml>`.",
        ctx.round_num,
        len(drafts),
    )
    declare_gate(session, "arms", drafts=drafts)

    def _take() -> list[Proposal] | None:
        standing = session.control.standing_submission()
        if standing is None:
            return None
        command_id, asked = standing
        arms = [
            *(drafts[idx] for idx in asked.keep),
            *(authored_arm(seat, arm) for arm in asked.arms),
        ]
        # Recorded and banked BEFORE the ack: a producer lost between them re-reads the answer.
        session.record_intervention("submit-arms")
        bank_proposals(ctx, arms, submitted=True)
        session.control.take_submission(command_id, arms=len(arms))
        return arms

    arms = await hold_for_operator(session, _take, node=_NODE, round_num=ctx.round_num)
    if arms is None:
        raise StopLoop(StopReason.PAUSED)
    declare_running(session)
    return arms
