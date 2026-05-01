"""Concrete entity stores + composite ``Stores`` bundle.

Three labeled sections, in dependency order:

1. **BackendStore + Stores composite** (``BackendStore``, ``Stores``,
   ``build_stores``, active-pointer helpers) — backend registration +
   sync, plus the composite that bundles the four stores together.
2. **CampaignStore** (per-cycle optimization artifacts under
   ``campaigns/``).
3. **SessionStore** (per-session metadata under ``sessions/``).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from promptpotter.config.settings import DEFAULT_CONNECTOR_TYPE
from promptpotter.domain.backend import BackendConnection, Execution
from promptpotter.infrastructure.store.base import (
    EntityStore,
    read_json,
    read_json_optional,
    validate_path_component,
    write_json,
)
from promptpotter.infrastructure.store.measurement_archive import MeasurementArchive

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PROJECTS_ROOT = _REPO_ROOT / ".promptpotter" / "projects"
DEFAULT_TENANT_ID = "default"


class BackendStore:
    """File I/O for backend registration and synced API responses.

    Backends live under ``library/backends/{backend_id}/`` — peer entities
    within the tenant, not outer axes themselves. Named datasets live
    outside the tenant tree at ``{datasets_root}/{name}/cache.json`` so
    they survive ``.promptpotter/`` resets.
    """

    def __init__(self, base_dir: Path, datasets_root: Path):
        self._base_dir = base_dir
        self._datasets_root = datasets_root

    def _backends_root(self) -> Path:
        return self._base_dir / "library" / "backends"

    def _backend_dir(self, backend_id: str) -> Path:
        validate_path_component(backend_id)
        return self._backends_root() / backend_id

    def _sync_dir(self, backend_id: str) -> Path:
        return self._backend_dir(backend_id) / "sync"

    # -- backend CRUD ---------------------------------------------------------

    def register(self, backend: BackendConnection) -> Path:
        """Write backend.json for a new backend."""
        path = self._backend_dir(backend.id) / "backend.json"
        write_json(path, backend.model_dump())
        return path

    def get(self, backend_id: str) -> BackendConnection | None:
        """Read backend.json, return None if not found."""
        data = read_json_optional(self._backend_dir(backend_id) / "backend.json")
        return BackendConnection(**data) if data is not None else None

    def list_all(self) -> list[BackendConnection]:
        """List all registered backends."""
        root = self._backends_root()
        if not root.exists():
            return []
        backends = []
        for d in sorted(root.iterdir()):
            cfg = d / "backend.json"
            if cfg.exists():
                backends.append(BackendConnection(**read_json(cfg)))
        return backends

    def update(self, backend: BackendConnection) -> None:
        """Overwrite backend.json with updated data."""
        path = self._backend_dir(backend.id) / "backend.json"
        write_json(path, backend.model_dump())

    # -- sync (verbatim API responses) ----------------------------------------

    def save_sync(self, backend_id: str, key: str, data: Any) -> Path:
        """Store a verbatim API response under sync/.

        ``key`` is a relative path like ``experiments.json`` or
        ``experiments/{id}.json``.
        """
        path = self._sync_dir(backend_id) / key
        write_json(path, data)
        return path

    def load_sync(self, backend_id: str, key: str) -> Any | None:
        """Read a synced API response. Returns None if not found."""
        return read_json_optional(self._sync_dir(backend_id) / key)

    def list_synced_experiments(self, backend_id: str) -> list[dict[str, Any]]:
        """List individual synced experiment files."""
        exp_dir = self._sync_dir(backend_id) / "experiments"
        if not exp_dir.exists():
            return []
        return [read_json(p) for p in sorted(exp_dir.glob("*.json"))]

    # -- executions (absorbed from ExecutionStore) ----------------------------

    def _executions_dir(self, backend_id: str) -> Path:
        return self._backend_dir(backend_id) / "executions"

    def load_execution(self, backend_id: str, execution_id: str) -> Execution | None:
        """Load an execution by ID. Returns None if not found."""
        data = read_json_optional(
            self._executions_dir(backend_id) / f"{execution_id}.json",
        )
        return Execution(**data) if data is not None else None

    def list_executions(self, backend_id: str) -> list[dict[str, Any]]:
        """List execution summaries (without full results array)."""
        d = self._executions_dir(backend_id)
        if not d.exists():
            return []
        items = []
        for p in sorted(d.glob("*.json")):
            data = read_json(p)
            items.append(
                {
                    "execution_id": data["execution_id"],
                    "backend_id": data["backend_id"],
                    "experiment_id": data["experiment_id"],
                    "variant_label": data.get("variant_label", ""),
                    "pipeline_notation": data.get("pipeline_notation", ""),
                    "query_count": data.get("query_count", 0),
                    "successful_count": data.get("successful_count", 0),
                    "created_at": data.get("created_at", ""),
                }
            )
        return items

    # -- datasets (repo-adjacent, gitignored) ----------------------------------
    # Datasets are identified by name alone, not by backend. Caches live at
    # ``{datasets_root}/{name}/cache.json`` next to each dataset's
    # pipeline.json / campaign.json, and survive ``.promptpotter/`` resets.

    def _dataset_cache_path(self, name: str) -> Path:
        validate_path_component(name)
        return self._datasets_root / name / "cache.json"

    def save_dataset(
        self,
        name: str,
        items: list,
        *,
        source_file: str = "",
    ) -> Path:
        """Write a named dataset to disk.

        ``items`` may be ``list[Sample]`` or ``list[dict]``; Samples are
        serialized via ``model_dump()``.
        """
        from promptpotter.domain.sample import Sample

        serialized = [item.model_dump() if isinstance(item, Sample) else item for item in items]
        data: dict[str, Any] = {
            "name": name,
            "created_at": datetime.now(UTC).isoformat(),
            "source_file": source_file,
            "row_count": len(serialized),
            "items": serialized,
        }
        path = self._dataset_cache_path(name)
        write_json(path, data)
        return path

    def load_dataset(self, name: str) -> dict[str, Any] | None:
        """Load a named dataset. Returns ``None`` if not found."""
        return read_json_optional(self._dataset_cache_path(name))

    def exclude_dataset_items(
        self,
        name: str,
        exclusions: list[dict[str, Any]],
    ) -> int:
        """Atomically move items from ``items`` into the ``excluded`` sidelist.

        Each entry in ``exclusions`` must have a ``query`` key matching the
        ``query`` field of an active item, plus arbitrary metadata
        (``reason``, ``hit_rate``, ``observations``, ``campaign_id``, …) that
        will be persisted alongside the original item.

        Returns the number of items actually moved. Items whose query is not
        in the active list are silently skipped (idempotent).
        """
        data = self.load_dataset(name)
        if data is None:
            return 0

        items: list[dict[str, Any]] = list(data.get("items", []))
        excluded: list[dict[str, Any]] = list(data.get("excluded", []))

        targets: dict[str, dict[str, Any]] = {e["query"]: e for e in exclusions}
        remaining: list[dict[str, Any]] = []
        moved = 0
        now = datetime.now(UTC).isoformat()
        for item in items:
            q = item.get("query", "")
            if q in targets:
                meta = targets[q]
                excluded.append(
                    {
                        "item": item,
                        "reason": meta.get("reason", "zero_signal"),
                        "hit_rate": meta.get("hit_rate"),
                        "observations": meta.get("observations"),
                        "campaign_id": meta.get("campaign_id", ""),
                        "excluded_at": now,
                    }
                )
                moved += 1
            else:
                remaining.append(item)

        if moved == 0:
            return 0

        data["items"] = remaining
        data["excluded"] = excluded
        data["row_count"] = len(remaining)
        write_json(self._dataset_cache_path(name), data)
        return moved

    def restore_dataset_items(
        self,
        name: str,
        queries: list[str] | None = None,
    ) -> int:
        """Move items from ``excluded`` back into ``items``.

        If ``queries`` is None, restores everything. Returns the number of
        items actually restored.
        """
        data = self.load_dataset(name)
        if data is None:
            return 0

        items: list[dict[str, Any]] = list(data.get("items", []))
        excluded: list[dict[str, Any]] = list(data.get("excluded", []))
        if not excluded:
            return 0

        keep: list[dict[str, Any]] = []
        restored = 0
        for entry in excluded:
            item = entry["item"]
            if queries is None or item.get("query", "") in queries:
                items.append(item)
                restored += 1
            else:
                keep.append(entry)

        if restored == 0:
            return 0

        data["items"] = items
        data["excluded"] = keep
        data["row_count"] = len(items)
        write_json(self._dataset_cache_path(name), data)
        return restored

    # -- connector profile (persistent per-backend defaults) -------------------

    def save_connector_profile(self, backend_id: str, profile: dict[str, Any]) -> None:
        """Write connector profile — persistent campaign defaults for this backend."""
        path = self._backend_dir(backend_id) / "connector_profile.json"
        write_json(path, profile)

    def load_connector_profile(self, backend_id: str) -> dict[str, Any] | None:
        """Load connector profile. Returns None if no profile saved."""
        return read_json_optional(
            self._backend_dir(backend_id) / "connector_profile.json",
        )


# ---------------------------------------------------------------------------
# Active session pointer — survives across CLI invocations. Payload shape is
# ``{tenant_id, session_id, cycle_id}``; ``backend_id`` is *not* part of the
# pointer because it's no longer the project axis. Callers that need the
# backend_id read it from the session's state blob.
# ---------------------------------------------------------------------------

_ACTIVE_SESSION_PATH = Path(__file__).resolve().parents[3] / ".promptpotter" / "active_session.json"


def mint_session_id() -> str:
    """Mint a fresh, opaque session id (``s_<8 hex>``).

    Random — no relation to problem identity. Stays stable across
    multiple campaigns under the same session in the future 1:N world.
    """
    import uuid

    return f"s_{uuid.uuid4().hex[:8]}"


def save_active_pointer(tenant_id: str, session_id: str, cycle_id: str) -> None:
    """Persist pointer to the active session+campaign across CLI invocations."""
    validate_path_component(tenant_id)
    validate_path_component(session_id)
    validate_path_component(cycle_id)
    _ACTIVE_SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    _ACTIVE_SESSION_PATH.write_text(
        json.dumps({"tenant_id": tenant_id, "session_id": session_id, "cycle_id": cycle_id}),
        encoding="utf-8",
    )


def clear_active_pointer() -> None:
    """Delete the active-session pointer file, if present. Idempotent."""
    _ACTIVE_SESSION_PATH.unlink(missing_ok=True)


def read_active_pointer() -> tuple[str, str, str]:
    """Return ``(tenant_id, session_id, cycle_id)`` from the pointer.

    Returns ``("", "", "")`` when the pointer is missing or unreadable.
    Does NOT raise if a referenced session/campaign has been deleted —
    only inspects the raw pointer file. Used by guardrails that need to
    compare the pointer to a requested tenant without coupling to session
    lifecycle.
    """
    if not _ACTIVE_SESSION_PATH.exists():
        return "", "", ""
    try:
        ptr = json.loads(_ACTIVE_SESSION_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "", "", ""
    return (
        ptr.get("tenant_id", ""),
        ptr.get("session_id", ""),
        ptr.get("cycle_id", ""),
    )


# ---------------------------------------------------------------------------
# Composite store — frozen bundle of the focused stores. Sessions and
# campaigns are separate stores rooted at peer subtrees.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Stores:
    """Composite bundle of focused stores rooted at a per-tenant ``base_dir``.

    Construct via :func:`build_stores`. ``base_dir`` is the tenant root
    (``{projects_root}/{tenant_id}/``). Sessions and campaigns are peer
    trees under the tenant; a campaign records its parent session via
    ``index.json::parent_session_id``.
    """

    base_dir: Path
    tenant_id: str
    backends: BackendStore
    sessions: SessionStore
    campaigns: CampaignStore
    archive: MeasurementArchive


def build_stores(
    projects_root: Path | str | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    datasets_root: Path | str | None = None,
) -> Stores:
    """Assemble a :class:`Stores` bundle rooted under a tenant.

    ``projects_root`` defaults to ``<repo_root>/.promptpotter/projects``.
    ``tenant_id`` defaults to ``"default"`` — the single-user CLI tenant.
    ``datasets_root`` defaults to ``<repo_root>/datasets`` — named dataset
    caches live there to survive ``.promptpotter/`` resets. Tests pass
    ``tmp_path`` to isolate from the real repo tree.
    """
    validate_path_component(tenant_id)
    root = Path(projects_root) if projects_root else DEFAULT_PROJECTS_ROOT
    tenant_dir = root / tenant_id
    ds_root = Path(datasets_root) if datasets_root else _REPO_ROOT / "datasets"
    return Stores(
        base_dir=tenant_dir,
        tenant_id=tenant_id,
        backends=BackendStore(tenant_dir, ds_root),
        sessions=SessionStore(tenant_dir),
        campaigns=CampaignStore(tenant_dir),
        archive=MeasurementArchive(tenant_dir),
    )


# ===========================================================================
# CampaignStore — per-cycle optimization artifacts under ``campaigns/``
# ===========================================================================


_SIBLING_SEP_RE = re.compile(r"_(fork|diag)_")


def root_cycle_id(cycle_id: str) -> str:
    """Family-root cycle id — the prefix before the first sibling separator.

    Two separators are recognized: ``_fork_`` (divergence forks, minted by
    ``_fork_at_divergence``) and ``_diag_`` (diagnostic-BFS siblings, minted
    by ``_fork_for_diag_sibling``). Both are deterministic prefixes — no
    I/O, no parent-chain walk."""
    m = _SIBLING_SEP_RE.search(cycle_id)
    return cycle_id[: m.start()] if m else cycle_id


def root_dir_for(tenant_root: Path, cycle_id: str) -> Path:
    """Family-root campaign dir — where telemetry binds (one continuous stream
    across all forks of the family)."""
    return tenant_root / "campaigns" / root_cycle_id(cycle_id)


def campaign_dir_for(tenant_root: Path, cycle_id: str) -> Path:
    """Per-cycle dir (audit). Roots live at ``campaigns/{cycle_id}``; forks
    nest under their family root at ``campaigns/{root}/forks/{cycle_id}``.

    Module-level so callers that don't hold a ``CampaignStore`` (the emitter's
    classmethod constructor) can resolve the same path without a store."""
    validate_path_component(cycle_id)
    root = root_cycle_id(cycle_id)
    if root == cycle_id:
        return tenant_root / "campaigns" / cycle_id
    return tenant_root / "campaigns" / root / "forks" / cycle_id


class CampaignStore(EntityStore):
    """File I/O for the per-cycle campaign tree."""

    def __init__(self, base_dir: Path):
        # base_dir is tenant root; campaigns nest directly under it
        super().__init__(base_dir, "campaigns")

    # -- path helpers ---------------------------------------------------------

    def _entity_dir(self, _backend_id: str) -> Path:
        """Parent dir for root campaign trees. Tenant-global."""
        return self._base_dir / "campaigns"

    def _campaign_dir(self, _backend_id: str, cycle_id: str) -> Path:
        """Per-cycle dir holding trials + audit. Resolves root vs fork layout."""
        return campaign_dir_for(self._base_dir, cycle_id)

    def campaign_dir(self, cycle_id: str) -> Path:
        """Public path accessor — root cycles at ``campaigns/{cycle_id}``,
        forks at ``campaigns/{root}/forks/{cycle_id}``."""
        return campaign_dir_for(self._base_dir, cycle_id)

    def _trials_dir(self, backend_id: str, cycle_id: str) -> Path:
        return self._campaign_dir(backend_id, cycle_id) / "trials"

    def _candidates_dir(self, backend_id: str, cycle_id: str) -> Path:
        # Internal resume checkpoint — hidden under ``.cache/`` so the cycle
        # dir's top level shows only files an operator should actually read.
        return self._campaign_dir(backend_id, cycle_id) / ".cache" / "candidates"

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
        ``.cache/candidates/round_{M:04d}.json`` for M > after_round into
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

        Walks both top-level root cycles (``campaigns/{cycle_id}/``) and
        nested forks (``campaigns/{root}/forks/{cycle_id}/``). Optionally
        filters by ``backend_id`` (matched against ``index.json::backend_id``).
        Pass ``""`` to list all campaigns regardless of backend.
        """
        campaigns_dir = self._entity_dir(backend_id)
        if not campaigns_dir.exists():
            return []

        def index_files() -> list[Path]:
            out: list[Path] = []
            for root_dir in sorted(campaigns_dir.iterdir()):
                if not root_dir.is_dir():
                    continue
                if (idx := root_dir / "index.json").is_file():
                    out.append(idx)
                forks_dir = root_dir / "forks"
                if forks_dir.is_dir():
                    for fork_dir in sorted(forks_dir.iterdir()):
                        if (idx := fork_dir / "index.json").is_file():
                            out.append(idx)
            return out

        results = []
        for index_path in index_files():
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
        :mod:`promptpotter.application.optimization.cycle` to re-derive
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
        """Persist generated candidates before scoring (mid-round checkpoint)."""
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


# ===========================================================================
# SessionStore — per-session metadata under ``sessions/``
# ===========================================================================


def session_dir_for(tenant_root: Path, session_id: str) -> Path:
    """Return ``{tenant_root}/sessions/{session_id}``.

    Module-level so callers that don't hold a ``SessionStore`` (e.g. the
    emitter's classmethod constructor, which only has a raw project root)
    can resolve the same path without instantiating a store.
    """
    validate_path_component(session_id)
    return tenant_root / "sessions" / session_id


class SessionStore:
    """File I/O for per-session artifacts.

    Sessions are tenant-scoped (no ``backend_id`` axis). The store is
    rooted at the tenant directory; per-session content nests under
    ``sessions/{session_id}/``.
    """

    def __init__(self, base_dir: Path):
        self._base_dir = base_dir

    # -- Path helpers ---------------------------------------------------------

    def session_dir(self, session_id: str) -> Path:
        """Public accessor for ``{tenant_root}/sessions/{session_id}``."""
        return session_dir_for(self._base_dir, session_id)

    def _state_path(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "session.json"

    def journal_path(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "journal.md"

    def notes_path(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "notes.md"

    # -- Session CRUD ---------------------------------------------------------

    def create(self, session_id: str, state: dict[str, Any]) -> Path:
        """Write ``session.json`` with timestamps.

        Idempotent: a re-create preserves the existing ``created_at`` and
        merges new keys over old. ``updated_at`` is always now.
        """
        path = self._state_path(session_id)
        existing = read_json_optional(path) or {}
        now = datetime.now(UTC).isoformat()
        data = {
            **existing,
            **state,
            "session_id": session_id,
            "created_at": existing.get("created_at", now),
            "updated_at": now,
        }
        write_json(path, data)
        return path

    def read(self, session_id: str) -> dict[str, Any] | None:
        return read_json_optional(self._state_path(session_id))

    def update(self, session_id: str, updates: dict[str, Any]) -> None:
        """Merge *updates* into ``session.json``. Updates ``updated_at``."""
        path = self._state_path(session_id)
        data = read_json(path)
        data.update(updates)
        data["updated_at"] = datetime.now(UTC).isoformat()
        write_json(path, data)

    # -- Narrative artifacts (touch on mint, append by helpers) ---------------

    def ensure_narrative_files(self, session_id: str) -> None:
        """Create empty journal.md / notes.md so parity holds from mint."""
        sdir = self.session_dir(session_id)
        sdir.mkdir(parents=True, exist_ok=True)
        for name in ("journal.md", "notes.md"):
            (sdir / name).touch()
