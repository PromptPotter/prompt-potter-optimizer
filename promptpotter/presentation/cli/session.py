from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.application.pipeline_resolve import resolve_campaign_config
from promptpotter.config.paths import benchmark_datasets_root
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.infrastructure.store.stores import Stores

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


def load_session(store: Stores, hop: CycleHop) -> SessionCtx:
    """The session *hop* was minted under (``index.json::parent_session_id``), the same read the web
    launch makes. A verb that minted passes its own hop: the pointer is rewritten by every mint."""
    session_id = str((store.campaigns.load(hop) or {}).get("parent_session_id") or "")
    if not session_id:
        raise SystemExit(f"ERROR: cycle {hop.cycle_id!r} in {hop.campaign_id!r} names no session.")

    state = store.sessions.read(session_id)
    if not state:
        raise SystemExit(f"ERROR: Session '{session_id}' not found.")

    backend_id = state.get("init_params", {}).get("backend_id", "") or ""
    return SessionCtx(store, state, backend_id, session_id, hop.campaign_id, hop.cycle_id)


__all__ = ["SessionCtx", "load_session", "no_dataset_hint"]
