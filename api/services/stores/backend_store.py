"""
Backend CRUD, sync, execution, and dataset storage.

Consolidates all backend-scoped file I/O into one class.
"""
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from api.models.backend import BackendConnection, Execution
from api.services.stores.base import (
    read_json,
    read_json_optional,
    validate_path_component,
    write_json,
)


class BackendStore:
    """File I/O for backend registration and synced API responses."""

    def __init__(self, base_dir: Path):
        self._base_dir = base_dir

    def _backend_dir(self, backend_id: str) -> Path:
        validate_path_component(backend_id)
        return self._base_dir / backend_id

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
        if not self._base_dir.exists():
            return []
        backends = []
        for d in sorted(self._base_dir.iterdir()):
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
        validate_path_component(backend_id)
        return self._base_dir / backend_id / "executions"

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
            items.append({
                "execution_id": data["execution_id"],
                "backend_id": data["backend_id"],
                "experiment_id": data["experiment_id"],
                "variant_label": data.get("variant_label", ""),
                "pipeline_notation": data.get("pipeline_notation", ""),
                "query_count": data.get("query_count", 0),
                "successful_count": data.get("successful_count", 0),
                "created_at": data.get("created_at", ""),
            })
        return items

    # -- datasets (absorbed from DatasetStore) --------------------------------

    def _datasets_dir(self, backend_id: str) -> Path:
        validate_path_component(backend_id)
        return self._base_dir / backend_id / "datasets"

    def save_dataset(
        self,
        backend_id: str,
        name: str,
        items: list[dict],
        *,
        source_file: str = "",
    ) -> Path:
        """Write a named dataset to disk."""
        validate_path_component(name)
        data: dict[str, Any] = {
            "name": name,
            "created_at": datetime.now(UTC).isoformat(),
            "source_file": source_file,
            "row_count": len(items),
            "items": items,
        }
        path = self._datasets_dir(backend_id) / f"{name}.json"
        write_json(path, data)
        return path

    def load_dataset(self, backend_id: str, name: str) -> dict[str, Any] | None:
        """Load a named dataset. Returns ``None`` if not found."""
        validate_path_component(name)
        return read_json_optional(
            self._datasets_dir(backend_id) / f"{name}.json",
        )
