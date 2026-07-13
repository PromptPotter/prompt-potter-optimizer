"""Backend registration + sync + execution + dataset cache + connector profile."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.domain.backend import BackendConnection
from promptpotter.infrastructure.store.io import (
    read_json,
    read_json_optional,
    validate_path_component,
    write_json,
)
from promptpotter.shared.clock import utcnow_iso

if TYPE_CHECKING:
    from promptpotter.domain.sample import Sample


class BackendStore:
    """Backend registration + synced API responses + dataset cache.

    Backends: ``archive/backends/{backend_id}/``. Named datasets sit outside
    the tenant tree at ``{datasets_root}/{name}/cache.json`` so they survive
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

    # -- datasets (repo-adjacent, gitignored, name-keyed) ----------------------

    def _dataset_cache_path(self, name: str) -> Path:
        validate_path_component(name)
        return self._datasets_root / name / "cache.json"

    def save_dataset(
        self,
        name: str,
        items: Sequence[Sample | dict[str, Any]],
    ) -> Path:
        """Write a named dataset to disk; ``Sample`` items serialized via ``model_dump()``.

        No ``source_file``: it defaulted to "", the sole caller omitted it, and nothing ever read
        `cache.json::source_file` back — every real `source_file` reader belongs to a different
        store (the draft, sweep and tenant-dataset ones)."""
        from promptpotter.domain.sample import Sample

        serialized = [item.model_dump() if isinstance(item, Sample) else item for item in items]
        data: dict[str, Any] = {
            "name": name,
            "created_at": utcnow_iso(),
            "row_count": len(serialized),
            "items": serialized,
        }
        path = self._dataset_cache_path(name)
        write_json(path, data)
        return path

    def load_dataset(self, name: str) -> dict[str, Any] | None:
        """Load a named dataset. Returns ``None`` if not found."""
        return read_json_optional(self._dataset_cache_path(name))

    # -- connector profile (persistent per-backend defaults) -------------------

    def load_connector_profile(self, backend_id: str) -> dict[str, Any] | None:
        """Load connector profile. Returns None if no profile saved."""
        return read_json_optional(
            self._backend_dir(backend_id) / "connector_profile.json",
        )


__all__ = ["BackendStore"]
