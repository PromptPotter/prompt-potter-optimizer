from __future__ import annotations

import argparse

from promptpotter.application.account import local_stores, tenant_trail, trail_by_email
from promptpotter.application.views.trail import trail_notes, trail_rows
from promptpotter.config.paths import default_projects_root
from promptpotter.presentation.cli.commands.result import CommandResult


async def cmd_trail(args: argparse.Namespace) -> CommandResult:
    email = args.email.strip()
    trail = (
        trail_by_email(default_projects_root(), email)
        if email
        else tenant_trail(local_stores(tenant=args.tenant))
    )
    nothing = "Nothing recorded yet." if trail.own else f"No account with email {email}."
    return CommandResult(
        data={
            "accounts": sorted(trail.own),
            "lines": [line._asdict() for line in trail.lines],
            "unreadable": trail.unreadable,
        },
        human="\n".join([*(trail_rows(trail) or [nothing]), *trail_notes(trail)]),
    )
