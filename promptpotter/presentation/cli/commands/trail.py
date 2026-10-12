from __future__ import annotations

import argparse

from promptpotter.application.account_trail import (
    account_trail,
    trail_by_email,
    trail_row,
    unreadable_note,
)
from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT
from promptpotter.presentation.cli.commands.result import CommandResult
from promptpotter.presentation.cli.commands.workspace import open_stores
from promptpotter.shared.identity import acting_principal_id

__all__ = ["cmd_trail"]


async def cmd_trail(args: argparse.Namespace) -> CommandResult:
    email = args.email.strip().lower()
    if not email:
        stores = open_stores(args)
        own = acting_principal_id(stores.identity)
        lines = [
            line._replace(actor="" if line.actor == own else line.actor)
            for line in account_trail(stores.base_dir)
        ]
        note = ""
    else:
        found = trail_by_email(DEFAULT_PROJECTS_ROOT, email)
        note = unreadable_note(found.unreadable)
        if found.lines is None:
            return CommandResult(
                data={"lines": None, "unreadable": found.unreadable},
                human="\n".join(part for part in (f"No account with email {email}.", note) if part),
            )
        lines = found.lines
    rows = [trail_row(line) for line in lines] or ["Nothing recorded yet."]
    return CommandResult(
        data={"lines": [line._asdict() for line in lines]},
        human="\n".join(part for part in (*rows, note) if part),
    )
