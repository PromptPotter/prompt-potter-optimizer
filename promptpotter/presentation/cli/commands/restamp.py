from __future__ import annotations

import argparse

from promptpotter.application.maintenance.restamp import (
    check_round_closes,
    compact_cycle_ledgers,
    reproject_cycle_indexes,
    restamp_campaign_configs,
)
from promptpotter.presentation.cli.commands.result import CommandResult

__all__ = ["cmd_restamp"]


async def cmd_restamp(args: argparse.Namespace) -> CommandResult:
    apply: bool = args.apply
    counts = restamp_campaign_configs(apply=apply)
    ledgers = compact_cycle_ledgers(apply=apply)
    indexes = reproject_cycle_indexes(apply=apply)
    # Read-only, so --apply does not change what it does.
    rounds = check_round_closes()
    verb = "re-stamped" if apply else "would re-stamp"
    human = (
        f"restamp: {verb} {counts['rewritten']} file(s); "
        f"{counts['failed']} still invalid, {counts['skipped']} unreadable. "
        f"Ledgers: {ledgers['cycles']} cycle(s), "
        f"{ledgers['bytes_saved'] / (1024 * 1024):.1f} MB reclaimed, "
        f"{ledgers['record_keys_dropped']} record key(s) the engine no longer declares pruned "
        f"off — every line carrying one was being skipped whole by the reader "
        f"({ledgers['skipped_producing']} producing + "
        f"{ledgers['skipped_checkin']} pre-loop, left alone). "
        f"Rounds: {rounds['rounds_checked'] - rounds['rounds_unreadable']}"
        f"/{rounds['rounds_checked']} load. "
        f"Cycle indexes: {indexes['cycle_indexes_reprojected']}"
        f"/{indexes['cycle_indexes']} re-derived from their ledgers."
    )
    return CommandResult(
        data={**counts, **ledgers, **rounds, **indexes},
        human=human,
    )
