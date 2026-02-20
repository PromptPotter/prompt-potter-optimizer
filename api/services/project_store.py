"""
File-based project store for backend data.

Layout:
    .promptpotter/projects/
      {backend_id}/
        backend.json                  # BackendConnection config
        sync/
          experiments.json            # GET /experiments (verbatim)
          experiments/
            {experiment_id}.json      # GET /experiments/{id} (verbatim)
        executions/
          {execution_id}.json         # Replay results
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from api.models.backend import BackendConnection, Execution

BASE_DIR = Path(".promptpotter") / "projects"


class ProjectStore:
    """File I/O for .promptpotter/projects/."""

    def __init__(self, base_dir: Path | None = None):
        self.base_dir = base_dir or BASE_DIR

    # -- helpers ----------------------------------------------------------

    def _backend_dir(self, backend_id: str) -> Path:
        return self.base_dir / backend_id

    def _sync_dir(self, backend_id: str) -> Path:
        return self._backend_dir(backend_id) / "sync"

    def _executions_dir(self, backend_id: str) -> Path:
        return self._backend_dir(backend_id) / "executions"

    @staticmethod
    def _write_json(path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    @staticmethod
    def _read_json(path: Path) -> Any:
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    # -- backend CRUD -----------------------------------------------------

    def register_backend(self, backend: BackendConnection) -> Path:
        """Write backend.json for a new backend."""
        path = self._backend_dir(backend.id) / "backend.json"
        self._write_json(path, backend.model_dump())
        return path

    def get_backend(self, backend_id: str) -> Optional[BackendConnection]:
        """Read backend.json, return None if not found."""
        path = self._backend_dir(backend_id) / "backend.json"
        if not path.exists():
            return None
        return BackendConnection(**self._read_json(path))

    def list_backends(self) -> List[BackendConnection]:
        """List all registered backends."""
        if not self.base_dir.exists():
            return []
        backends = []
        for d in sorted(self.base_dir.iterdir()):
            cfg = d / "backend.json"
            if cfg.exists():
                backends.append(BackendConnection(**self._read_json(cfg)))
        return backends

    def delete_backend(self, backend_id: str) -> bool:
        """Delete a backend and all its data. Returns True if it existed."""
        import shutil

        d = self._backend_dir(backend_id)
        if not d.exists():
            return False
        shutil.rmtree(d)
        return True

    def update_backend(self, backend: BackendConnection) -> None:
        """Overwrite backend.json with updated data."""
        path = self._backend_dir(backend.id) / "backend.json"
        self._write_json(path, backend.model_dump())

    # -- sync (verbatim API responses) ------------------------------------

    def save_sync(self, backend_id: str, key: str, data: Any) -> Path:
        """Store a verbatim API response under sync/.

        ``key`` is a relative path like ``experiments.json`` or
        ``experiments/{id}.json``.
        """
        path = self._sync_dir(backend_id) / key
        self._write_json(path, data)
        return path

    def load_sync(self, backend_id: str, key: str) -> Optional[Any]:
        """Read a synced API response. Returns None if not found."""
        path = self._sync_dir(backend_id) / key
        if not path.exists():
            return None
        return self._read_json(path)

    def list_synced_experiments(self, backend_id: str) -> List[Dict[str, Any]]:
        """List individual synced experiment files."""
        exp_dir = self._sync_dir(backend_id) / "experiments"
        if not exp_dir.exists():
            return []
        results = []
        for p in sorted(exp_dir.glob("*.json")):
            results.append(self._read_json(p))
        return results

    # -- executions -------------------------------------------------------

    def save_execution(self, execution: Execution) -> Path:
        """Save an execution result."""
        path = (
            self._executions_dir(execution.backend_id)
            / f"{execution.execution_id}.json"
        )
        self._write_json(path, execution.model_dump())
        return path

    def load_execution(
        self, backend_id: str, execution_id: str
    ) -> Optional[Execution]:
        """Load an execution by ID. Returns None if not found."""
        path = self._executions_dir(backend_id) / f"{execution_id}.json"
        if not path.exists():
            return None
        return Execution(**self._read_json(path))

    def list_executions(self, backend_id: str) -> List[Dict[str, Any]]:
        """List execution summaries (without full results array)."""
        d = self._executions_dir(backend_id)
        if not d.exists():
            return []
        items = []
        for p in sorted(d.glob("*.json")):
            data = self._read_json(p)
            # Return summary without the large results list
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
