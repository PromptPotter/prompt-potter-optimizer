"""``control`` subcommand — write HITL control signals to campaign_control.json."""

from __future__ import annotations

import argparse
import json
import sys

from promptpotter.infrastructure.persistence.control import CONTROL_FILENAME
from promptpotter.presentation.cli.parser import SIGNAL_ACTIONS
from promptpotter.presentation.cli.result import CommandResult
from promptpotter.presentation.cli.session import load_session


async def cmd_control(args: argparse.Namespace) -> CommandResult:
    """Write a control signal to campaign_control.json."""
    from promptpotter.infrastructure.store.base import write_json

    ctx = load_session(args)
    session_dir = ctx.store.sessions._session_dir(ctx.backend_id, ctx.session_id)
    control_path = session_dir / CONTROL_FILENAME
    if not control_path.exists():
        sys.exit(f"ERROR: No {CONTROL_FILENAME} — run 'optimize' first.")

    control = json.loads(control_path.read_text(encoding="utf-8"))
    key, value = SIGNAL_ACTIONS[args.signal]
    control[key] = value
    write_json(control_path, control)

    return CommandResult(
        data={"signal": args.signal, "control": control},
        human=f"Control: {args.signal} requested.",
    )
