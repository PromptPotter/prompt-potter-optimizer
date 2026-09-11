from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.application.pipeline_resolve import resolve_campaign_config
from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT, benchmark_datasets_root
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.infrastructure.store.session_pointer import (
    active_pointer_exists,
    read_active_pointer,
)
from promptpotter.infrastructure.store.stores import Stores, build_stores
from promptpotter.presentation.cli.commands._shared import identity_from_args

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import CampaignConfig


@dataclass
class SessionCtx:
    store: Stores
    state: dict[str, Any]
    backend_id: str
    session_id: str
    campaign_id: str
    cycle_id: str

    @property
    def hop(self) -> CycleHop:
        """This context's cycle as the pair that addresses it. Derived, never stored: ``cycle_id`` is reassigned IN PLACE when a
        verb forks, so a stored copy names the pre-fork cycle from the moment it stopped being the one running."""
        return CycleHop(campaign_id=self.campaign_id, cycle_id=self.cycle_id)

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
        campaign = (
            self.store.campaigns.load_campaign(self.campaign_id) if self.campaign_id else None
        )
        if campaign is None:
            raise SystemExit(f"ERROR: campaign {self.campaign_id!r} has no manifest on disk.")
        return resolve_campaign_config(self.store, campaign, self.hop)

    @property
    def task_context(self) -> dict[str, Any] | None:
        return self.state.get("task_context")


def no_dataset_hint() -> str:
    root = benchmark_datasets_root()
    datasets = sorted(p.parent.name for p in root.glob("*/campaign.yaml"))
    lines = [f"  python -m promptpotter new {name}" for name in datasets]
    body = "\n".join(lines) if lines else f"  (no datasets found under {root})"
    return "Available datasets:\n\n" + body


def load_session(args: argparse.Namespace) -> SessionCtx:

    # THE resolver, not a copy of it — a comment asserting "same resolver as
    # `identity_from_args`" sat here instead, and a copy that must match is a copy that
    # can stop matching: resume would then read one tenant's pointer and look for the
    # session in another's tree. Imported here, not at module scope, because `_shared`
    # imports `SessionCtx` from this module.
    identity = identity_from_args(args)
    store = build_stores(identity, projects_root=DEFAULT_PROJECTS_ROOT)
    if not active_pointer_exists(store.base_dir):
        raise SystemExit(
            "ERROR: No active session.\n\n"
            "To start a campaign, run `new` against a dataset:\n\n" + no_dataset_hint()
        )
    pointer_sid, pointer_cid, pointer_cyid = read_active_pointer(store.base_dir)
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


__all__ = ["SessionCtx", "load_session", "no_dataset_hint"]
