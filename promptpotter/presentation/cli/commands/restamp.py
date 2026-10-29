from __future__ import annotations

import argparse

from promptpotter.application.maintenance.restamp import (
    check_readable,
    compact_cycle_ledgers,
    prune_finished_jobs,
    prune_stored_documents,
    reproject_cycle_indexes,
    restamp_measurement_archives,
)
from promptpotter.presentation.cli.commands.result import CommandResult


async def cmd_restamp(args: argparse.Namespace) -> CommandResult:
    apply: bool = args.apply
    counts = prune_stored_documents(apply=apply)
    ledgers = compact_cycle_ledgers(apply=apply)
    indexes = reproject_cycle_indexes(apply=apply)
    archive = restamp_measurement_archives(apply=apply)
    jobs = prune_finished_jobs(apply=apply)
    # Read-only, so --apply does not change what it does.
    read = check_readable()
    verb = "pruned" if apply else "would prune"
    human = (
        f"restamp: {verb} {counts['rewritten']} file(s); "
        f"{counts['failed']} still invalid, {counts['skipped']} unreadable. "
        f"Ledgers: {ledgers['cycles']} cycle(s), "
        f"{ledgers['bytes_saved'] / (1024 * 1024):.1f} MB reclaimed, "
        f"{ledgers['record_keys_dropped']} record key(s) the engine no longer declares pruned "
        f"off — every line carrying one refused its cycle "
        f"({ledgers['skipped_producing']} producing, left alone). "
        f"Cycles: {read['ledgers_checked'] - read['ledgers_refused']}"
        f"/{read['ledgers_checked']} read. "
        f"Campaigns unreadable: {read['campaigns_unreadable']}. "
        f"Block packs refused: {read['packs_refused']}. "
        f"Cycle indexes: {indexes['cycle_indexes_reprojected']}"
        f"/{indexes['cycle_indexes']} re-derived from their ledgers. "
        f"Archive: {archive['archive_records_removed']} record(s) "
        f"{'removed' if apply else 'to remove'}, {archive['archive_records_pruned']} pruned, "
        f"{read['archive_refused']} file(s) unreadable"
        f"{f' ({w} cycle(s) appending, not walked)' if (w := archive['archive_writers']) else ''}. "
        f"Jobs: {jobs['jobs_finished']} finished {'deleted' if apply else 'to delete'}, "
        f"{jobs['jobs_invalid']} invalid."
    )
    return CommandResult(
        data={**counts, **ledgers, **read, **indexes, **archive, **jobs},
        human=human,
    )
