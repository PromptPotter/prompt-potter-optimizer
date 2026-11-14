from __future__ import annotations

import argparse
import logging

from promptpotter.application.account import local_stores
from promptpotter.application.commands import measuring
from promptpotter.application.commands.dispatcher import CommandCall, CommandDispatcher
from promptpotter.application.cycle_addressing import launched
from promptpotter.application.views.render.primitives import bench_readout
from promptpotter.domain.command_kinds import Door, GradeBenchPayload
from promptpotter.presentation.cli.commands.launch import ctrl_c_stops_pass
from promptpotter.presentation.cli.commands.result import CommandResult

logger = logging.getLogger("promptpotter.presentation.cli.bench")


async def cmd_bench(args: argparse.Namespace) -> CommandResult:
    store = local_stores(tenant=args.tenant)
    campaign_id = args.campaign or launched(store).campaign_id
    call = CommandCall(GradeBenchPayload(campaign_id=campaign_id), door=Door.TERMINAL)
    dispatcher = CommandDispatcher(store)
    with ctrl_c_stops_pass(dispatcher, call):
        sent = await dispatcher.answer(measuring.grade_bench, call)
    score, graded = sent.result, sent.payload.campaign_id
    logger.info("bench: %s -> graded", graded)
    return CommandResult(
        data=score.model_dump(mode="json"), human=f"{graded} -> bench: {bench_readout(score)}"
    )
