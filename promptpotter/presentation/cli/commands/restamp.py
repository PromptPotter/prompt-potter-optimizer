"""``restamp`` — a thin shell over ``application/restamp.py``, which carries the rationale and the two tree shapes.
Dry-run by default; ``--apply`` rewrites."""

from __future__ import annotations

import argparse

from promptpotter.application.restamp import compact_cycle_ledgers, restamp_campaign_configs
from promptpotter.presentation.cli.commands._shared import CommandResult

__all__ = ["cmd_restamp"]


async def cmd_restamp(args: argparse.Namespace) -> CommandResult:
    apply = bool(getattr(args, "apply", False))
    counts = restamp_campaign_configs(apply=apply)
    ledgers = compact_cycle_ledgers(apply=apply)
    verb = "re-stamped" if apply else "would re-stamp"
    human = (
        f"restamp: {verb} {counts['rewritten']} file(s); "
        f"{counts['failed']} still invalid, {counts['skipped']} unreadable. "
        f"Ledgers: {ledgers['cycles']} cycle(s), "
        f"{ledgers['bytes_saved'] / (1024 * 1024):.1f} MB reclaimed "
        f"({ledgers['skipped_live']} live, left alone)."
    )
    return CommandResult(data={**counts, **ledgers}, human=human)
