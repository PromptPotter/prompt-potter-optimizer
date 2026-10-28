from __future__ import annotations

import argparse

from promptpotter.application.datasets.startability import offer_extras
from promptpotter.presentation.cli.commands.result import CommandResult


async def cmd_offer_extras(_args: argparse.Namespace) -> CommandResult:
    extras = sorted(offer_extras())
    return CommandResult(data={"extras": extras}, human="\n".join(extras))
