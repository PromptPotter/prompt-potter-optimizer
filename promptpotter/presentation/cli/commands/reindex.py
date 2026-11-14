from __future__ import annotations

import argparse

from promptpotter.application.account import local_stores
from promptpotter.application.maintenance.archive_maintenance import reindex_measurement_archive
from promptpotter.presentation.cli.commands.result import CommandResult


async def cmd_reindex(args: argparse.Namespace) -> CommandResult:
    stores = local_stores(tenant=args.tenant)
    apply = bool(args.apply)
    counts = reindex_measurement_archive(stores, apply=apply)
    human = (
        f"reindex: {'indexed' if apply else 'would index'} {counts['indexed']} run(s), "
        f"{counts['undescribed']} undescribed left out."
    )
    if not apply:
        human += "\nDry run. Re-run with --apply to write."
    return CommandResult(data={**counts, "applied": apply}, human=human)
