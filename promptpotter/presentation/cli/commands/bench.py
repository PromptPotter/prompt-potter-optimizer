from __future__ import annotations

import argparse
import logging

from promptpotter.application.commands import command_stopping, measuring
from promptpotter.application.commands.dispatcher import CommandCall, CommandDispatcher
from promptpotter.application.cycle_addressing import active_pointer
from promptpotter.application.views.render.primitives import bench_readout
from promptpotter.domain.command_kinds import GradeBenchPayload
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
        active = active_pointer(store)
        campaign_id = None if active is None else active.campaign_id
    if not campaign_id:
        raise NotFoundError("No active campaign to bench — name one with --campaign.")
    payload = GradeBenchPayload(campaign_id=campaign_id)
    # The pass scores the cycle holding the line, so that is the cycle a Ctrl+C pauses.
    with ctrl_c_stops_pass(store, command_stopping.pass_pause(store, payload, reason="Ctrl+C")):
        sent = await CommandDispatcher(store).answer(measuring.grade_bench, CommandCall(payload))
    score, graded = sent.result, sent.payload.campaign_id
    logger.info("bench: %s -> graded", graded)
    return CommandResult(
        data=score.model_dump(mode="json"), human=f"{graded} -> bench: {bench_readout(score)}"
    )
