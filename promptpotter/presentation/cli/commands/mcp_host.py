"""Imported only when `mcp` runs (a verb names its handler), so the server's guard refuses an
install without the `mcp` extra by name, and no other verb reaches it."""

from __future__ import annotations

import argparse

from promptpotter.application.account import local_stores
from promptpotter.application.caller import Caller
from promptpotter.presentation.cli.commands.result import CommandResult
from promptpotter.presentation.mcp.server import serve_stdio


async def cmd_mcp(args: argparse.Namespace) -> CommandResult:
    """As the principal every other verb here acts as: a host is one more door of the terminal's."""
    await serve_stdio(Caller.holding(local_stores(tenant=args.tenant)))
    # Nothing to report: stdout was the wire, and its host has hung up.
    return CommandResult(human="")
