"""
Campaign registry persistence.

Stores campaign metadata and per-trial results to disk:

    {backend_id}/campaigns/{campaign_id}.json        — metadata + trial index
    {backend_id}/campaigns/{campaign_id}/trial_NNNN.json  — trial detail
"""
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from api.services.stores.base import read_json, validate_path_component, write_json

logger = logging.getLogger(__name__)


class CampaignStore:
    """File I/O for campaign registry persistence."""

    def __init__(self, base_dir: Path):
        self._base_dir = base_dir

    def _campaigns_dir(self, backend_id: str) -> Path:
        validate_path_component(backend_id)
        return self._base_dir / backend_id / "campaigns"

    def _campaign_path(self, backend_id: str, campaign_id: str) -> Path:
        validate_path_component(campaign_id)
        return self._campaigns_dir(backend_id) / f"{campaign_id}.json"

    def _trial_dir(self, backend_id: str, campaign_id: str) -> Path:
        validate_path_component(campaign_id)
        return self._campaigns_dir(backend_id) / campaign_id

    # -- Campaign CRUD --

    def create(
        self, backend_id: str, campaign_id: str, metadata: dict[str, Any],
    ) -> Path:
        """Create a new campaign file with initial metadata."""
        path = self._campaign_path(backend_id, campaign_id)
        if path.exists():
            raise FileExistsError(f"Campaign already exists: {campaign_id}")
        now = datetime.now(timezone.utc).isoformat()
        data = {
            "campaign_id": campaign_id,
            "created_at": now,
            "updated_at": now,
            "status": "active",
            "n_trials": 0,
            "best_accuracy": 0.0,
            "best_trial_id": None,
            "baseline_accuracy": 0.0,
            "trials": [],
            **metadata,
        }
        write_json(path, data)
        return path

    def load(
        self, backend_id: str, campaign_id: str,
    ) -> dict[str, Any] | None:
        """Load campaign metadata + trial index.  Returns None if not found."""
        path = self._campaign_path(backend_id, campaign_id)
        if not path.exists():
            return None
        return read_json(path)

    def update(
        self, backend_id: str, campaign_id: str, updates: dict[str, Any],
    ) -> None:
        """Merge *updates* into the campaign file and write back."""
        path = self._campaign_path(backend_id, campaign_id)
        data = read_json(path)
        data.update(updates)
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        write_json(path, data)

    def list_all(self, backend_id: str) -> list[dict[str, Any]]:
        """Return summary for every campaign under *backend_id*."""
        campaigns_dir = self._campaigns_dir(backend_id)
        if not campaigns_dir.exists():
            return []
        results = []
        for path in sorted(
            p for pattern in ("campaign_*.json", "cycle_*.json")
            for p in campaigns_dir.glob(pattern)
        ):
            data = read_json(path)
            results.append({
                "campaign_id": data.get("campaign_id", path.stem),
                "name": data.get("name", ""),
                "status": data.get("status", "unknown"),
                "n_trials": data.get("n_trials", 0),
                "best_accuracy": data.get("best_accuracy", 0.0),
                "baseline_accuracy": data.get("baseline_accuracy", 0.0),
                "created_at": data.get("created_at", ""),
                "updated_at": data.get("updated_at", ""),
            })
        return results

    # -- Trial CRUD --

    def add_trial(
        self,
        backend_id: str,
        campaign_id: str,
        trial: dict[str, Any],
    ) -> Path:
        """Persist a trial detail file and update the campaign index."""
        trial_id = trial["trial_id"]
        validate_path_component(trial_id)
        round_num = trial.get("round", 0)

        # Write trial detail
        detail_path = (
            self._trial_dir(backend_id, campaign_id)
            / f"trial_{round_num:04d}.json"
        )
        write_json(detail_path, trial)

        # Update campaign index
        campaign_path = self._campaign_path(backend_id, campaign_id)
        data = read_json(campaign_path)

        summary = {
            "trial_id": trial_id,
            "round": round_num,
            "label": trial.get("label", ""),
            "prompt_state_id": trial.get("prompt_state_id", ""),
            "accuracy": trial.get("accuracy", 0.0),
            "hits": trial.get("hits", 0),
            "total": trial.get("total", 0),
            "improved": trial.get("improved", False),
            "created_at": trial.get("created_at", ""),
        }
        data["trials"].append(summary)
        data["n_trials"] = len(data["trials"])

        # Track best
        if trial["accuracy"] > data.get("best_accuracy", 0.0):
            data["best_accuracy"] = trial["accuracy"]
            data["best_trial_id"] = trial_id

        # Baseline is always round 0
        if round_num == 0:
            data["baseline_accuracy"] = trial["accuracy"]

        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        write_json(campaign_path, data)

        return detail_path

    def load_trial(
        self,
        backend_id: str,
        campaign_id: str,
        round_num: int,
    ) -> dict[str, Any] | None:
        """Load a trial detail by round number.  Returns None if not found."""
        path = (
            self._trial_dir(backend_id, campaign_id)
            / f"trial_{round_num:04d}.json"
        )
        if not path.exists():
            return None
        return read_json(path)

    def export(
        self, backend_id: str, campaign_id: str,
    ) -> dict[str, Any] | None:
        """Full campaign export: metadata + all trial details."""
        campaign = self.load(backend_id, campaign_id)
        if campaign is None:
            return None

        trial_dir = self._trial_dir(backend_id, campaign_id)
        full_trials = []
        if trial_dir.exists():
            for path in sorted(trial_dir.glob("trial_*.json")):
                full_trials.append(read_json(path))

        campaign["trials_detail"] = full_trials
        return campaign

    def delete(self, backend_id: str, campaign_id: str) -> bool:
        """Delete a campaign and its trial directory.  Returns True if deleted."""
        import shutil

        campaign_path = self._campaign_path(backend_id, campaign_id)
        trial_dir = self._trial_dir(backend_id, campaign_id)

        deleted = False
        if trial_dir.exists():
            shutil.rmtree(trial_dir)
            deleted = True
        if campaign_path.exists():
            campaign_path.unlink()
            deleted = True
        return deleted
