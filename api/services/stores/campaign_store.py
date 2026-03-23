"""
Campaign registry persistence.

Stores campaign metadata and per-trial results to disk:

    {backend_id}/campaigns/{campaign_id}.json        — metadata + trial index
    {backend_id}/campaigns/{campaign_id}/trial_NNNN.json  — trial detail
"""
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from api.services.stores.base import (
    read_json,
    read_json_optional,
    validate_path_component,
    write_json,
)

logger = logging.getLogger(__name__)


def generate_campaign_id() -> str:
    """Generate a unique campaign identifier."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    short = uuid.uuid4().hex[:8]
    return f"campaign_{ts}_{short}"


def generate_trial_id(round_num: int) -> str:
    """Generate a unique trial identifier for a round."""
    short = uuid.uuid4().hex[:8]
    return f"trial_{round_num:04d}_{short}"


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
        return read_json_optional(self._campaign_path(backend_id, campaign_id))

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
        for path in sorted(campaigns_dir.glob("*.json")):
            data = read_json(path)
            results.append({
                "campaign_id": data["campaign_id"],
                "name": data.get("name", ""),
                "status": data["status"],
                "n_trials": data["n_trials"],
                "best_accuracy": data["best_accuracy"],
                "baseline_accuracy": data["baseline_accuracy"],
                "created_at": data["created_at"],
                "updated_at": data["updated_at"],
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
        # Replace existing trial for same round (idempotent on replay)
        data["trials"] = [
            t for t in data["trials"] if t.get("round") != round_num
        ]
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
        return read_json_optional(
            self._trial_dir(backend_id, campaign_id) / f"trial_{round_num:04d}.json",
        )

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

    # -- High-level convenience methods (formerly in campaign_registry.py) --

    def create_campaign(
        self,
        backend_id: str,
        *,
        name: str = "",
        config: dict[str, Any] | None = None,
        campaign_id: str | None = None,
        langfuse_trace_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a new campaign and persist to disk.  Returns campaign metadata."""
        cid = campaign_id or generate_campaign_id()
        metadata = {
            "name": name or cid,
            "backend_id": backend_id,
            "config": config or {},
            "langfuse_trace_id": langfuse_trace_id,
        }
        self.create(backend_id, cid, metadata)
        logger.info("Created campaign %s for backend %s", cid, backend_id)
        return self.load(backend_id, cid)

    def record_trial(
        self,
        backend_id: str,
        campaign_id: str,
        *,
        round_num: int,
        prompt_state: dict[str, Any],
        accuracy: float,
        hits: int,
        total: int,
        results: list[dict[str, Any]] | None = None,
        label: str = "",
        improved: bool = False,
        candidates_evaluated: int = 0,
        langfuse_trace_id: str | None = None,
        mlflow_run_id: str | None = None,
    ) -> dict[str, Any]:
        """Record a single optimization trial.  Returns the trial detail dict."""
        trial_id = generate_trial_id(round_num)
        now = datetime.now(timezone.utc).isoformat()

        trial = {
            "trial_id": trial_id,
            "round": round_num,
            "label": label,
            "prompt_state": prompt_state,
            "prompt_state_id": prompt_state.get("id", ""),
            "parent_prompt_state_id": prompt_state.get("parent_id"),
            "accuracy": accuracy,
            "hits": hits,
            "total": total,
            "improved": improved,
            "candidates_evaluated": candidates_evaluated,
            "results": results or [],
            "langfuse_trace_id": langfuse_trace_id,
            "mlflow_run_id": mlflow_run_id,
            "created_at": now,
        }

        self.add_trial(backend_id, campaign_id, trial)
        logger.info(
            "Recorded trial %s (round %d, acc=%.3f) in campaign %s",
            trial_id, round_num, accuracy, campaign_id,
        )
        return trial

    def record_campaign_rounds(
        self,
        backend_id: str,
        campaign_id: str,
        campaign_rounds: list[dict[str, Any]],
    ) -> list[str]:
        """Bulk-record campaign round dicts as trials.  Returns trial IDs."""
        trial_ids = []
        for rd in campaign_rounds:
            ps = rd["prompt_state"]
            ps_dict = ps.model_dump()

            trial = self.record_trial(
                backend_id, campaign_id,
                round_num=rd.get("round", 0),
                prompt_state=ps_dict,
                accuracy=rd.get("accuracy", 0.0),
                hits=rd.get("hits", 0),
                total=rd.get("total", 0),
                results=rd.get("results"),
                label=rd.get("label", ""),
                improved=rd.get("improved", False),
                candidates_evaluated=rd.get("candidates_evaluated", 0),
            )
            trial_ids.append(trial["trial_id"])
        return trial_ids

    def complete(self, backend_id: str, campaign_id: str) -> None:
        """Mark a campaign as completed."""
        self.update(backend_id, campaign_id, {"status": "completed"})
        logger.info("Campaign %s completed", campaign_id)

    def save_round_candidates(
        self,
        backend_id: str,
        campaign_id: str,
        round_num: int,
        candidates: list[dict[str, Any]],
    ) -> None:
        """Persist generated candidates before evaluation (mid-round checkpoint)."""
        path = (
            self._trial_dir(backend_id, campaign_id)
            / f"round_{round_num:04d}_candidates.json"
        )
        write_json(path, candidates)
        logger.info(
            "Saved %d candidates for round %d → %s",
            len(candidates), round_num, path.name,
        )

    def load_round_candidates(
        self,
        backend_id: str,
        campaign_id: str,
        round_num: int,
    ) -> list[dict[str, Any]] | None:
        """Load persisted candidates for a round.  Returns None if not on disk."""
        return read_json_optional(
            self._trial_dir(backend_id, campaign_id)
            / f"round_{round_num:04d}_candidates.json",
        )

    def delete_round_candidates(
        self, backend_id: str, campaign_id: str, round_num: int,
    ) -> None:
        """Delete persisted candidates for a round (forces fresh generation)."""
        path = (
            self._trial_dir(backend_id, campaign_id)
            / f"round_{round_num:04d}_candidates.json"
        )
        if path.exists():
            path.unlink()
            logger.info(
                "Deleted cached candidates for round %d (escalation invalidation)",
                round_num,
            )

    def get_lineage(
        self, backend_id: str, campaign_id: str,
    ) -> list[dict[str, Any]]:
        """Reconstruct the full PromptState lineage chain for a campaign.

        Returns a list of ``{trial_id, round, prompt_state_id, parent_id,
        accuracy, label}`` ordered by round.
        """
        campaign = self.load(backend_id, campaign_id)
        if not campaign:
            return []

        lineage = []
        for entry in campaign.get("trials", []):
            trial = self.load_trial(backend_id, campaign_id, entry["round"])
            parent_id = trial.get("parent_prompt_state_id") if trial else None
            lineage.append({
                "trial_id": entry["trial_id"],
                "round": entry["round"],
                "prompt_state_id": entry.get("prompt_state_id", ""),
                "parent_prompt_state_id": parent_id,
                "accuracy": entry.get("accuracy", 0.0),
                "label": entry.get("label", ""),
            })
        return lineage
