"""``profile`` subcommand — manage per-backend connector profiles."""

from __future__ import annotations

import argparse
import json

from promptpotter.infrastructure.store import build_stores, read_active_pointer
from promptpotter.presentation.cli.result import CommandResult


async def cmd_profile(args: argparse.Namespace) -> CommandResult:
    """Manage connector profile — persistent per-backend defaults."""
    store = build_stores(tenant_id=getattr(args, "tenant", "default"))
    backend_id = args.backend_id

    if args.save:
        _tid, pointer_sid, _cid = read_active_pointer()
        sid = getattr(args, "session", None) or pointer_sid
        state = store.sessions.read(sid) if sid else None
        if not state:
            return CommandResult(
                data={"saved": False, "error": "no_active_session"},
                human="ERROR: No active session — run `init` first.",
            )
        backend_id = backend_id or state.get("init_params", {}).get("backend_id", "")
        profile = state.get("campaign_config", {})
        store.backends.save_connector_profile(backend_id, profile)
        return CommandResult(
            data={"saved": True, "backend_id": backend_id},
            human=f"Profile saved for '{backend_id}'.",
        )

    if args.set:
        key, raw_value = args.set
        try:
            value = json.loads(raw_value)
        except json.JSONDecodeError:
            value = raw_value
        profile = store.backends.load_connector_profile(backend_id) or {}
        profile[key] = value
        store.backends.save_connector_profile(backend_id, profile)
        return CommandResult(
            data={"backend_id": backend_id, "key": key, "value": value},
            human=f"Profile '{backend_id}': {key} = {json.dumps(value)}",
        )

    profile = store.backends.load_connector_profile(backend_id)
    if not profile:
        return CommandResult(
            data={"backend_id": backend_id, "profile": None},
            human=f"No connector profile for '{backend_id}'. Use --save or --set to create one.",
        )
    return CommandResult(
        data={"backend_id": backend_id, "profile": profile},
        human=json.dumps(profile, indent=2, default=str),
    )
