from __future__ import annotations

import argparse

from promptpotter.connectors.harbor import pin_roster
from promptpotter.presentation.cli.commands.result import CommandResult
from promptpotter.shared.extras import Extra, require_extra


async def cmd_pin_roster(args: argparse.Namespace) -> CommandResult:
    require_extra(Extra.HARBOR)
    path = pin_roster(args.dataset, args.version)
    return CommandResult(data={"path": str(path)}, human=f"pin-roster: {path.name} written.")
