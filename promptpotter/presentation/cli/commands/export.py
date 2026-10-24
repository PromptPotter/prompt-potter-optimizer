from __future__ import annotations

import argparse
from pathlib import Path

from promptpotter.application.cycle_reads import export_cycle
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.presentation.cli.commands.result import CommandResult
from promptpotter.presentation.cli.commands.workspace import cycle_target, open_stores
from promptpotter.shared.errors import ConflictError, NotFoundError


async def cmd_export(args: argparse.Namespace) -> CommandResult:
    stores = open_stores(args)
    target = cycle_target(stores, args, "export")
    if isinstance(target, CommandResult):
        return target
    campaign_id, cycle_id = target
    ids = {"campaign_id": campaign_id, "cycle_id": cycle_id, "tier": args.tier}
    try:
        files = export_cycle(
            stores, (CycleHop(campaign_id=campaign_id, cycle_id=cycle_id),), args.tier
        )
    except (NotFoundError, ConflictError) as exc:
        return CommandResult(data={**ids, "status": "refused"}, human=str(exc))
    single = files.single
    if single is not None and args.out is None:
        return CommandResult(data={**ids, "text": single}, human=single.rstrip("\n"))
    root = (args.out or Path.cwd()) / files.name
    for rel, text in files.files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    written = sorted(files.files)
    return CommandResult(
        data={**ids, "dir": str(root), "files": written},
        human=f"{args.tier}: wrote {len(written)} file(s) under {root}",
    )
