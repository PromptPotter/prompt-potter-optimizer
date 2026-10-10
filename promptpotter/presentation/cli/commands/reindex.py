from __future__ import annotations

import argparse
import logging

from promptpotter.application.maintenance.archive_maintenance import reindex_measurement_archive
from promptpotter.presentation.cli.commands.result import CommandResult
from promptpotter.presentation.cli.commands.workspace import open_stores

logger = logging.getLogger("promptpotter.presentation.cli.reindex")

__all__ = ["cmd_reindex"]


async def cmd_reindex(args: argparse.Namespace) -> CommandResult:
    stores = open_stores(args)
    counts = reindex_measurement_archive(stores)
    human = f"reindex: {counts['indexed']} run(s) indexed."
    return CommandResult(data=counts, human=human)
