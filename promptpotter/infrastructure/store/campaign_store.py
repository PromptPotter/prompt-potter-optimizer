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
      trials/trial_NNNN.json                                  # optimizer resume WAL
      candidates/round_NNNN.json                              # pre-scoring checkpoint

The ``backend_id`` parameter on public methods is preserved for call-site
stability; campaigns are tenant-global so it does not affect path
construction. A campaign's ``backend_id`` is recorded in its metadata blob.
"""

from __future__ import annotations

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

    def _trials_dir(self, backend_id: str, cycle_id: str) -> Path:
        return self._campaign_dir(backend_id, cycle_id) / "trials"

    def _candidates_dir(self, backend_id: str, cycle_id: str) -> Path:
        return self._campaign_dir(backend_id, cycle_id) / "candidates"

    def _entity_path(self, backend_id: str, entity_id: str) -> Path:
        """Campaign metadata (index.json) lives INSIDE the per-cycle dir."""
        return self._campaign_dir(backend_id, entity_id) / "index.json"

    # -- Campaign CRUD --------------------------------------------------------

    def create(
        self,
        backend_id: str,
        cycle_id: str,
        metadata: dict[str, Any],
    ) -> Path:
        """Create/augment a campaign's ``index.json`` with metadata.

        When the file doesn't exist yet, writes a fresh blob with defaults.
        When it does, merges the new keys over existing values without
        clobbering trial/best/baseline accumulators — defaults only fill
        gaps. ``parent_session_id`` flows through ``metadata``.
        """
        path = self._entity_path(backend_id, cycle_id)
        existing = read_json_optional(path) or {}
        now = datetime.now(UTC).isoformat()
        defaults: dict[str, Any] = {
            "campaign_id": cycle_id,
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
        cycle_id: str,
        updates: dict[str, Any],
    ) -> None:
        """Merge *updates* into the campaign file and write back (+ timestamp)."""
        path = self._entity_path(backend_id, cycle_id)
        data = read_json(path)
        data.update(updates)
        data["updated_at"] = datetime.now(UTC).isoformat()
        write_json(path, data)

    def rewind_to_round(
        self,
        backend_id: str,
        cycle_id: str,
        after_round: int,
    ) -> None:
        """Archive trial/candidate files for rounds > ``after_round``.

        Moves ``trials/trial_{M:04d}.json`` and
        ``candidates/round_{M:04d}.json`` for M > after_round into
        ``archived/resumed_at_<ts>/{trials,candidates}/``, then rebuilds
        the cycle's trial index to reflect only surviving trials
        (rounds 0..after_round). No-op on a cycle with no such trial.
        """
        cycle_dir = self._campaign_dir(backend_id, cycle_id)
        trials_dir = self._trials_dir(backend_id, cycle_id)
        candidates_dir = self._candidates_dir(backend_id, cycle_id)

        if not trials_dir.exists():
            raise LookupError(f"cycle {cycle_id!r} has no trials on disk")

        target = trials_dir / f"trial_{after_round:04d}.json"
        if not target.exists():
            raise LookupError(
                f"--from {after_round}: trial_{after_round:04d}.json not found in {trials_dir}"
            )

        survivors: list[Path] = []
        to_archive_trials: list[Path] = []
        to_archive_candidates: list[Path] = []
        for p in sorted(trials_dir.glob("trial_*.json")):
            try:
                n = int(p.stem.removeprefix("trial_"))
            except ValueError:
                continue
            (to_archive_trials if n > after_round else survivors).append(p)
        if candidates_dir.exists():
            for p in sorted(candidates_dir.glob("round_*.json")):
                try:
                    n = int(p.stem.removeprefix("round_"))
                except ValueError:
                    continue
                if n > after_round:
                    to_archive_candidates.append(p)

        archived_count = len(to_archive_trials) + len(to_archive_candidates)
        if archived_count:
            ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            archive_root = cycle_dir / "archived" / f"resumed_at_{ts}"
            if to_archive_trials:
                (archive_root / "trials").mkdir(parents=True, exist_ok=True)
                for p in to_archive_trials:
                    p.rename(archive_root / "trials" / p.name)
            if to_archive_candidates:
                (archive_root / "candidates").mkdir(parents=True, exist_ok=True)
                for p in to_archive_candidates:
                    p.rename(archive_root / "candidates" / p.name)
            logger.info(
                "Rewind cycle %s to round %d: archived %d file(s) → %s",
                cycle_id,
                after_round,
                archived_count,
                archive_root,
            )

        self._rebuild_trial_index(backend_id, cycle_id, survivors)

    @staticmethod
    def _trial_summary(trial: dict[str, Any]) -> dict[str, Any]:
        """Projection of a trial detail into the index.json::trials shape."""
        round_num = trial.get("round", 0)
        return {
            "trial_id": trial.get("trial_id", f"round_{round_num}"),
            "round": round_num,
            "label": trial.get("label", ""),
            "prompt_fields_id": trial.get("prompt_fields_id", ""),
            "accuracy": trial.get("accuracy", 0.0),
            "hits": trial.get("hits", 0),
            "total": trial.get("total", 0),
            "improved": trial.get("improved", False),
            "created_at": trial.get("created_at", ""),
        }

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

        rebuilt = [self._trial_summary(read_json(p)) for p in sorted(survivors)]
        best = max(rebuilt, key=lambda s: s["accuracy"], default=None)

        data["trials"] = rebuilt
        data["n_trials"] = len(rebuilt)
        data["best_accuracy"] = best["accuracy"] if best else 0.0
        data["best_trial_id"] = best["trial_id"] if best else None
        data["updated_at"] = datetime.now(UTC).isoformat()
        write_json(campaign_path, data)

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
        cycle_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Load full campaign records for *cycle_ids*, or all campaigns when None.

        Skips campaigns whose detail file is missing.
        """
        if cycle_ids is None:
            cycle_ids = [s["campaign_id"] for s in self.list_all(backend_id)]
        return [c for cid in cycle_ids if (c := self.load(backend_id, cid)) is not None]

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
        cycle_id: str,
        trial: dict[str, Any],
    ) -> Path:
        """Persist a trial detail file and update the campaign index."""
        trial_id = trial["trial_id"]
        validate_path_component(trial_id)
        round_num = trial.get("round", 0)

        detail_path = self._trials_dir(backend_id, cycle_id) / f"trial_{round_num:04d}.json"
        write_json(detail_path, trial)

        campaign_path = self._entity_path(backend_id, cycle_id)
        data = read_json(campaign_path)

        data["trials"] = [t for t in data["trials"] if t.get("round") != round_num]
        data["trials"].append(self._trial_summary(trial))
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
        cycle_id: str,
        round_num: int,
    ) -> dict[str, Any] | None:
        """Load a trial detail by round number.  Returns None if not found."""
        return read_json_optional(
            self._trials_dir(backend_id, cycle_id) / f"trial_{round_num:04d}.json",
        )

    def load_trials_range(
        self,
        backend_id: str,
        cycle_id: str,
        start: int,
        end: int,
    ) -> list[dict[str, Any]]:
        """Load trials for rounds ``start..end`` inclusive, in round order.

        Missing trials are skipped silently (``None`` from
        :meth:`load_trial`). Used by the resume-divergence walker in
        :mod:`promptpotter.application.campaign.decisions` to re-derive
        each recorded decision under the current scorer.
        """
        out: list[dict[str, Any]] = []
        for r in range(start, end + 1):
            trial = self.load_trial(backend_id, cycle_id, r)
            if trial is not None:
                out.append(trial)
        return out

    def complete(self, backend_id: str, cycle_id: str) -> None:
        """Mark a campaign as completed."""
        self.update(backend_id, cycle_id, {"status": "completed"})
        logger.info("Campaign %s completed", cycle_id)

    def save_round_candidates(
        self,
        backend_id: str,
        cycle_id: str,
        round_num: int,
        candidates: list[dict[str, Any]],
    ) -> None:
        """Persist generated candidates before evaluation (mid-round checkpoint)."""
        path = self._candidates_dir(backend_id, cycle_id) / f"round_{round_num:04d}.json"
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
        cycle_id: str,
        round_num: int,
    ) -> list[dict[str, Any]] | None:
        """Load persisted candidates for a round.  Returns None if not on disk."""
        return read_json_optional(
            self._candidates_dir(backend_id, cycle_id) / f"round_{round_num:04d}.json",
        )

    def delete_round_candidates(
        self,
        backend_id: str,
        cycle_id: str,
        round_num: int,
    ) -> None:
        """Delete persisted candidates for a round (forces fresh generation)."""
        path = self._candidates_dir(backend_id, cycle_id) / f"round_{round_num:04d}.json"
        if path.exists():
            path.unlink()
            logger.debug(
                "Deleted cached candidates for round %d (escalation invalidation)",
                round_num,
            )

    # log.md is owned exclusively by CampaignPersistenceEmitter — see
    # infrastructure/persistence/session_emitter.py and dashboard_md.py.
    # No store-level append/load accessors: appending from multiple
    # writers was the source of the duplicate-header bug the sliding-
    # window dashboard replaced.
