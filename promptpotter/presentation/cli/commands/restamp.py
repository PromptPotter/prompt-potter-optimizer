"""``restamp`` — re-stamp every on-disk ``CampaignConfig`` onto the current model.

Thin shell over ``application/restamp.py``, which carries the rationale and the two
tree shapes. Dry-run by default; ``--apply`` rewrites.
"""

from __future__ import annotations

import argparse

from promptpotter.application.restamp import restamp_campaign_configs
from promptpotter.presentation.cli.commands._shared import CommandResult

__all__ = ["cmd_restamp"]


async def cmd_restamp(args: argparse.Namespace) -> CommandResult:
    """Prune stale keys from both config surfaces; report what each file held."""
    counts = restamp_campaign_configs(apply=bool(getattr(args, "apply", False)))
    verb = "re-stamped" if getattr(args, "apply", False) else "would re-stamp"
    human = (
        f"restamp: {verb} {counts['rewritten']} file(s); "
        f"{counts['failed']} still invalid, {counts['skipped']} unreadable."
    )
    return CommandResult(data=counts, human=human)
