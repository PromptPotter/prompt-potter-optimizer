"""Per-batch sweep artifacts at ``campaigns/{campaign_id}/sweeps/{batch_id}/``.

Batch-level (not cycle-level): one ``index.json`` + ``summary.md`` spans many
sweep-fork cycles (which live flat under ``cycles/``). Keyed by
``(campaign_id, batch_id)``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from promptpotter.domain.cycle_paths import WorkspaceDir
from promptpotter.infrastructure.store.io import (
    read_json,
    read_json_optional,
    write_json,
    write_text,
)
from promptpotter.infrastructure.store.layout import sweep_batch_dir_for

logger = logging.getLogger(__name__)


class SweepStore:
    def __init__(self, base_dir: WorkspaceDir) -> None:
        self._base_dir = base_dir  # tenant root

    def batch_dir(self, campaign_id: str, batch_id: str) -> Path:
        return sweep_batch_dir_for(self._base_dir, campaign_id, batch_id)

    def init_batch(
        self,
        campaign_id: str,
        batch_id: str,
        *,
        parent_cycle_id: str,
        started_at: str,
        payloads: list[dict[str, str]],
    ) -> Path:
        """Write the running batch index. ``payloads`` is the per-source dispatch list."""
        path = self.batch_dir(campaign_id, batch_id) / "index.json"
        write_json(
            path,
            {
                "batch_id": batch_id,
                "parent_cycle_id": parent_cycle_id,
                "campaign_id": campaign_id,
                "started_at": started_at,
                "status": "running",
                "payloads": payloads,
            },
        )
        return path

    def finalize_batch(
        self,
        campaign_id: str,
        batch_id: str,
        *,
        completed_at: str,
        status_by_source: dict[str, str],
        cycle_by_source: dict[str, str],
    ) -> Path:
        path = self.batch_dir(campaign_id, batch_id) / "index.json"
        index = read_json(path)
        index["status"] = "completed"
        index["completed_at"] = completed_at
        for entry in index["payloads"]:
            if entry["status"] != "pending":
                continue
            cid = cycle_by_source.get(entry["source_file"], "")
            if cid:
                # Per-payload status is a StopOutcome value (domain/phases.py).
                entry["status"] = status_by_source.get(entry["source_file"], "success")
            else:
                entry["status"] = "skipped"
            entry["cycle_id"] = cid
        write_json(path, index)
        return path

    def write_summary_md(
        self,
        campaign_id: str,
        batch_id: str,
        content: str,
    ) -> Path:
        path = self.batch_dir(campaign_id, batch_id) / "summary.md"
        write_text(path, content)
        return path

    def load_batch(self, campaign_id: str, batch_id: str) -> dict[str, Any] | None:
        return read_json_optional(self.batch_dir(campaign_id, batch_id) / "index.json")


__all__ = ["SweepStore"]
