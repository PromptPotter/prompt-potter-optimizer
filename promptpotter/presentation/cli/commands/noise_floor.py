from __future__ import annotations

import argparse

from promptpotter.application.diagnostics.noise_floor import NoiseFloorError, measure_noise_floor
from promptpotter.config.logging import setup_logging
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.results import BankedSearchPointError
from promptpotter.presentation.cli.commands.result import CommandResult
from promptpotter.presentation.cli.commands.workspace import (
    open_stores,
    resolve_campaign,
    resolve_cycle,
)


async def cmd_noise_floor(args: argparse.Namespace) -> CommandResult:
    setup_logging(style="full" if args.verbose else "cli")
    stores = open_stores(args)
    campaign_id = resolve_campaign(stores, args.campaign)
    cycle_id = resolve_cycle(stores, campaign_id, args.cycle)

    try:
        outcome = await measure_noise_floor(
            stores=stores,
            hop=CycleHop(campaign_id=campaign_id, cycle_id=cycle_id),
            k=args.k,
        )
    except (NoiseFloorError, BankedSearchPointError) as exc:
        raise SystemExit(f"ERROR: {exc}") from exc

    record = outcome.record
    assert record.noise_floor_mean is not None
    assert record.noise_floor_ci_lo is not None
    assert record.noise_floor_ci_hi is not None
    raw = ", ".join(f"{v:.4f}" for v in (record.noise_floor_raw or []))
    human = (
        f"noise-floor {campaign_id}/{cycle_id} C0: k={record.noise_floor_k} "
        f"composite {record.noise_floor_mean:.4f} "
        f"[{record.noise_floor_ci_lo:.4f}, {record.noise_floor_ci_hi:.4f}] "
        f"on {record.workspace_n} samples (raw: {raw}). "
        f"Origin's recorded composite was {record.source_campaign_composite:.4f}. "
        f"Record → {outcome.artifact_path}"
    )
    return CommandResult(data=record.model_dump(), human=human)


__all__ = ["cmd_noise_floor"]
