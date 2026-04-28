"""Concrete store implementations — BackendStore, ``Stores`` bundle."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from promptpotter.domain.backend import BackendConnection, Execution
from promptpotter.infrastructure.store.base import (
    read_json,
    read_json_optional,
    validate_path_component,
    write_json,
)
from promptpotter.infrastructure.store.campaign_store import CampaignStore
from promptpotter.infrastructure.store.measurement_archive import MeasurementArchive
from promptpotter.infrastructure.store.session_store import SessionStore

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
