"""Per-batch sweep artifacts under ``campaigns/{root}/sweeps/{batch_id}/``.

Sweeps are batch-level, not cycle-level: one ``index.json`` + ``summary.md``
spans many forked cycles. Keyed by ``(family_root, batch_id)`` rather than
``(backend_id, entity_id)`` — not an ``EntityStore``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from promptpotter.infrastructure.store.base import (
    read_json,
    read_json_optional,
    write_json,
    write_text,
)
from promptpotter.infrastructure.store.paths import sweep_batch_dir_for

logger = logging.getLogger(__name__)


class SweepStore:
    """File I/O for sweep-batch artifacts (``index.json`` + ``summary.md``)."""

    def __init__(self, base_dir: Path) -> None:
        # base_dir is tenant root; sweeps nest under campaigns/{root}/sweeps/{batch_id}/.
        self._base_dir = base_dir

    def batch_dir(self, family_root: str, batch_id: str) -> Path:
        return sweep_batch_dir_for(self._base_dir, family_root, batch_id)

    def init_batch(
        self,
        family_root: str,
        batch_id: str,
        *,
        parent_cycle_id: str,
        started_at: str,
        payloads: list[dict[str, str]],
    ) -> Path:
        """Write the running batch index. ``payloads`` is the per-source dispatch list."""
        path = self.batch_dir(family_root, batch_id) / "index.json"
        write_json(
            path,
            {
                "batch_id": batch_id,
                "parent_cycle_id": parent_cycle_id,
                "family_root": family_root,
                "started_at": started_at,
                "status": "running",
                "payloads": payloads,
            },
        )
        return path

    def finalize_batch(
        self,
        family_root: str,
        batch_id: str,
        *,
        completed_at: str,
        status_by_source: dict[str, str],
        cycle_by_source: dict[str, str],
    ) -> Path:
        """Read the running index, mark complete, fill per-payload status+cycle, write back."""
        path = self.batch_dir(family_root, batch_id) / "index.json"
        index = read_json(path)
        index["status"] = "completed"
        index["completed_at"] = completed_at
        for entry in index["payloads"]:
            if entry["status"] != "pending":
                continue
            cid = cycle_by_source.get(entry["source_file"], "")
            if cid:
                entry["status"] = status_by_source.get(entry["source_file"], "completed")
            else:
                entry["status"] = "skipped"
            entry["cycle_id"] = cid
        write_json(path, index)
        return path

    def write_summary_md(
        self,
        family_root: str,
        batch_id: str,
        content: str,
    ) -> Path:
        """Write the pre-rendered batch summary markdown."""
        path = self.batch_dir(family_root, batch_id) / "summary.md"
        write_text(path, content)
        return path

    def load_batch(self, family_root: str, batch_id: str) -> dict[str, Any] | None:
        """Read the batch index; ``None`` if it doesn't exist."""
        return read_json_optional(self.batch_dir(family_root, batch_id) / "index.json")


__all__ = ["SweepStore"]
