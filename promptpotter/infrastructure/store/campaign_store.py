"""
Campaign registry persistence.

Stores campaign metadata and per-trial results to disk:

    {backend_id}/campaigns/{campaign_id}.json        — metadata + trial index
    {backend_id}/campaigns/{campaign_id}/trial_NNNN.json  — trial detail
"""

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from promptpotter.infrastructure.store.base import (
    EntityStore,
    read_json,
    read_json_optional,
    validate_path_component,
    write_json,
)
from promptpotter.shared.constants import DEFAULT_CONNECTOR_TYPE

logger = logging.getLogger(__name__)


class CampaignStore(EntityStore):
    """File I/O for campaign registry persistence."""

    def __init__(self, base_dir: Path):
        super().__init__(base_dir, "campaigns")

    def _trial_dir(self, backend_id: str, campaign_id: str) -> Path:
        validate_path_component(campaign_id)
        return self._entity_dir(backend_id) / campaign_id

    # -- Campaign CRUD --

    def create(
        self,
        backend_id: str,
        campaign_id: str,
        metadata: dict[str, Any],
    ) -> Path:
        """Create a new campaign file with initial metadata."""
        path = self._entity_path(backend_id, campaign_id)
        if path.exists():
            raise FileExistsError(f"Campaign already exists: {campaign_id}")
        now = datetime.now(UTC).isoformat()
        data = {
            "campaign_id": campaign_id,
            "created_at": now,
            "updated_at": now,
            "status": "active",
            "connector_type": metadata.get("connector_type", DEFAULT_CONNECTOR_TYPE),
            "n_trials": 0,
            "best_accuracy": 0.0,
            "best_trial_id": None,
            "baseline_accuracy": 0.0,
            "trials": [],
            **metadata,
        }
        write_json(path, data)
        return path

    def update(
        self,
        backend_id: str,
        campaign_id: str,
        updates: dict[str, Any],
    ) -> None:
        """Merge *updates* into the campaign file and write back (+ timestamp)."""
        path = self._entity_path(backend_id, campaign_id)
        data = read_json(path)
        data.update(updates)
        data["updated_at"] = datetime.now(UTC).isoformat()
        write_json(path, data)

    @classmethod
    def bootstrap_cycle(
        cls,
        config: Any,
        baseline_render: str,
        baseline_accuracy: float,
        dataset: list[dict[str, Any]],
        cycle_id_override: str | None,
    ) -> tuple["CampaignStore | None", str | None, int]:
        """Open the store and resume/create the cycle in one shot.

        Returns ``(store, cycle_id, resumed_from_round)``.  All-None on
        missing project_root/backend_id or any resume failure.
        """
        import json as _json

        from promptpotter.domain.cycle_identity import TUNING_KEYS, cycle_config_identity

        if not (config.project_root and config.backend_id):
            return None, None, 0
        try:
            store = cls(Path(config.project_root))
            resolved = cycle_id_override or cycle_config_identity(
                config, baseline_render, dataset, strict=config.strict_cycle_identity
            )
            resumed_from = store.resume_or_create(
                config.backend_id,
                resolved,
                config_snapshot=config.model_dump(mode="json"),
                baseline_accuracy=baseline_accuracy,
                hot_update_keys=TUNING_KEYS if cycle_id_override else frozenset(),
            )
            return store, resolved, resumed_from
        except (OSError, _json.JSONDecodeError, KeyError):
            logger.warning("Cycle resume setup failed — running fresh", exc_info=True)
            return None, None, 0

    def resume_or_create(
        self,
        backend_id: str,
        cycle_id: str,
        *,
        config_snapshot: dict[str, Any],
        baseline_accuracy: float,
        hot_update_keys: frozenset[str] = frozenset(),
    ) -> int:
        """Resume an existing cycle or create a new one.

        Returns ``resumed_from_round`` — the number of trial files already
        on disk (0 for a fresh cycle).  If the cycle exists and
        ``hot_update_keys`` is non-empty, merge those keys from
        ``config_snapshot`` into the stored config before returning.
        """
        existing = self.load(backend_id, cycle_id)
        if existing is not None:
            if hot_update_keys:
                stored_cfg = existing.get("config", {})
                if stored_cfg:
                    cfg_updated = False
                    for k in hot_update_keys:
                        if stored_cfg.get(k) != config_snapshot.get(k):
                            stored_cfg[k] = config_snapshot.get(k)
                            cfg_updated = True
                    if cfg_updated:
                        self.update(backend_id, cycle_id, {"config": stored_cfg})
                        logger.info("Updated loop-control config for %s", cycle_id)
            resumed_from_round = len(existing.get("trials", []))
            if resumed_from_round:
                logger.debug(
                    "Resuming cycle %s — %d prior round(s) on disk",
                    cycle_id,
                    resumed_from_round,
                )
            return resumed_from_round

        self.create(
            backend_id,
            cycle_id,
            {
                "type": "optimization_loop",
                "config": config_snapshot,
                "baseline_accuracy": baseline_accuracy,
            },
        )
        return 0

    def mark_finished(
        self,
        backend_id: str,
        cycle_id: str,
        *,
        status: str,
        stop_reason: str,
        best_accuracy: float,
        best_round: int,
        n_rounds: int,
        finished_at: str,
    ) -> None:
        """Write the terminal status/stop_reason + outcome summary to disk.

        Wraps the underlying :meth:`update` in :func:`graceful` so finalize
        paths never raise on store I/O errors.
        """
        from promptpotter.shared.errors import graceful

        with graceful("Campaign completion update failed"):
            self.update(
                backend_id,
                cycle_id,
                {
                    "status": status,
                    "stop_reason": stop_reason,
                    "best_accuracy": best_accuracy,
                    "best_round": best_round,
                    "n_rounds": n_rounds,
                    "finished_at": finished_at,
                },
            )

    def list_all(self, backend_id: str) -> list[dict[str, Any]]:
        """Return summary for every campaign under *backend_id*."""
        campaigns_dir = self._entity_dir(backend_id)
        if not campaigns_dir.exists():
            return []
        results = []
        for path in sorted(campaigns_dir.glob("*.json")):
            data = read_json(path)
            results.append(
                {
                    "campaign_id": data["campaign_id"],
                    "name": data.get("name", ""),
                    "status": data["status"],
                    "n_trials": data["n_trials"],
                    "best_accuracy": data["best_accuracy"],
                    "baseline_accuracy": data["baseline_accuracy"],
                    "created_at": data["created_at"],
                    "updated_at": data["updated_at"],
                }
            )
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
        detail_path = self._trial_dir(backend_id, campaign_id) / f"trial_{round_num:04d}.json"
        write_json(detail_path, trial)

        # Update campaign index
        campaign_path = self._entity_path(backend_id, campaign_id)
        data = read_json(campaign_path)

        summary = {
            "trial_id": trial_id,
            "round": round_num,
            "label": trial.get("label", ""),
            "prompt_fields_id": trial.get("prompt_fields_id", ""),
            "accuracy": trial.get("accuracy", 0.0),
            "hits": trial.get("hits", 0),
            "total": trial.get("total", 0),
            "improved": trial.get("improved", False),
            "created_at": trial.get("created_at", ""),
        }
        # Replace existing trial for same round (idempotent on replay)
        data["trials"] = [t for t in data["trials"] if t.get("round") != round_num]
        data["trials"].append(summary)
        data["n_trials"] = len(data["trials"])

        # Track best
        if trial["accuracy"] > data.get("best_accuracy", 0.0):
            data["best_accuracy"] = trial["accuracy"]
            data["best_trial_id"] = trial_id

        # Baseline is always round 0
        if round_num == 0:
            data["baseline_accuracy"] = trial["accuracy"]

        data["updated_at"] = datetime.now(UTC).isoformat()
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
        path = self._trial_dir(backend_id, campaign_id) / f"round_{round_num:04d}_candidates.json"
        write_json(path, candidates)
        logger.debug(
            "Saved %d candidates for round %d → %s",
            len(candidates),
            round_num,
            path.name,
        )

    def load_round_candidates(
        self,
        backend_id: str,
        campaign_id: str,
        round_num: int,
    ) -> list[dict[str, Any]] | None:
        """Load persisted candidates for a round.  Returns None if not on disk."""
        return read_json_optional(
            self._trial_dir(backend_id, campaign_id) / f"round_{round_num:04d}_candidates.json",
        )

    def delete_round_candidates(
        self,
        backend_id: str,
        campaign_id: str,
        round_num: int,
    ) -> None:
        """Delete persisted candidates for a round (forces fresh generation)."""
        path = self._trial_dir(backend_id, campaign_id) / f"round_{round_num:04d}_candidates.json"
        if path.exists():
            path.unlink()
            logger.debug(
                "Deleted cached candidates for round %d (escalation invalidation)",
                round_num,
            )
