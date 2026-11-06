from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from promptpotter.application.pipeline_resolve import cycle_config
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.shared.errors import NotFoundError

if TYPE_CHECKING:
    from promptpotter.domain.campaign import Campaign
    from promptpotter.domain.campaign_config import CampaignConfig
    from promptpotter.infrastructure.store.stores import Stores


@dataclass
class SessionCtx:
    store: Stores
    campaign: Campaign
    cycle_id: str

    @property
    def campaign_id(self) -> str:
        return self.campaign.campaign_id

    @property
    def hop(self) -> CycleHop:
        """Derived, never stored: ``cycle_id`` is reassigned IN PLACE when a verb forks."""
        return CycleHop(campaign_id=self.campaign_id, cycle_id=self.cycle_id)

    @property
    def campaign_config(self) -> CampaignConfig:
        return cycle_config(self.store, self.campaign, self.hop).config


def load_session(store: Stores, hop: CycleHop) -> SessionCtx:
    campaign = store.campaigns.load_campaign(hop.campaign_id)
    if campaign is None:
        refused = NotFoundError(f"campaign manifest not found for '{hop.campaign_id}'.")
        refused.add_note("Run `python -m promptpotter new <dataset>` to mint a fresh campaign.")
        raise refused
    return SessionCtx(store, campaign, hop.cycle_id)
