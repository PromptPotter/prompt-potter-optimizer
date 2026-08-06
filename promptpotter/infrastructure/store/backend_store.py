from __future__ import annotations

from pathlib import Path
from typing import Any

from promptpotter.domain.backend import BackendConnection
from promptpotter.infrastructure.store.io import (
    read_json,
    read_json_optional,
    validate_path_component,
    write_json,
)


class BackendStore:
    """Backend registration + synced API responses. It also held a named-dataset row cache OUTSIDE the tenant tree, which is
    what made the install tier need to be writable; rows are the operator's and live with the rest of their data."""

    def __init__(self, base_dir: Path):
        self._base_dir = base_dir

    def _backends_root(self) -> Path:
        return self._base_dir / "archive" / "backends"

    def _backend_dir(self, backend_id: str) -> Path:
        validate_path_component(backend_id)
        return self._backends_root() / backend_id

    # -- backend CRUD ---------------------------------------------------------

    def register(self, backend: BackendConnection) -> Path:
        path = self._backend_dir(backend.id) / "backend.json"
        write_json(path, backend.model_dump())
        return path

    def get(self, backend_id: str) -> BackendConnection | None:
        data = read_json_optional(self._backend_dir(backend_id) / "backend.json")
        return BackendConnection(**data) if data is not None else None

    def list_all(self) -> list[BackendConnection]:
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
        path = self._backend_dir(backend.id) / "backend.json"
        write_json(path, backend.model_dump())

    # -- connector profile (persistent per-backend defaults) -------------------

    def load_connector_profile(self, backend_id: str) -> dict[str, Any] | None:
        return read_json_optional(
            self._backend_dir(backend_id) / "connector_profile.json",
        )


__all__ = ["BackendStore"]
