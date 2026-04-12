"""``show-status`` subcommand — raw JSON cat of the canonical dashboard files."""

from __future__ import annotations

import argparse
import contextlib
import json
from typing import Any

from promptpotter.infrastructure.persistence.control import CONTROL_FILENAME
from promptpotter.presentation.cli.result import CommandResult
from promptpotter.presentation.cli.session import load_session


async def cmd_status(args: argparse.Namespace) -> CommandResult:
    """Emit raw JSON from the canonical session artifacts.

    ``campaign_state.json`` is the single source of truth for the live
    dashboard (see ``infrastructure/persistence/session_emitter.py``). The
    webapp reads it directly; this command just cats it alongside the control
    file and optimize_result so a human can ``jq`` the same shape.
    """
    ctx = load_session(args)
    session_dir = ctx.store.sessions._session_dir(ctx.backend_id, ctx.session_id)

    payload: dict[str, Any] = {
        "session_id": ctx.session_id,
        "backend_id": ctx.backend_id,
        "phase": ctx.state["phase"],
    }
    for key, filename in (
        ("campaign_state", "campaign_state.json"),
        ("control", CONTROL_FILENAME),
        ("optimize_result", "optimize_result.json"),
    ):
        path = session_dir / filename
        if path.exists():
            with contextlib.suppress(json.JSONDecodeError, OSError):
                payload[key] = json.loads(path.read_text(encoding="utf-8"))

    return CommandResult(data=payload)
