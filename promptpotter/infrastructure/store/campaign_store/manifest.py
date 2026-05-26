"""Campaign-manifest CRUD — ``campaign.json`` + the cycle tree."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from promptpotter.domain.campaign import Campaign
from promptpotter.infrastructure.store.base import read_json, write_json
from promptpotter.infrastructure.store.campaign_store._kernel import CampaignStoreKernel
from promptpotter.infrastructure.store.paths import root_cycle_id, session_index


class CampaignManifestMixin(CampaignStoreKernel):
    """``campaign.json`` manifest CRUD + session-root enumeration."""

    def create_campaign(self, campaign: Campaign) -> Path:
        """Write ``campaign.json``; the single config-snapshot writer."""
        path = self._manifest_path(campaign.campaign_id)
        write_json(path, campaign.model_dump(mode="json"))
        return path

    def update_campaign(self, campaign_id: str, updates: dict[str, Any]) -> None:
        path = self._manifest_path(campaign_id)
        data = read_json(path)
        data.update(updates)
        write_json(path, data)

    def list_campaign_ids(self) -> list[str]:
        """Every campaign id on disk (dir with ``campaign.json``), sorted."""
        campaigns_dir = self._base_dir / "campaigns"
        if not campaigns_dir.exists():
            return []
        return sorted(
            p.name
            for p in campaigns_dir.iterdir()
            if p.is_dir() and (p / "campaign.json").is_file()
        )

    def list_campaigns(
        self,
        dataset_name: str | None = None,
        *,
        lifecycle: str = "active",
        owner_user_id: str | None = None,
    ) -> list[Campaign]:
        """Campaigns matching the filter.

        *lifecycle* — one of ``"active"`` / ``"archived"`` / ``"deleted"`` /
        ``"all"``; defaults to ``"active"`` so archived + deleted campaigns
        drop out of the default sidebar. The store is the sole filter
        gateway; API + CLI pass through.

        *owner_user_id* — when set, only campaigns owned by this user are
        returned. Unset means no owner filter (the in-process tenant scope
        is still enforced by ``Stores.identity``).
        """
        out: list[Campaign] = []
        for cid in self.list_campaign_ids():
            campaign = self.load_campaign(cid)
            if campaign is None:
                continue
            if dataset_name and campaign.dataset_name != dataset_name:
                continue
            if lifecycle != "all" and campaign.lifecycle_status != lifecycle:
                continue
            if owner_user_id is not None and campaign.owner_user_id != owner_user_id:
                continue
            out.append(campaign)
        return out

    def mark_campaign_finished(self, campaign_id: str, *, status: str, finished_at: str) -> None:
        if self.load_campaign(campaign_id) is None:
            return
        self.update_campaign(campaign_id, {"status": status, "finished_at": finished_at})

    def mark_campaign_lifecycle(
        self,
        campaign_id: str,
        *,
        lifecycle_status: str,
        lifecycle_changed_at: str,
        lifecycle_reason: str = "",
    ) -> None:
        """Soft-mark a campaign as ``archived`` / ``deleted`` / ``active``.

        Never physically removes data — measurements survive so siblings
        still cache-hit per ADR-0002 §0.5. ``unarchive`` flips back to
        ``"active"``.
        """
        if self.load_campaign(campaign_id) is None:
            return
        self.update_campaign(
            campaign_id,
            {
                "lifecycle_status": lifecycle_status,
                "lifecycle_changed_at": lifecycle_changed_at,
                "lifecycle_reason": lifecycle_reason,
            },
        )

    def list_sessions(self, campaign_id: str) -> list[str]:
        """Session-root cycle ids in the campaign tree, ordered by ``session_index``."""
        cycles_dir = self.campaign_root_dir(campaign_id) / "cycles"
        if not cycles_dir.exists():
            return []
        roots = [
            p.name
            for p in cycles_dir.iterdir()
            if p.is_dir() and (p / "index.json").is_file() and root_cycle_id(p.name) == p.name
        ]
        return sorted(roots, key=session_index)
