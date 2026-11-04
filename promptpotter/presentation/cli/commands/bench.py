from __future__ import annotations

import argparse
import logging
from typing import TYPE_CHECKING

from promptpotter.application.commands.dispatcher import CommandCall, CommandDispatcher
from promptpotter.application.cycle_addressing import active_pointer, resolve_campaign_id_hint
from promptpotter.application.views.render.primitives import bench_readout
from promptpotter.domain.command_kinds import GradeBenchPayload, PauseCyclePayload
from promptpotter.presentation.cli.commands.launch import ctrl_c_pauses
from promptpotter.presentation.cli.commands.result import CommandResult
from promptpotter.presentation.cli.commands.workspace import open_stores
from promptpotter.shared.errors import NotFoundError

if TYPE_CHECKING:
    from promptpotter.domain.bench import BenchScore

logger = logging.getLogger("promptpotter.presentation.cli.bench")


async def cmd_bench(args: argparse.Namespace) -> CommandResult:
    store = open_stores(args)
    if args.campaign:
        campaign_id: str | None = resolve_campaign_id_hint(store, args.campaign)
    else:
        active = active_pointer(store)
        campaign_id = None if active is None else active.campaign_id
    if not campaign_id:
        raise NotFoundError("No active campaign to bench — name one with --campaign.")
    campaign = store.campaigns.load_campaign(campaign_id)
    if campaign is None:
        raise NotFoundError(f"Campaign not found: {campaign_id}")
    # The pass scores the cycle holding the line, so that is the cycle a Ctrl+C pauses.
    holder = store.campaigns.line_holder(campaign.root_hop)
    pause = PauseCyclePayload(
        campaign_id=holder.campaign_id, cycle_id=holder.cycle_id, reason="Ctrl+C"
    )
    with ctrl_c_pauses(store, [pause]):
        sent = await CommandDispatcher(store).dispatch(
            CommandCall(GradeBenchPayload(campaign_id=campaign_id))
        )
    score: BenchScore = sent.result
    logger.info("bench: %s -> graded", campaign_id)
    return CommandResult(
        data=score.model_dump(mode="json"), human=f"{campaign_id} -> bench: {bench_readout(score)}"
    )
