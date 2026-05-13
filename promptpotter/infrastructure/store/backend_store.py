"""Backend registration + sync + execution + dataset cache + connector profile."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from promptpotter.domain.backend import BackendConnection
from promptpotter.infrastructure.store.base import (
    read_json,
    read_json_optional,
    validate_path_component,
    write_json,
)


class BackendStore:
    """File I/O for backend registration and synced API responses.

    Backends live under ``archive/backends/{backend_id}/`` —
    machine state the runtime needs (registration record, sync responses,
    connector profile). Named datasets live outside the tenant tree at
    ``{datasets_root}/{name}/cache.json`` so they survive
    ``.promptpotter/`` resets.
    """

    def __init__(self, base_dir: Path, datasets_root: Path):
        self._base_dir = base_dir
        self._datasets_root = datasets_root

    def _backends_root(self) -> Path:
        return self._base_dir / "archive" / "backends"

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

    def load_connector_profile(self, backend_id: str) -> dict[str, Any] | None:
        """Load connector profile. Returns None if no profile saved."""
        return read_json_optional(
            self._backend_dir(backend_id) / "connector_profile.json",
        )
