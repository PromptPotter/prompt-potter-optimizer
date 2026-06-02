"""Campaign + cycle artifacts under ``campaigns/{campaign_id}/`` — composed from four mixins."""

from __future__ import annotations

from promptpotter.infrastructure.store.campaign_store.cycles import CycleIndexMixin
from promptpotter.infrastructure.store.campaign_store.forks import ForkMixin
from promptpotter.infrastructure.store.campaign_store.manifest import CampaignManifestMixin
from promptpotter.infrastructure.store.campaign_store.overrides import CycleOverrideMixin
from promptpotter.infrastructure.store.campaign_store.rounds import RoundMixin


class CampaignStore(
    CampaignManifestMixin, CycleIndexMixin, ForkMixin, RoundMixin, CycleOverrideMixin
):
    pass


__all__ = ["CampaignStore"]
