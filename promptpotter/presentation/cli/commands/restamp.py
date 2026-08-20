"""``restamp`` — a thin shell over ``application/restamp.py``, which carries the rationale and the two tree shapes.
Dry-run by default; ``--apply`` rewrites."""

from __future__ import annotations

import argparse

from promptpotter.application.restamp import (
    backfill_inner_facts,
    check_round_documents,
    compact_cycle_ledgers,
    restamp_campaign_configs,
)
from promptpotter.presentation.cli.commands._shared import CommandResult

__all__ = ["cmd_restamp"]


async def cmd_restamp(args: argparse.Namespace) -> CommandResult:
    apply = bool(getattr(args, "apply", False))
    counts = restamp_campaign_configs(apply=apply)
    ledgers = compact_cycle_ledgers(apply=apply)
    inner = backfill_inner_facts(apply=apply)
    # Read-only, so --apply does not change what it does.
    rounds = check_round_documents()
    verb = "re-stamped" if apply else "would re-stamp"
    human = (
        f"restamp: {verb} {counts['rewritten']} file(s); "
        f"{counts['failed']} still invalid, {counts['skipped']} unreadable. "
        f"Ledgers: {ledgers['cycles']} cycle(s), "
        f"{ledgers['bytes_saved'] / (1024 * 1024):.1f} MB reclaimed "
        f"({ledgers['skipped_producing']} producing + "
        f"{ledgers['skipped_checkin']} pre-loop, left alone). "
        f"Rounds: {rounds['rounds_checked'] - rounds['rounds_unreadable']}"
        f"/{rounds['rounds_checked']} load. "
        f"Inner seed facts {verb} onto {inner['inner_rows_filled']} row(s) from the inner "
        f"campaigns themselves; {inner['inner_rows_orphaned']} cell(s) no longer have one on "
        f"disk and stay absent. Peak lift and round budget are never backfilled — no surviving "
        f"record reproduces them exactly."
    )
    return CommandResult(data={**counts, **ledgers, **rounds, **inner}, human=human)
