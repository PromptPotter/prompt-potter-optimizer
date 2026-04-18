"""Campaign registry persistence.

``CampaignStore`` is the single mint point for the session≡campaign invariant
(v3). It owns the per-cycle artifact directory — campaign metadata, session
state, trials, dashboard, logs, journal, notes, recon results — all
co-located. The legacy ``SessionStore`` is absorbed here; there is no
separate ``sessions`` store, and no separate ``session.json``.

Disk layout (v3)::

    {tenant_root}/campaigns/{cycle_id}/                      # per-cycle dir
      index.json                                              # merged session state + campaign metadata + trial index
      dashboard.json                                          # live scalar counters
      control.json                                            # HITL signals
      output.log                                              # per-query audit
      log.md                                                  # round-by-round summary
      journal.md                                              # user narrative
      notes.md                                                # Claude notes
      recon.json                                              # this cycle's scan result
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
    """File I/O for campaign registry + per-cycle session artifacts."""

    def __init__(self, base_dir: Path):
        # base_dir is tenant root; campaigns nest directly under it
        super().__init__(base_dir, "campaigns")

    # -- path helpers ---------------------------------------------------------

    def _entity_dir(self, _backend_id: str) -> Path:
        """Parent dir for campaign trees. Tenant-global."""
        return self._base_dir / "campaigns"

    def _campaign_dir(self, _backend_id: str, cycle_id: str) -> Path:
        """Per-cycle dir holding trials + session artifacts + logs."""
        return campaign_dir_for(self._base_dir, cycle_id)

    def campaign_dir(self, cycle_id: str) -> Path:
        """Public accessor for the per-cycle artifact directory.

        Single source of truth for ``{tenant_root}/campaigns/{cycle_id}``;
        shared by the presentation layer and the emitter so the v3 layout
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
        When it does (session state was seeded via ``create_session``),
        merges the campaign-metadata keys alongside the session state
        already on disk — ``index.json`` carries both concerns (session ≡
        campaign). The optimizer-added keys never clobber existing session
        state keys from ``create_session``.
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
            "n_trials": 0,
            "best_accuracy": 0.0,
            "best_trial_id": None,
            "baseline_accuracy": 0.0,
            "trials": [],
        }
        # Merge order: existing session state → defaults for missing keys →
        # explicit metadata overrides. "trials" / "n_trials" / "best_*" are
        # preserved on replay via ``existing`` since defaults only fill gaps.
        data = {**defaults, **existing, **metadata}
        # ``updated_at`` is always now; ``created_at`` sticks.
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
        resume_from_round_override: int | None = None,
    ) -> tuple[CampaignStore | None, str | None, int]:
        """Open the store and resume/create the cycle in one shot.

        Returns ``(store, cycle_id, resumed_from_round)``.  All-None on
        missing project_root/backend_id or any resume failure.

        When ``resume_from_round_override`` is set, trials for rounds > N
        are archived into ``archived/resumed_at_<ts>/`` and the trial
        index is rebuilt before resume.
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
    ) -> int:
        """Resume an existing cycle or create a new one.

        Returns ``resumed_from_round`` — the number of trial files already
        on disk (0 for a fresh cycle).  If the cycle exists and
        ``hot_update_keys`` is non-empty, merge those keys from
        ``config_snapshot`` into the stored config before returning.
        """
        existing = self.load(backend_id, cycle_id)
        has_campaign_meta = bool(existing and "trials" in existing)
        if has_campaign_meta:
            if hot_update_keys:
                stored_cfg = existing.get("config", {}) if existing else {}
                if stored_cfg:
                    cfg_updated = False
                    for k in hot_update_keys:
                        if stored_cfg.get(k) != config_snapshot.get(k):
                            stored_cfg[k] = config_snapshot.get(k)
                            cfg_updated = True
                    if cfg_updated:
                        self.update(backend_id, cycle_id, {"config": stored_cfg})
                        logger.info("Updated loop-control config for %s", cycle_id)
            resumed_from_round = len((existing or {}).get("trials", []))
            if resumed_from_round:
                logger.debug(
                    "Resuming cycle %s — %d prior round(s) on disk",
                    cycle_id,
                    resumed_from_round,
                )
            return resumed_from_round

        # Fresh or session-only index.json — merge campaign metadata in.
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
        Skips cycle dirs that have only session state (no campaign
        metadata yet) — those show up once ``optimize`` runs and
        ``resume_or_create`` merges campaign keys into ``index.json``.
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
            if "campaign_id" not in data or "trials" not in data:
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

    # ----------------------------------------------------------------------
    # Session artifacts — absorbed from the deleted ``SessionStore`` class.
    # Session ≡ campaign (v3): session_id IS the cycle_id; session state and
    # campaign metadata are merged into one ``index.json`` per cycle.
    # ----------------------------------------------------------------------

    def _session_dir(self, backend_id: str, session_id: str) -> Path:
        """Per-campaign artifact directory. Alias for ``_campaign_dir``."""
        return self._campaign_dir(backend_id, session_id)

    def create_session(self, backend_id: str, state: dict[str, Any], cycle_hash: str) -> str:
        """Mint a new cycle and persist the initial session state.

        ``cycle_hash`` is the 12-hex content-addressed tail (no ``cycle_``
        prefix) returned by ``cycle_hash_suffix``. The session dir becomes
        ``campaigns/{timestamp}_{cycle_hash}/``; its suffix matches the
        ``cycle_{cycle_hash}`` dir that ``bootstrap_cycle`` produces for
        the same problem, so the two pair at a glance.

        Campaign metadata (trials, config, baseline_accuracy, etc.) is
        merged into the same index.json later by ``resume_or_create``.
        """
        from promptpotter.infrastructure.store.stores import generate_session_id

        cycle_id = generate_session_id(cycle_hash)
        state["session_id"] = cycle_id
        state["created_at"] = datetime.now(UTC).isoformat()
        self.save_session(backend_id, cycle_id, state)
        return cycle_id

    def save_session(self, backend_id: str, session_id: str, state: dict[str, Any]) -> None:
        """Merge session-state keys into the cycle's ``index.json``.

        Preserves any campaign-metadata keys (trials, n_trials,
        best_accuracy, …) that were written by ``create`` /
        ``resume_or_create`` / ``add_trial``.
        """
        path = self._entity_path(backend_id, session_id)
        existing = read_json_optional(path) or {}
        existing.update(state)
        write_json(path, existing)

    def load_session(self, backend_id: str, session_id: str) -> dict[str, Any] | None:
        """Load merged session state + metadata. Returns None if not found."""
        return read_json_optional(self._entity_path(backend_id, session_id))

    # -- Scan results ---------------------------------------------------------

    def save_recon_results(
        self,
        backend_id: str,
        session_id: str,
        scan_df_records: list[dict],
        axis_profiles: list[dict],
    ) -> None:
        """Persist sensitivity scan results for this cycle."""
        path = self._session_dir(backend_id, session_id) / "recon.json"
        write_json(
            path,
            {
                "recon_df": scan_df_records,
                "axis_profiles": axis_profiles,
                "saved_at": datetime.now(UTC).isoformat(),
            },
        )

    def load_recon_results(
        self,
        backend_id: str,
        session_id: str,
    ) -> dict[str, Any] | None:
        """Load scan results. Returns None if not found."""
        path = self._session_dir(backend_id, session_id) / "recon.json"
        return read_json_optional(path)

    # -- Campaign log ---------------------------------------------------------

    def append_log(
        self,
        backend_id: str,
        session_id: str,
        section: str,
    ) -> None:
        """Append a markdown section to the campaign log (``log.md``)."""
        path = self._session_dir(backend_id, session_id) / "log.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(section + "\n\n")

    def load_log(self, backend_id: str, session_id: str) -> str:
        """Load the full campaign log. Returns empty string if not found."""
        path = self._session_dir(backend_id, session_id) / "log.md"
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    # -- Active session pointer ----------------------------------------------

    def save_active_pointer(self, tenant_id: str, cycle_id: str) -> None:
        """Persist pointer to the active campaign across CLI invocations."""
        from promptpotter.infrastructure.store.stores import save_active_pointer

        save_active_pointer(tenant_id, cycle_id)

    def clear_active_pointer(self) -> None:
        """Delete the active-session pointer file, if present. Idempotent."""
        from promptpotter.infrastructure.store.stores import clear_active_pointer

        clear_active_pointer()

    def read_active_pointer(self) -> tuple[str, str]:
        """Return ``(tenant_id, cycle_id)`` from the pointer, or ``("", "")``."""
        from promptpotter.infrastructure.store.stores import read_active_pointer

        return read_active_pointer()

    def load_active(
        self,
        session_override: str | None = None,
    ) -> tuple[dict[str, Any], str, str]:
        """Load active session state + backend_id + cycle_id.

        Reads ``.promptpotter/active_session.json`` to find the active
        tenant+cycle, then loads the full session state. The ``backend_id``
        is read from the state blob's ``init_params`` (not from the
        pointer, which is tenant-scoped).

        Raises ``SystemExit`` if no active session or session not found.
        """
        from promptpotter.infrastructure.store.stores import (
            _ACTIVE_SESSION_PATH,
            read_active_pointer,
        )

        if not _ACTIVE_SESSION_PATH.exists():
            raise SystemExit("ERROR: No active session pointer found.")
        _tid, cid = read_active_pointer()
        cid = session_override or cid
        if not cid:
            raise SystemExit("ERROR: No active cycle_id in pointer.")

        # backend_id does not affect path construction — campaigns are
        # tenant-global. Recover backend_id from the loaded state blob.
        state = self.load_session("", cid)
        if not state:
            raise SystemExit(f"ERROR: Session '{cid}' not found.")
        bid = state.get("init_params", {}).get("backend_id", "") or ""
        return state, bid, cid
