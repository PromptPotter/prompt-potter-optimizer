from __future__ import annotations

import argparse
from pathlib import Path

from promptpotter.application.caller import Caller
from promptpotter.application.read_params import CycleExportParams
from promptpotter.application.views.render.export import ExportFiles
from promptpotter.presentation.cli.commands.result import CommandResult


def export_written(
    params: CycleExportParams, files: ExportFiles, args: argparse.Namespace, _caller: Caller
) -> CommandResult:
    ids = {"campaign_id": params.campaign_id, "cycle_id": params.cycle_id, "tier": params.tier}
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
        human=f"{params.tier}: wrote {len(written)} file(s) under {root}",
    )
