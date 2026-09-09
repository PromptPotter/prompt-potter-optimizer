"""``restamp`` — a thin shell over ``application/restamp.py``, which carries the rationale and the two tree shapes.
Dry-run by default; ``--apply`` rewrites."""

from __future__ import annotations

import argparse

from promptpotter.application.restamp import (
    check_round_documents,
    compact_cycle_ledgers,
    reproject_cycle_indexes,
    restamp_campaign_configs,
    shrink_measurement_runs,
)
from promptpotter.presentation.cli.commands._shared import CommandResult

__all__ = ["cmd_restamp"]


async def cmd_restamp(args: argparse.Namespace) -> CommandResult:
    apply = bool(getattr(args, "apply", False))
    counts = restamp_campaign_configs(apply=apply)
    ledgers = compact_cycle_ledgers(apply=apply)
    runs = shrink_measurement_runs(apply=apply)
    indexes = reproject_cycle_indexes(apply=apply)
    # Read-only, so --apply does not change what it does.
    rounds = check_round_documents()
    verb = "re-stamped" if apply else "would re-stamp"
    runs_line = (
        f"Measurement rows: skipped, {runs['archive_writers']} cycle(s) can still append. "
        if runs["archive_writers"]
        else f"Measurement rows: {runs['runs_shrunk']} run(s), "
        f"{runs['run_bytes_saved'] / (1024 * 1024):.1f} MB reclaimed. "
    )
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
        f"{runs_line}"
        f"Cycle indexes: {indexes['cycle_indexes_reprojected']}"
        f"/{indexes['cycle_indexes']} re-derived from their round documents."
    )
    return CommandResult(
        data={**counts, **ledgers, **runs, **rounds, **indexes},
        human=human,
    )
