"""``show-status`` subcommand — dashboard + control + last result."""

from __future__ import annotations

import argparse
import contextlib
import json
from typing import Any

from promptpotter.infrastructure.persistence.control import CONTROL_FILENAME
from promptpotter.presentation.cli.result import CommandResult
from promptpotter.presentation.cli.session import load_session
from promptpotter.presentation.views import render_status


async def cmd_status(args: argparse.Namespace) -> CommandResult:
    """Emit dashboard + control + last result.

    ``dashboard.json`` is the single source of truth for the live dashboard
    (see ``infrastructure/persistence/session_emitter.py``). JSON mode
    cats it alongside the control file and optimize_result so a human can
    ``jq`` the same shape; human mode delegates to ``render_status`` in
    ``presentation/views`` — the same renderer the notebook will adopt.
    """
    ctx = load_session(args)
    session_dir = ctx.store.sessions.session_dir(ctx.session_id)
    campaign_dir = ctx.store.campaigns.campaign_dir(ctx.cycle_id) if ctx.cycle_id else None

    payload: dict[str, Any] = {
        "session_id": ctx.session_id,
        "cycle_id": ctx.cycle_id,
        "backend_id": ctx.backend_id,
        "phase": ctx.state["phase"],
    }
    sources = [
        ("control", session_dir / CONTROL_FILENAME),
    ]
    if campaign_dir is not None:
        sources.extend(
            [
                ("dashboard", campaign_dir / "dashboard.json"),
                ("optimize_result", campaign_dir / "optimize_result.json"),
            ]
        )
    for key, path in sources:
        if path.exists():
            with contextlib.suppress(json.JSONDecodeError, OSError):
                payload[key] = json.loads(path.read_text(encoding="utf-8"))

    human = render_status(
        payload.get("dashboard", {}),
        payload.get("control"),
        payload.get("optimize_result"),
    )
    return CommandResult(data=payload, human=human)
