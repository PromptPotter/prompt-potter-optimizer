"""Durable check-in working-state under ``campaigns/{campaign_id}/checkin/``.

Replaces the restart-wiped in-memory ``DraftCampaignRegistry`` + the
``datasets/.drafts/{id}/cache.json`` bank: an in-progress origin authoring lives
under the campaign it belongs to, so a check-in survives a server restart and is
resumable from disk like any other campaign. Two files:

  * ``draft.json`` — the lossless ``DraftCampaign.to_disk()`` dict (every gated
    field + provenance). The readiness gate at Start rehydrates from this.
  * ``cache.json`` — the pre-commit sample bank (raw header-keyed rows + the
    ``resolution`` breadcrumb), same shape the old draft cache wrote. Start reads
    it and writes ``datasets/{slug}/`` fresh; nothing under ``campaigns/{id}/``
    ever moves.

The ``checkin/`` subdir is invisible to the ``campaigns/*/cycles/*/index.json``
cycle scan, so it never pollutes cycle/campaign enumeration. Tenant scope is by
path — the store is rooted at the tenant's ``base_dir``, so a cross-tenant
``campaign_id`` simply isn't found. Stores dicts, not ``DraftCampaign``: the
dataclass conversion (``to_disk`` / ``from_disk``) happens in the application
caller, keeping this leaf free of application/domain imports.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.infrastructure.store.io import (
    read_json_optional,
    write_json,
)
from promptpotter.infrastructure.store.paths import campaign_root_dir_for
from promptpotter.shared.clock import utcnow_iso

if TYPE_CHECKING:
    from promptpotter.domain.sample import Sample


class CheckinDraftStore:
    """Reads + writes the check-in working-state under ``campaigns/{id}/checkin/``."""

    def __init__(self, base_dir: Path):
        self._base_dir = base_dir

    def _checkin_dir(self, campaign_id: str) -> Path:
        return campaign_root_dir_for(self._base_dir, campaign_id) / "checkin"

    # -- draft.json (lossless DraftCampaign dict) -----------------------------

    def write_draft(self, campaign_id: str, draft: dict[str, Any]) -> Path:
        """Persist the lossless ``DraftCampaign.to_disk()`` dict."""
        path = self._checkin_dir(campaign_id) / "draft.json"
        write_json(path, draft)
        return path

    def read_draft(self, campaign_id: str) -> dict[str, Any] | None:
        """Load the stored draft dict, or ``None`` when this campaign has no check-in."""
        return read_json_optional(self._checkin_dir(campaign_id) / "draft.json")

    # -- cache.json (pre-commit sample bank) ----------------------------------

    def write_bank(
        self,
        campaign_id: str,
        items: Sequence[Sample | dict[str, Any]],
        *,
        source_file: str = "",
        headers: Sequence[str] = (),
    ) -> Path:
        """Persist the parsed sample bank to ``checkin/cache.json``.

        On ingest ``items`` are the raw header-keyed rows (the column mapping
        isn't confirmed yet); ``headers`` records the column order. A prior
        ``resolution`` block (provenance + gaps) is preserved across a rewrite.
        On Start the launcher rewrites this with materialized ``Sample`` rows and
        hands them to ``write_committed_dataset``, which writes ``datasets/{slug}/``
        fresh — this dir stays put as the check-in's audit breadcrumb.
        """
        from promptpotter.domain.sample import Sample

        path = self._checkin_dir(campaign_id) / "cache.json"
        prior = read_json_optional(path) or {}
        serialized = [item.model_dump() if isinstance(item, Sample) else item for item in items]
        data: dict[str, Any] = {
            "name": campaign_id,
            "created_at": utcnow_iso(),
            "source_file": source_file,
            "headers": list(headers),
            "row_count": len(serialized),
            "items": serialized,
        }
        if "resolution" in prior:
            data["resolution"] = prior["resolution"]
        write_json(path, data)
        return path

    def load_bank(self, campaign_id: str) -> dict[str, Any] | None:
        """Read the parsed bank, or ``None`` when this campaign has no check-in."""
        return read_json_optional(self._checkin_dir(campaign_id) / "cache.json")

    def write_resolution(self, campaign_id: str, resolution: dict[str, Any]) -> None:
        """Patch the bank's ``resolution`` block (per-field provenance + gaps) — the
        AI-on-disk breadcrumb showing what blocks mint. No-op when no bank exists."""
        path = self._checkin_dir(campaign_id) / "cache.json"
        data = read_json_optional(path)
        if data is None:
            return
        data["resolution"] = resolution
        write_json(path, data)


__all__ = ["CheckinDraftStore"]
