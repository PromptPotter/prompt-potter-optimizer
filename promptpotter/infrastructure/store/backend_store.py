"""Backend registration + sync + execution + connector profile."""

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
    """Backend registration + synced API responses.

    Backends: ``archive/backends/{backend_id}/``. It also held a named-dataset row
    cache, writing ``{datasets_root}/{name}/cache.json`` outside the tenant tree —
    which is what made the install tier have to be writable, and it is not once the
    definitions ship inside a wheel. Rows are the operator's and now live with the
    rest of their data (``TenantDatasetStore.save_benchmark_rows``); a HuggingFace
    fetch was never a backend fact.
    """

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
