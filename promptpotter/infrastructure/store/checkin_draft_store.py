"""Durable check-in working-state under the campaign it belongs to, so authoring survives a restart. The ``checkin/``
subdir is invisible to the cycle scan; it stores DICTS, which keeps this leaf free of application/domain imports."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.domain.cycle_paths import WorkspaceDir
from promptpotter.infrastructure.store.io import (
    read_json_optional,
    write_json,
)
from promptpotter.infrastructure.store.layout import campaign_root_dir_for
from promptpotter.shared.clock import utcnow_iso

if TYPE_CHECKING:
    from promptpotter.domain.sample import Sample


class CheckinDraftStore:
    def __init__(self, base_dir: WorkspaceDir):
        self._base_dir = base_dir

    def _checkin_dir(self, campaign_id: str) -> Path:
        return campaign_root_dir_for(self._base_dir, campaign_id) / "checkin"

    # -- draft.json (lossless DraftCampaign dict) -----------------------------

    def write_draft(self, campaign_id: str, draft: dict[str, Any]) -> Path:
        path = self._checkin_dir(campaign_id) / "draft.json"
        write_json(path, draft)
        return path

    def read_draft(self, campaign_id: str) -> dict[str, Any] | None:
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
        """Persist the parsed sample bank. On ingest ``items`` are RAW header-keyed rows (the mapping isn't confirmed yet); a prior
        ``resolution`` block survives a rewrite. Start rewrites this with materialized rows and leaves it as the breadcrumb."""
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
