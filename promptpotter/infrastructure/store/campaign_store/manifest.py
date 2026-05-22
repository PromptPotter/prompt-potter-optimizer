"""Campaign-manifest CRUD — ``campaign.json`` + the session forest.

A campaign is a forest: it holds N *session* roots (one per ``new`` on
the same origin declaration) plus their fork descendants. The campaign
owns the frozen ``CampaignConfig`` snapshot and the forest's identity
(``campaign_id = {dataset}__{hash}``).
"""

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
        """Write the ``campaign.json`` manifest. The single config-snapshot writer."""
        path = self._manifest_path(campaign.campaign_id)
        write_json(path, campaign.model_dump(mode="json"))
        return path

    def update_campaign(self, campaign_id: str, updates: dict[str, Any]) -> None:
        """Merge *updates* into ``campaign.json`` and write back."""
        path = self._manifest_path(campaign_id)
        data = read_json(path)
        data.update(updates)
        write_json(path, data)

    def list_campaign_ids(self) -> list[str]:
        """Every campaign id on disk (dir with a ``campaign.json``), sorted."""
        campaigns_dir = self._base_dir / "campaigns"
        if not campaigns_dir.exists():
            return []
        return sorted(
            p.name
            for p in campaigns_dir.iterdir()
            if p.is_dir() and (p / "campaign.json").is_file()
        )

    def list_campaigns(self, dataset_name: str | None = None) -> list[Campaign]:
        """Every campaign manifest, optionally filtered to one dataset."""
        out: list[Campaign] = []
        for cid in self.list_campaign_ids():
            campaign = self.load_campaign(cid)
            if campaign is None:
                continue
            if dataset_name and campaign.dataset_name != dataset_name:
                continue
            out.append(campaign)
        return out

    def mark_campaign_finished(self, campaign_id: str, *, status: str, finished_at: str) -> None:
        """Stamp the terminal ``status`` + ``finished_at`` onto ``campaign.json``.

        ``status`` reflects the campaign's most-recent session — a fresh
        ``new`` reactivates the campaign (see ``auto_mint_session``)."""
        if self.load_campaign(campaign_id) is None:
            return
        self.update_campaign(campaign_id, {"status": status, "finished_at": finished_at})

    def list_sessions(self, campaign_id: str) -> list[str]:
        """Every session-root cycle id in the campaign's forest, ordered by
        session index.

        A session root is a cycle that is its own family root — no
        ``_fork_``/``_diag_``/``_sweep_`` separator. The bare ``cycle_{hash}``
        is session 1; ``cycle_{hash}_s{N}`` is the Nth ``new`` re-run of the
        same origin declaration.
        """
        cycles_dir = self.campaign_root_dir(campaign_id) / "cycles"
        if not cycles_dir.exists():
            return []
        roots = [
            p.name
            for p in cycles_dir.iterdir()
            if p.is_dir() and (p / "index.json").is_file() and root_cycle_id(p.name) == p.name
        ]
        return sorted(roots, key=session_index)

    def next_session_index(self, campaign_id: str) -> int:
        """Index for the next session minted into this campaign (1 if empty)."""
        sessions = self.list_sessions(campaign_id)
        if not sessions:
            return 1
        return max(session_index(s) for s in sessions) + 1
