"""Round + candidate detail-file CRUD under a cycle dir.

``rounds/round_{N:04d}.json`` is the public round detail; the candidate
files under ``.runtime/cache/candidates/`` are the mid-round checkpoint.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from promptpotter.infrastructure.store.base import (
    read_json,
    read_json_optional,
    validate_path_component,
    write_json,
)
from promptpotter.infrastructure.store.campaign_store._kernel import CampaignStoreKernel
from promptpotter.infrastructure.store.campaign_store.index_helpers import round_summary

logger = logging.getLogger(__name__)


class RoundMixin(CampaignStoreKernel):
    """Round detail + candidate checkpoint file CRUD."""

    def save_round_file(
        self,
        campaign_id: str,
        cycle_id: str,
        round_data: dict[str, Any],
    ) -> Path:
        """Persist a round_data detail file and update the cycle index."""
        round_id = round_data["round_id"]
        validate_path_component(round_id)
        round_num = round_data.get("round", 0)

        detail_path = self._rounds_dir(campaign_id, cycle_id) / f"round_{round_num:04d}.json"
        write_json(detail_path, round_data)

        index_path = self._index_path(campaign_id, cycle_id)
        data = read_json(index_path)

        data["rounds"] = [t for t in data["rounds"] if t.get("round") != round_num]
        data["rounds"].append(round_summary(round_data))
        data["n_rounds"] = len(data["rounds"])

        if round_data["accuracy"] > data.get("best_accuracy", 0.0):
            data["best_accuracy"] = round_data["accuracy"]
            data["best_round_id"] = round_id

        data["updated_at"] = datetime.now(UTC).isoformat()
        write_json(index_path, data)

        return detail_path

    def load_round_file(
        self,
        campaign_id: str,
        cycle_id: str,
        round_num: int,
    ) -> dict[str, Any] | None:
        """Load a round_data detail by round number. ``None`` if not found."""
        return read_json_optional(
            self._rounds_dir(campaign_id, cycle_id) / f"round_{round_num:04d}.json",
        )

    def load_rounds_range(
        self,
        campaign_id: str,
        cycle_id: str,
        start: int,
        end: int,
    ) -> list[dict[str, Any]]:
        """Load rounds ``start..end`` inclusive, in round order. Missing rounds skipped."""
        out: list[dict[str, Any]] = []
        for r in range(start, end + 1):
            round_data = self.load_round_file(campaign_id, cycle_id, r)
            if round_data is not None:
                out.append(round_data)
        return out

    def save_round_candidates(
        self,
        campaign_id: str,
        cycle_id: str,
        round_num: int,
        candidates: list[dict[str, Any]],
    ) -> None:
        """Persist generated candidates before scoring (mid-round checkpoint)."""
        path = self._candidates_dir(campaign_id, cycle_id) / f"round_{round_num:04d}.json"
        write_json(path, candidates)
        logger.debug("Saved %d candidates for round %d → %s", len(candidates), round_num, path.name)

    def load_round_candidates(
        self,
        campaign_id: str,
        cycle_id: str,
        round_num: int,
    ) -> list[dict[str, Any]] | None:
        """Load persisted candidates for a round. ``None`` if not on disk."""
        return read_json_optional(
            self._candidates_dir(campaign_id, cycle_id) / f"round_{round_num:04d}.json",
        )

    def delete_round_candidates(
        self,
        campaign_id: str,
        cycle_id: str,
        round_num: int,
    ) -> None:
        """Delete persisted candidates for a round (forces fresh generation)."""
        path = self._candidates_dir(campaign_id, cycle_id) / f"round_{round_num:04d}.json"
        if path.exists():
            path.unlink()
            logger.debug(
                "Deleted cached candidates for round %d (escalation invalidation)", round_num
            )
