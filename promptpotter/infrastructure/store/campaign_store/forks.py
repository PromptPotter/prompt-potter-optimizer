"""Fork-sibling ``index.json`` writers — divergence / diag / sweep.

Each writer mints a sibling cycle's ``index.json`` within the parent's
campaign; ``copy_parent_rounds_and_candidates`` carries the inherited
round/candidate detail files forward.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from promptpotter.infrastructure.store.base import read_json_optional, write_json
from promptpotter.infrastructure.store.campaign_store._kernel import CampaignStoreKernel
from promptpotter.infrastructure.store.campaign_store.index_helpers import fresh_sibling_index_blob


class ForkMixin(CampaignStoreKernel):
    """Divergence / diag / sweep fork ``index.json`` writers."""

    def save_divergence_fork(
        self,
        campaign_id: str,
        parent_cycle_id: str,
        new_cycle_id: str,
        *,
        surviving_rounds: list[dict[str, Any]],
        forked_at: str,
        forked_from_round: int,
    ) -> Path:
        """Divergence-fork ``index.json`` inheriting parent state (same campaign)."""
        parent_index = read_json_optional(self._index_path(campaign_id, parent_cycle_id)) or {}
        best_acc = max(
            (float(t.get("accuracy", 0.0)) for t in surviving_rounds),
            default=0.0,
        )
        best_round_id = next(
            (
                t.get("round_id")
                for t in surviving_rounds
                if float(t.get("accuracy", 0.0)) == best_acc
            ),
            None,
        )
        index = {
            **parent_index,
            "parent_cycle_id": parent_cycle_id,
            "sibling_kind": "fork",
            "forked_from_round": forked_from_round,
            "forked_at": forked_at,
            "rounds": list(surviving_rounds),
            "n_rounds": len(surviving_rounds),
            "best_accuracy": best_acc,
            "best_round_id": best_round_id,
            "status": "resumed",
            "updated_at": forked_at,
        }
        index.pop("cycle_id", None)
        path = self._index_path(campaign_id, new_cycle_id)
        write_json(path, index)
        return path

    def save_diag_fork(
        self,
        campaign_id: str,
        parent_cycle_id: str,
        new_cycle_id: str,
        *,
        forked_at: str,
    ) -> Path:
        """Clean-slate diag-sibling ``index.json``."""
        parent_index = read_json_optional(self._index_path(campaign_id, parent_cycle_id)) or {}
        blob = fresh_sibling_index_blob(parent_index, parent_cycle_id, "diag", forked_at)
        path = self._index_path(campaign_id, new_cycle_id)
        write_json(path, blob)
        return path

    def save_sweep_fork(
        self,
        campaign_id: str,
        parent_cycle_id: str,
        new_cycle_id: str,
        *,
        sweep_batch_id: str,
        forked_at: str,
    ) -> Path:
        """Clean-slate sweep-fork ``index.json`` carrying ``sweep_batch_id``."""
        parent_index = read_json_optional(self._index_path(campaign_id, parent_cycle_id)) or {}
        blob = fresh_sibling_index_blob(
            parent_index,
            parent_cycle_id,
            "sweep",
            forked_at,
            sweep_batch_id=sweep_batch_id,
        )
        path = self._index_path(campaign_id, new_cycle_id)
        write_json(path, blob)
        return path

    def copy_parent_rounds_and_candidates(
        self,
        campaign_id: str,
        parent_cycle_id: str,
        new_cycle_id: str,
        *,
        before_round: int,
    ) -> int:
        """Copy parent's ``rounds/`` + ``candidates/`` files for rounds < ``before_round``."""
        copy_specs: tuple[tuple[Path, Path, str], ...] = (
            (
                self._rounds_dir(campaign_id, parent_cycle_id),
                self._rounds_dir(campaign_id, new_cycle_id),
                "round_",
            ),
            (
                self._candidates_dir(campaign_id, parent_cycle_id),
                self._candidates_dir(campaign_id, new_cycle_id),
                "round_",
            ),
        )
        n_copied = 0
        for src, dst, prefix in copy_specs:
            if not src.exists():
                continue
            dst.mkdir(parents=True, exist_ok=True)
            for p in sorted(src.glob(f"{prefix}*.json")):
                try:
                    n = int(p.stem.removeprefix(prefix))
                except ValueError:
                    continue
                if n < before_round:
                    shutil.copyfile(p, dst / p.name)
                    n_copied += 1
        return n_copied
