"""CLI session-state plumbing — ``SessionCtx`` wraps the raw state dict with typed accessors."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.infrastructure.identity.migration import registered_or_default_identity
from promptpotter.infrastructure.store.stores import Stores, build_stores

if TYPE_CHECKING:
    from promptpotter.application.config import CampaignConfig


@dataclass
class SessionCtx:
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
        """The dataset's live ``campaign.json`` (edits picked up + drift-detected) with the
        per-campaign overlay (origin-floor values + param locks) re-applied from the frozen
        ``campaign.json::config`` — that overlay lives only on the snapshot, so a live-file
        rebuild would drop it. Session-state copy never consulted (may carry stale schema fields).

        Resolution is **tenant-first**, via the same ``readable_dataset_dir`` the runner
        uses: a tenant upload at ``projects/{tenant}/datasets/{slug}/`` before install
        content at ``datasets/{name}/``. Reading only the repo root meant every *ingested*
        dataset — the only kind a distributed install has — found no live file and resumed
        off the frozen snapshot instead, making it the config of record for exactly the
        campaigns whose snapshot is least likely to still validate."""
        from promptpotter.application.config import (
            apply_inherited_overlay,
        )
        from promptpotter.application.config import (
            load_campaign_config as validate_campaign_config,
        )
        from promptpotter.application.datasets.authored import read_campaign_config_file
        from promptpotter.infrastructure.store.dataset_access import (
            DatasetAccessError,
            readable_dataset_dir,
        )

        dataset_name = self.init_params.get("dataset_name") or ""
        raw: dict[str, Any] = {}
        if dataset_name:
            try:
                ds_dir = readable_dataset_dir(self.store, dataset_name)
            except DatasetAccessError:
                pass  # campaign outlives its dataset dir — resume off the snapshot
            else:
                raw = read_campaign_config_file(ds_dir / "campaign.json")
        config = validate_campaign_config(raw)
        campaign = (
            self.store.campaigns.load_campaign(self.campaign_id) if self.campaign_id else None
        )
        if campaign is not None:
            seed = self.store.campaigns.read_cycle_seed(self.campaign_id, self.cycle_id)
            config = apply_inherited_overlay(config, campaign.config, seed)
        return config

    @property
    def task_context(self) -> dict[str, Any] | None:
        return self.state.get("task_context")

    def save_phase(self, phase: str) -> None:
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
    from promptpotter.infrastructure.store.session_pointer import (
        active_pointer_exists,
        read_active_pointer,
    )

    # Same resolver as `identity_from_args` — explicit --tenant > registered
    # developer (claim marker) > anonymous default. Must match, else resume
    # reads one tenant's pointer but looks for the session in another's tree.
    identity = registered_or_default_identity(getattr(args, "tenant", None))
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


__all__ = ["SessionCtx", "load_session", "no_dataset_hint"]
