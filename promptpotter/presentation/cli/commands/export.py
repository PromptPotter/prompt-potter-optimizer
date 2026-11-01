from __future__ import annotations

import argparse
from pathlib import Path

from promptpotter.application.cycle_reads import export_cycle
from promptpotter.presentation.cli.commands.result import CommandResult
from promptpotter.presentation.cli.commands.workspace import open_stores, resolve_target


async def cmd_export(args: argparse.Namespace) -> CommandResult:
    stores = open_stores(args)
    hop = resolve_target(args, stores)
    ids = {"campaign_id": hop.campaign_id, "cycle_id": hop.cycle_id, "tier": args.tier}
    files = export_cycle(stores, (hop,), args.tier)
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
