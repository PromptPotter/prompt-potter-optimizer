from __future__ import annotations

import argparse
import logging
from typing import TYPE_CHECKING

from promptpotter.application.commands.dispatcher import CommandCall, CommandDispatcher
from promptpotter.application.cycle_listing import active_pointer, resolve_campaign_id_hint
from promptpotter.domain.command_kinds import GradeBenchPayload
from promptpotter.presentation.cli.commands.result import CommandResult
from promptpotter.presentation.cli.commands.workspace import open_stores, refused_by

if TYPE_CHECKING:
    from promptpotter.domain.bench import BenchScore

logger = logging.getLogger("promptpotter.presentation.cli.bench")


async def cmd_bench(args: argparse.Namespace) -> CommandResult:
    store = open_stores(args)
    campaign_id = (
        resolve_campaign_id_hint(store, args.campaign)
        if args.campaign
        else active_pointer(store).campaign_id
    )
    if not campaign_id:
        return CommandResult(
            data={"status": "no_target"},
            human="No active campaign to bench — name one with --campaign.",
        )
    sent = await refused_by(
        CommandDispatcher(store).dispatch(CommandCall(GradeBenchPayload(campaign_id=campaign_id))),
        {"campaign_id": campaign_id},
    )
    if isinstance(sent, CommandResult):
        return sent
    score: BenchScore = sent.result
    logger.info("bench: %s -> graded", campaign_id)
    return CommandResult(
        data=score.model_dump(mode="json"), human=f"{campaign_id} -> bench: {score.line}"
    )
