from __future__ import annotations

import argparse
import logging

from promptpotter.application.commands import measuring
from promptpotter.application.commands.dispatcher import CommandCall, CommandDispatcher
from promptpotter.application.cycle_addressing import latest_launch
from promptpotter.application.views.render.primitives import bench_readout
from promptpotter.domain.command_kinds import Door, GradeBenchPayload
from promptpotter.presentation.cli.commands.launch import ctrl_c_stops_pass
from promptpotter.presentation.cli.commands.result import CommandResult
from promptpotter.presentation.cli.commands.workspace import open_stores
from promptpotter.shared.errors import NotFoundError

logger = logging.getLogger("promptpotter.presentation.cli.bench")


async def cmd_bench(args: argparse.Namespace) -> CommandResult:
    store = open_stores(args)
    if args.campaign:
        campaign_id: str | None = args.campaign
    else:
        active = latest_launch(store)
        campaign_id = None if active is None else active.campaign_id
    if not campaign_id:
        raise NotFoundError("No active campaign to bench — name one with --campaign.")
    call = CommandCall(GradeBenchPayload(campaign_id=campaign_id), door=Door.TERMINAL)
    with ctrl_c_stops_pass(store, call):
        sent = await CommandDispatcher(store).answer(measuring.grade_bench, call)
    score, graded = sent.result, sent.payload.campaign_id
    logger.info("bench: %s -> graded", graded)
    return CommandResult(
        data=score.model_dump(mode="json"), human=f"{graded} -> bench: {bench_readout(score)}"
    )
