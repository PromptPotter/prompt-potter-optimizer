"""CLI session state plumbing — load and type-access session state.

``SessionCtx`` wraps the raw state dict that ``Stores.sessions`` reads
from disk, exposing typed accessors so command bodies don't have to
string-index every field.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from promptpotter.infrastructure.store import Stores, build_stores

if TYPE_CHECKING:
    from promptpotter.application.campaign.config import CampaignConfig


@dataclass
class SessionCtx:
    """Loaded session state with typed accessors over the raw state dict."""

    store: Stores
    state: dict
    backend_id: str
    session_id: str

    @property
    def init_params(self) -> dict:
        return self.state["init_params"]

    @property
    def backend_url(self) -> str:
        return self.init_params["backend_url"]

    @property
    def campaign_config(self) -> CampaignConfig:
        return cast("CampaignConfig", self.state["campaign_config"])

    @property
    def task_context(self) -> dict | None:
        return self.state.get("task_context")

    @property
    def recon_variants(self) -> dict:
        return self.state.get("recon_variants") or {}

    def save_phase(self, phase: str, *, log: str = "") -> None:
        """Set phase, persist state, optionally append log entry."""
        self.state["phase"] = phase
        self.store.sessions.save(self.backend_id, self.session_id, self.state)
        if log:
            self.store.sessions.append_log(self.backend_id, self.session_id, log)


def load_session(args: argparse.Namespace) -> SessionCtx:
    """Load active session from disk."""
    store = build_stores()
    state, backend_id, session_id = store.sessions.load_active(args.session)
    return SessionCtx(store, state, backend_id, session_id)


def load_campaign_config(config_path: str | None) -> dict:
    """Load campaign config JSON, unwrapping the optional outer ``campaign_config`` key."""
    if not config_path:
        return {}
    with open(config_path) as f:
        data = json.load(f)
    return data.get("campaign_config", data) or {}
