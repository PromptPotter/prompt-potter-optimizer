"""The round's arms are the operator's answer: a kept draft passes untouched, an arm of theirs edits the parent."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from promptpotter.application.bench.node_context import NodeContext
from promptpotter.application.run_observers import declare_gate, declare_running
from promptpotter.application.runner.operator_hold import hold_for_operator
from promptpotter.domain.opt_search_point import OPERATOR_SOURCE, Variation
from promptpotter.domain.phases import StopLoop, StopReason
from promptpotter.domain.results import round_document_digest
from promptpotter.domain.strict_model import StrictModel

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import ArmGateMode
    from promptpotter.application.optimizers.nodes import RoundContext
    from promptpotter.domain.command_kinds import SubmittedArm
    from promptpotter.domain.results import CandidateProposal

logger = logging.getLogger(__name__)

__all__ = ["arm_gate_mode", "operator_arms"]

_NODE = "arm_gate"


class _OperatorSeat(NodeContext[StrictModel]):
    @property
    def variation(self) -> Variation:
        return Variation(node=OPERATOR_SOURCE, mode="operator")

    def authored(self, arm: SubmittedArm) -> CandidateProposal:
        edits: dict[str, Any] = dict(arm.prompt_fields)
        return self.child(
            [self.parent],
            overlay=arm.pipeline_overlay,
            changes_description=arm.changes_description,
            **edits,
        )


def arm_gate_mode(ctx: RoundContext) -> ArmGateMode:
    """``off`` for a round that lands nowhere: a detached one has no ledger to be answered on."""
    if ctx.detached or not ctx.cycle.session.state.cycle_id:
        return "off"
    return ctx.cycle.config.optimization.arm_gate


async def operator_arms(
    ctx: RoundContext, drafts: list[CandidateProposal]
) -> list[CandidateProposal]:
    """Holds once per round: an answer already banked against this parent IS the round's arms."""
    session = ctx.cycle.session
    seat = _OperatorSeat(ctx, _NODE)
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

    def _take() -> list[CandidateProposal] | None:
        standing = session.control.standing_submission()
        if standing is None:
            return None
        command_id, asked = standing
        arms = [
            *(drafts[idx] for idx in asked.keep),
            *(seat.authored(arm) for arm in asked.arms),
        ]
        # Recorded and banked BEFORE the ack: a producer lost between them re-reads the answer.
        session.store.campaigns.record_intervention(session.hop, kind="submit-arms")
        seat.bank_proposals(arms, submitted=True)
        session.control.take_submission(command_id, arms=len(arms))
        return arms

    arms = await hold_for_operator(session, _take, node=_NODE, round_num=ctx.round_num)
    if arms is None:
        raise StopLoop(StopReason.PAUSED)
    session.human_intervened = True
    declare_running(session)
    return arms
