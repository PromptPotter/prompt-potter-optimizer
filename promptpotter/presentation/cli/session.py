"""CLI session state plumbing — load and type-access session state.

``SessionCtx`` wraps the raw state dict that ``Stores.sessions`` reads
from disk, exposing typed accessors so command bodies don't have to
string-index every field.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from promptpotter.infrastructure.store import Stores, build_stores

if TYPE_CHECKING:
    from promptpotter.application.config import CampaignConfig


@dataclass
class SessionCtx:
    """Loaded session state with typed accessors over the raw state dict."""

    store: Stores
    state: dict
    backend_id: str
    session_id: str
    cycle_id: str

    @property
    def init_params(self) -> dict:
        return self.state["init_params"]

    @property
    def backend_url(self) -> str:
        return self.init_params["backend_url"]

    @property
    def campaign_config(self) -> CampaignConfig:
        from promptpotter.application.config import load_campaign_config

        return load_campaign_config(self.state["campaign_config"])

    @property
    def task_context(self) -> dict | None:
        return self.state.get("task_context")

    def save_phase(self, phase: str) -> None:
        """Set phase and persist the session state."""
        self.state["phase"] = phase
        self.store.sessions.update(self.session_id, dict(self.state))


def no_dataset_hint() -> str:
    """Formatted list of discovered datasets + exact init commands."""
    datasets = sorted(p.parent.name for p in Path("datasets").glob("*/campaign.json"))
    lines = [f"  python -m promptpotter init --dataset-name {name}" for name in datasets]
    body = "\n".join(lines) if lines else "  (no datasets found under ./datasets/)"
    return "Available datasets:\n\n" + body


def load_session(args: argparse.Namespace) -> SessionCtx:
    """Load active session from disk."""
    from promptpotter.infrastructure.store import active_pointer_exists, read_active_pointer

    if not active_pointer_exists():
        raise SystemExit(
            "ERROR: No active session.\n\n"
            "To start a campaign, init one of the available datasets:\n\n" + no_dataset_hint()
        )
    store = build_stores(tenant_id=getattr(args, "tenant", "default"))
    _tid, pointer_sid, pointer_cid = read_active_pointer()
    session_id = getattr(args, "session", None) or pointer_sid
    if not session_id:
        raise SystemExit("ERROR: No active session_id in pointer.")

    state = store.sessions.read(session_id)
    if not state:
        raise SystemExit(f"ERROR: Session '{session_id}' not found.")

    cycle_id = getattr(args, "cycle", None) or pointer_cid or ""
    backend_id = state.get("init_params", {}).get("backend_id", "") or ""
    return SessionCtx(store, state, backend_id, session_id, cycle_id)


def load_campaign_config(config_path: str | None) -> dict:
    """Load campaign config JSON, unwrapping the optional outer ``campaign_config`` key."""
    if not config_path:
        return {}
    with open(config_path) as f:
        data = json.load(f)
    return data.get("campaign_config", data) or {}
