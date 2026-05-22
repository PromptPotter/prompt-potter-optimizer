"""Shared kernel for the ``CampaignStore`` mixins.

Path resolution under ``campaigns/{campaign_id}/`` plus the
campaign/cycle reads used across more than one mixin (``load_campaign``,
the ``index.json`` enumeration helpers). Every mixin inherits this kernel
so the four method groups stay decoupled — they call only their own
methods and this shared surface.
"""

from __future__ import annotations

from pathlib import Path

from promptpotter.domain.campaign import Campaign
from promptpotter.infrastructure.store.base import EntityStore, read_json_optional
from promptpotter.infrastructure.store.paths import campaign_root_dir_for, cycle_dir_for


class CampaignStoreKernel(EntityStore):
    """Path helpers + cross-mixin reads for the ``campaigns/`` tree."""

    def __init__(self, base_dir: Path):
        # base_dir is tenant root; campaigns nest directly under it
        super().__init__(base_dir, "campaigns")

    # -- path helpers ---------------------------------------------------------

    def campaign_root_dir(self, campaign_id: str) -> Path:
        """Campaign dir — ``campaign.json`` / ``log.md`` / ``cycles/`` (the forest)."""
        return campaign_root_dir_for(self._base_dir, campaign_id)

    def cycle_dir(self, campaign_id: str, cycle_id: str) -> Path:
        """Per-cycle dir — ``campaigns/{campaign_id}/cycles/{cycle_id}``."""
        return cycle_dir_for(self._base_dir, campaign_id, cycle_id)

    def _manifest_path(self, campaign_id: str) -> Path:
        return self.campaign_root_dir(campaign_id) / "campaign.json"

    def _index_path(self, campaign_id: str, cycle_id: str) -> Path:
        return self.cycle_dir(campaign_id, cycle_id) / "index.json"

    def _rounds_dir(self, campaign_id: str, cycle_id: str) -> Path:
        return self.cycle_dir(campaign_id, cycle_id) / "rounds"

    def _candidates_dir(self, campaign_id: str, cycle_id: str) -> Path:
        return self.cycle_dir(campaign_id, cycle_id) / ".runtime" / "cache" / "candidates"

    # -- cross-mixin reads ----------------------------------------------------

    def load_campaign(self, campaign_id: str) -> Campaign | None:
        """Load the ``campaign.json`` manifest; ``None`` when absent."""
        data = read_json_optional(self._manifest_path(campaign_id))
        if data is None:
            return None
        return Campaign.model_validate(data)

    def _index_files(self) -> list[Path]:
        """Every cycle ``index.json`` under this tenant — ``campaigns/*/cycles/*/index.json``."""
        campaigns_dir = self._base_dir / "campaigns"
        if not campaigns_dir.exists():
            return []
        return sorted(campaigns_dir.glob("*/cycles/*/index.json"))

    @staticmethod
    def _ids_from_index_path(index_path: Path) -> tuple[str, str]:
        """``(campaign_id, cycle_id)`` for a ``campaigns/{c}/cycles/{cy}/index.json`` path."""
        cycle_id = index_path.parent.name
        campaign_id = index_path.parent.parent.parent.name
        return campaign_id, cycle_id
