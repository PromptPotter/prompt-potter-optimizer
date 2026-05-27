"""CLI session-state plumbing — ``SessionCtx`` wraps the raw state dict with typed accessors."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.infrastructure.store import Stores, build_stores
from promptpotter.shared.identity import default_identity

if TYPE_CHECKING:
    from promptpotter.application.config import CampaignConfig


@dataclass
class SessionCtx:
    """Loaded session state with typed accessors over the raw state dict."""

    store: Stores
    state: dict[str, Any]
    backend_id: str
    session_id: str
    campaign_id: str
    cycle_id: str

    @property
    def init_params(self) -> dict[str, Any]:
        params: dict[str, Any] = self.state["init_params"]
        return params

    @property
    def backend_url(self) -> str:
        url: str = self.init_params["backend_url"]
        return url

    @property
    def campaign_config(self) -> CampaignConfig:
        """Live ``datasets/{name}/campaign.json`` (edits picked up + drift-detected); frozen ``campaign.json::config`` fallback. Session-state copy never consulted (may carry stale schema fields)."""
        from promptpotter.application.config import (
            load_campaign_config as validate_campaign_config,
        )
        from promptpotter.infrastructure.store.paths import DEFAULT_DATASETS_ROOT

        dataset_name = self.init_params.get("dataset_name") or ""
        raw: dict[str, Any] = {}
        if dataset_name:
            ds_path = DEFAULT_DATASETS_ROOT / dataset_name / "campaign.json"
            if ds_path.is_file():
                raw = load_campaign_config(str(ds_path))
        if not raw and self.campaign_id:
            campaign = self.store.campaigns.load_campaign(self.campaign_id)
            if campaign is not None:
                raw = campaign.config
        return validate_campaign_config(raw)

    @property
    def task_context(self) -> dict[str, Any] | None:
        return self.state.get("task_context")

    def save_phase(self, phase: str) -> None:
        """Set phase and persist the session state."""
        self.state["phase"] = phase
        self.store.sessions.update(self.session_id, dict(self.state))


def no_dataset_hint() -> str:
    """Formatted list of discovered datasets + exact fresh-init commands."""
    datasets = sorted(p.parent.name for p in Path("datasets").glob("*/campaign.json"))
    lines = [f"  python -m promptpotter new {name}" for name in datasets]
    body = "\n".join(lines) if lines else "  (no datasets found under ./datasets/)"
    return "Available datasets:\n\n" + body


def load_session(args: argparse.Namespace) -> SessionCtx:
    """Load active session from disk."""
    from promptpotter.infrastructure.store import active_pointer_exists, read_active_pointer

    identity = default_identity(tenant_id=getattr(args, "tenant", None) or "default")
    if not active_pointer_exists(identity.tenant_id):
        raise SystemExit(
            "ERROR: No active session.\n\n"
            "To start a campaign, run `new` against a dataset:\n\n" + no_dataset_hint()
        )
    store = build_stores(identity)
    pointer_sid, pointer_cid, pointer_cyid = read_active_pointer(identity.tenant_id)
    session_id = getattr(args, "session", None) or pointer_sid
    if not session_id:
        raise SystemExit("ERROR: No active session_id in pointer.")

    state = store.sessions.read(session_id)
    if not state:
        raise SystemExit(f"ERROR: Session '{session_id}' not found.")

    campaign_id = pointer_cid or ""
    cycle_id = getattr(args, "cycle", None) or pointer_cyid or ""
    backend_id = state.get("init_params", {}).get("backend_id", "") or ""
    return SessionCtx(store, state, backend_id, session_id, campaign_id, cycle_id)


def load_campaign_config(config_path: str | None) -> dict[str, Any]:
    """Load campaign config JSON, unwrapping the optional outer ``campaign_config`` key."""
    if not config_path:
        return {}
    with open(config_path) as f:
        data = json.load(f)
    result: dict[str, Any] = data.get("campaign_config", data) or {}
    return result


__all__ = ["SessionCtx", "load_campaign_config", "load_session", "no_dataset_hint"]
