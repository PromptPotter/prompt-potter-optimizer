from __future__ import annotations

import argparse
import logging

from promptpotter.application.commands.payloads import GradeBenchPayload
from promptpotter.application.runner.campaign_result import read_cycle_bench
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.infrastructure.store.stores import owned_campaign
from promptpotter.presentation.cli.commands.result import CommandResult
from promptpotter.presentation.cli.commands.workspace import (
    cycle_target,
    open_stores,
    send_to_cycle,
)

logger = logging.getLogger("promptpotter.presentation.cli.bench")

__all__ = ["cmd_bench"]


async def cmd_bench(args: argparse.Namespace) -> CommandResult:
    store = open_stores(args)
    target = cycle_target(store, args, "grade")
    if isinstance(target, CommandResult):
        return target
    campaign_id, cycle_id = target
    campaign = store.campaigns.load_campaign(campaign_id)
    if campaign is not None and not args.cycle:
        # The bench grades the campaign's result, which the cycle holding its line answers for.
        cycle_id = store.campaigns.line_holder(campaign.root_hop).cycle_id
    sent = await send_to_cycle(store, GradeBenchPayload(campaign_id=campaign_id, cycle_id=cycle_id))
    if isinstance(sent, CommandResult):
        return sent
    score = read_cycle_bench(
        store,
        owned_campaign(store, campaign_id),
        CycleHop(campaign_id=campaign_id, cycle_id=cycle_id),
    )
    logger.info("bench: %s/%s -> graded", campaign_id, cycle_id)
    return CommandResult(
        data=score.model_dump(mode="json"),
        human=f"{campaign_id}/{cycle_id} -> bench: {score.line}",
    )
