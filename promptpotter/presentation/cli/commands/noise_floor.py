from __future__ import annotations

import argparse

from promptpotter.application.cycle_addressing import cycle_target
from promptpotter.application.diagnostics.noise_floor import measure_noise_floor
from promptpotter.application.jobs.launcher.admission import PassStoppedError
from promptpotter.config.logging import setup_logging
from promptpotter.domain.command_kinds import PauseCyclePayload
from promptpotter.domain.phases import StopOutcome
from promptpotter.presentation.cli.commands.launch import ctrl_c_stops_pass
from promptpotter.presentation.cli.commands.result import CommandResult
from promptpotter.presentation.cli.commands.workspace import open_stores


async def cmd_noise_floor(args: argparse.Namespace) -> CommandResult:
    setup_logging(style="full" if args.verbose else "cli")
    stores = open_stores(args)
    hop = cycle_target(stores, args.campaign, args.cycle or "")
    campaign_id, cycle_id = hop.campaign_id, hop.cycle_id
    pause = PauseCyclePayload(campaign_id=campaign_id, cycle_id=cycle_id, reason="Ctrl+C")

    try:
        with ctrl_c_stops_pass(stores, pause):
            outcome = await measure_noise_floor(stores=stores, hop=hop, k=args.k)
    except PassStoppedError as stopped:
        # A band over fewer than k rescores is no band: nothing is banked, and a rerun replays them.
        said = f"noise-floor {campaign_id}/{cycle_id}: no band banked. {stopped}"
        return CommandResult(
            data={"stopped": str(stopped)}, human=said, exit_code=StopOutcome.PAUSED.exit_code
        )

    record = outcome.record
    raw = ", ".join(f"{v:.4f}" for v in record.noise_floor_raw)
    human = (
        f"noise-floor {campaign_id}/{cycle_id} C0: k={record.noise_floor_k} "
        f"composite {record.noise_floor_mean:.4f} "
        f"[{record.noise_floor_ci_lo:.4f}, {record.noise_floor_ci_hi:.4f}] "
        f"on {record.workspace_n} samples (raw: {raw}). "
        f"Origin's recorded composite was {record.source_campaign_composite:.4f}. "
        f"Record → {outcome.artifact_path}"
    )
    return CommandResult(data=record.model_dump(), human=human)
