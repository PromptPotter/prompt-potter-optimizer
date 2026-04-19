"""Campaign registry persistence — per-cycle optimization artifacts.

``CampaignStore`` owns the campaign tree only. Operator session state
(journal/notes/control + the active-cycle pointer) lives in
``SessionStore`` under a peer ``sessions/{session_id}/`` subtree; each
campaign records its parent in ``index.json::parent_session_id``.

Disk layout::

    {tenant_root}/campaigns/{cycle_id}/                      # per-cycle dir
      index.json                                              # campaign metadata + trial index + parent_session_id
      dashboard.json                                          # live scalar counters
      output.log                                              # per-query audit
      log.md                                                  # round-by-round summary
      trial_NNNN.json                                         # optimizer resume WAL
      round_NNNN_candidates.json                              # pre-scoring checkpoint

The ``backend_id`` parameter on public methods is preserved for call-site
stability; campaigns are tenant-global so it does not affect path
construction. A campaign's ``backend_id`` is recorded in its metadata blob.
"""

from __future__ import annotations

import json
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


def campaign_dir_for(tenant_root: Path, cycle_id: str) -> Path:
    """Return ``{tenant_root}/campaigns/{cycle_id}``.

    Module-level so callers that don't hold a ``CampaignStore`` (e.g. the
    emitter's classmethod constructor, which only has a raw project root)
    can resolve the same path without instantiating a store.
    """
    validate_path_component(cycle_id)
    return tenant_root / "campaigns" / cycle_id


class CampaignStore(EntityStore):
    """File I/O for the per-cycle campaign tree."""

    def __init__(self, base_dir: Path):
        # base_dir is tenant root; campaigns nest directly under it
        super().__init__(base_dir, "campaigns")

    # -- path helpers ---------------------------------------------------------

    def _entity_dir(self, _backend_id: str) -> Path:
        """Parent dir for campaign trees. Tenant-global."""
        return self._base_dir / "campaigns"

    def _campaign_dir(self, _backend_id: str, cycle_id: str) -> Path:
        """Per-cycle dir holding trials + dashboard + logs."""
        return campaign_dir_for(self._base_dir, cycle_id)

    def campaign_dir(self, cycle_id: str) -> Path:
        """Public accessor for ``{tenant_root}/campaigns/{cycle_id}``.

        Shared by the presentation layer and the emitter so the layout
        lives in one place.
        """
        return campaign_dir_for(self._base_dir, cycle_id)

    # Retained alias for existing resume/rewind call sites.
    _trial_dir = _campaign_dir

    def _entity_path(self, backend_id: str, entity_id: str) -> Path:
        """Campaign metadata (index.json) lives INSIDE the per-cycle dir."""
        return self._campaign_dir(backend_id, entity_id) / "index.json"

    # -- Campaign CRUD --------------------------------------------------------

    def create(
        self,
        backend_id: str,
        campaign_id: str,
        metadata: dict[str, Any],
    ) -> Path:
        """Create/augment a campaign's ``index.json`` with metadata.

        When the file doesn't exist yet, writes a fresh blob with defaults.
        When it does, merges the new keys over existing values without
        clobbering trial/best/baseline accumulators — defaults only fill
        gaps. ``parent_session_id`` flows through ``metadata``.
        """
        path = self._entity_path(backend_id, campaign_id)
        existing = read_json_optional(path) or {}
        now = datetime.now(UTC).isoformat()
        defaults: dict[str, Any] = {
            "campaign_id": campaign_id,
            "created_at": existing.get("created_at", now),
            "updated_at": now,
            "status": "active",
            "connector_type": metadata.get("connector_type", DEFAULT_CONNECTOR_TYPE),
            "backend_id": backend_id,
            "parent_session_id": existing.get("parent_session_id", ""),
            "n_trials": 0,
            "best_accuracy": 0.0,
            "best_trial_id": None,
            "baseline_accuracy": 0.0,
            "trials": [],
        }
        # Merge order: defaults for missing keys → existing accumulators →
        # explicit metadata overrides. Accumulators ("trials" / "n_trials"
        # / "best_*") are preserved on replay via ``existing``.
        data = {**defaults, **existing, **metadata}
        data["updated_at"] = now
        data["backend_id"] = backend_id
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
        session: Any,
        baseline_render: str,
        baseline_accuracy: float,
        dataset: list[dict[str, Any]],
        active_steps: list[str],
        cycle_id_override: str | None,
        *,
        parent_session_id: str = "",
        resume_from_round_override: int | None = None,
    ) -> tuple[CampaignStore | None, str | None, int]:
        """Open the store and resume/create the cycle in one shot.

        Returns ``(store, cycle_id, resumed_from_round)``.  All-None on
        missing project_root/backend_id or any resume failure.

        ``parent_session_id`` is recorded on a newly created campaign;
        ignored on resume.  When ``resume_from_round_override`` is set,
        trials for rounds > N are archived into
        ``archived/resumed_at_<ts>/`` and the trial index is rebuilt
        before resume.
        """
        from promptpotter.domain.cycle_identity import TUNING_KEYS, cycle_config_identity

        if not (session.project_root and session.backend_id):
            return None, None, 0
        try:
            # project_root points at the tenant root when built via build_stores.
            store = cls(Path(session.project_root))
            resolved = cycle_id_override or cycle_config_identity(
                config,
                baseline_render,
                dataset,
                active_steps,
                strict=config.optimization.strict_cycle_identity,
            )
            if resume_from_round_override is not None:
                store.rewind_to_round(
                    session.backend_id,
                    resolved,
                    resume_from_round_override,
                )
            resumed_from = store.resume_or_create(
                session.backend_id,
                resolved,
                config_snapshot=config.model_dump(mode="json"),
                baseline_accuracy=baseline_accuracy,
                hot_update_keys=TUNING_KEYS if cycle_id_override else frozenset(),
                parent_session_id=parent_session_id,
            )
            return store, resolved, resumed_from
        except (OSError, json.JSONDecodeError, KeyError):
            logger.warning("Cycle resume setup failed — running fresh", exc_info=True)
            return None, None, 0

    def rewind_to_round(
        self,
        backend_id: str,
        cycle_id: str,
        after_round: int,
    ) -> None:
        """Archive trial/candidate files for rounds > ``after_round``.

        Moves ``trial_{M:04d}.json`` and ``round_{M:04d}_candidates.json``
        for M > after_round into ``archived/resumed_at_<ts>/``, then
        rebuilds the cycle's trial index to reflect only surviving trials
        (rounds 0..after_round). No-op on a cycle with no such trial.
        """
        trial_dir = self._campaign_dir(backend_id, cycle_id)
        if not trial_dir.exists():
            raise LookupError(f"cycle {cycle_id!r} has no trials on disk")

        target = trial_dir / f"trial_{after_round:04d}.json"
        if not target.exists():
            raise LookupError(
                f"--from {after_round}: trial_{after_round:04d}.json not found in {trial_dir}"
            )

        survivors: list[Path] = []
        to_archive: list[Path] = []
        for p in sorted(trial_dir.glob("trial_*.json")):
            try:
                n = int(p.stem.removeprefix("trial_"))
            except ValueError:
                continue
            (to_archive if n > after_round else survivors).append(p)
        for p in sorted(trial_dir.glob("round_*_candidates.json")):
            try:
                n = int(p.stem.removeprefix("round_").removesuffix("_candidates"))
            except ValueError:
                continue
            if n > after_round:
                to_archive.append(p)

        if to_archive:
            ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            archive_dir = trial_dir / "archived" / f"resumed_at_{ts}"
            archive_dir.mkdir(parents=True, exist_ok=True)
            for p in to_archive:
                p.rename(archive_dir / p.name)
            logger.info(
                "Rewind cycle %s to round %d: archived %d file(s) → %s",
                cycle_id,
                after_round,
                len(to_archive),
                archive_dir,
            )

        self._rebuild_trial_index(backend_id, cycle_id, survivors)

    def _rebuild_trial_index(
        self,
        backend_id: str,
        cycle_id: str,
        survivors: list[Path],
    ) -> None:
        """Recompute ``trials`` / ``n_trials`` / ``best_accuracy`` / ``best_trial_id``
        from the trial detail files that remain after a rewind."""
        campaign_path = self._entity_path(backend_id, cycle_id)
        data = read_json(campaign_path)

        rebuilt: list[dict[str, Any]] = []
        best_acc = 0.0
        best_trial_id: str | None = None
        for p in sorted(survivors):
            trial = read_json(p)
            round_num = trial.get("round", 0)
            trial_id = trial.get("trial_id", f"round_{round_num}")
            rebuilt.append(
                {
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
            )
            if trial.get("accuracy", 0.0) > best_acc:
                best_acc = trial["accuracy"]
                best_trial_id = trial_id

        data["trials"] = rebuilt
        data["n_trials"] = len(rebuilt)
        data["best_accuracy"] = best_acc
        data["best_trial_id"] = best_trial_id
        data["updated_at"] = datetime.now(UTC).isoformat()
        write_json(campaign_path, data)

    def resume_or_create(
        self,
        backend_id: str,
        cycle_id: str,
        *,
        config_snapshot: dict[str, Any],
        baseline_accuracy: float,
        hot_update_keys: frozenset[str] = frozenset(),
        parent_session_id: str = "",
    ) -> int:
        """Resume an existing cycle or create a new one.

        Returns ``resumed_from_round`` — the number of trial files already
        on disk (0 for a fresh cycle).  If the cycle exists and
        ``hot_update_keys`` is non-empty, merge those keys from
        ``config_snapshot`` into the stored config before returning.
        ``parent_session_id`` is stamped only on fresh creation.
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

        # Fresh campaign — create index.json with metadata + parent link.
        self.create(
            backend_id,
            cycle_id,
            {
                "type": "optimization_loop",
                "config": config_snapshot,
                "baseline_accuracy": baseline_accuracy,
                "parent_session_id": parent_session_id,
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
        """Write the terminal status/stop_reason + outcome summary to disk."""
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

    def load_many(
        self,
        backend_id: str,
        campaign_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Load full campaign records for *campaign_ids*, or all campaigns when None.

        Skips campaigns whose detail file is missing.
        """
        if campaign_ids is None:
            campaign_ids = [s["campaign_id"] for s in self.list_all(backend_id)]
        return [c for cid in campaign_ids if (c := self.load(backend_id, cid)) is not None]

    def list_all(self, backend_id: str) -> list[dict[str, Any]]:
        """Return summary for every campaign stored under this tenant.

        Optionally filters by ``backend_id`` (matched against the
        ``backend_id`` field inside each ``index.json``). Campaigns are
        tenant-global; the filter is a post-read pass, not a path scope.
        Pass ``""`` to list all campaigns regardless of backend.
        """
        campaigns_dir = self._entity_dir(backend_id)
        if not campaigns_dir.exists():
            return []
        results = []
        for cycle_dir in sorted(campaigns_dir.iterdir()):
            index_path = cycle_dir / "index.json"
            if not index_path.is_file():
                continue
            data = read_json(index_path)
            if "campaign_id" not in data:
                continue
            if backend_id and data.get("backend_id") and data["backend_id"] != backend_id:
                continue
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
                    "parent_session_id": data.get("parent_session_id", ""),
                }
            )
        return results

    # -- Trial CRUD -----------------------------------------------------------

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

        detail_path = self._campaign_dir(backend_id, campaign_id) / f"trial_{round_num:04d}.json"
        write_json(detail_path, trial)

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
        data["trials"] = [t for t in data["trials"] if t.get("round") != round_num]
        data["trials"].append(summary)
        data["n_trials"] = len(data["trials"])

        if trial["accuracy"] > data.get("best_accuracy", 0.0):
            data["best_accuracy"] = trial["accuracy"]
            data["best_trial_id"] = trial_id

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
            self._campaign_dir(backend_id, campaign_id) / f"trial_{round_num:04d}.json",
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
        path = (
            self._campaign_dir(backend_id, campaign_id) / f"round_{round_num:04d}_candidates.json"
        )
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
            self._campaign_dir(backend_id, campaign_id) / f"round_{round_num:04d}_candidates.json",
        )

    def delete_round_candidates(
        self,
        backend_id: str,
        campaign_id: str,
        round_num: int,
    ) -> None:
        """Delete persisted candidates for a round (forces fresh generation)."""
        path = (
            self._campaign_dir(backend_id, campaign_id) / f"round_{round_num:04d}_candidates.json"
        )
        if path.exists():
            path.unlink()
            logger.debug(
                "Deleted cached candidates for round %d (escalation invalidation)",
                round_num,
            )

    # -- Campaign log ---------------------------------------------------------

    def append_log(
        self,
        backend_id: str,
        cycle_id: str,
        section: str,
    ) -> None:
        """Append a markdown section to the campaign log (``log.md``)."""
        path = self._campaign_dir(backend_id, cycle_id) / "log.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(section + "\n\n")

    def load_log(self, backend_id: str, cycle_id: str) -> str:
        """Load the full campaign log. Returns empty string if not found."""
        path = self._campaign_dir(backend_id, cycle_id) / "log.md"
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")
