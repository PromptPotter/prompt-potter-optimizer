"""Rebuild the measurement index from the detail files and GC orphans. The index is a DERIVED read model, so it can always be
reconstructed and loses nothing. Pure disk work — zero LLM spend, no cycle mutation."""

from __future__ import annotations

import argparse
import logging

from promptpotter.application.archive_maintenance import reindex_measurement_archive
from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT
from promptpotter.infrastructure.store.stores import build_stores
from promptpotter.presentation.cli.commands._shared import CommandResult, identity_from_args

logger = logging.getLogger("promptpotter.presentation.cli.reindex")

__all__ = ["cmd_reindex"]


async def cmd_reindex(args: argparse.Namespace) -> CommandResult:
    stores = build_stores(identity_from_args(args), projects_root=DEFAULT_PROJECTS_ROOT)
    counts = reindex_measurement_archive(stores)
    human = (
        f"reindex: {counts['indexed']} run(s) indexed from "
        f"{counts['details_scanned']} detail file(s); "
        f"{counts['orphans_removed']} orphan(s) removed."
    )
    return CommandResult(data=counts, human=human)
