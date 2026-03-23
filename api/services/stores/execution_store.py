"""
Execution (pipeline replay) storage.
"""
from pathlib import Path
from typing import Any

from api.models.backend import Execution
from api.services.stores.base import (
    read_json,
    read_json_optional,
    validate_path_component,
)


class ExecutionStore:
    """File I/O for pipeline execution replays."""

    def __init__(self, base_dir: Path):
        self._base_dir = base_dir

    def _executions_dir(self, backend_id: str) -> Path:
        validate_path_component(backend_id)
        return self._base_dir / backend_id / "executions"

    # -- read / write ---------------------------------------------------------

    def load(self, backend_id: str, execution_id: str) -> Execution | None:
        """Load an execution by ID. Returns None if not found."""
        data = read_json_optional(
            self._executions_dir(backend_id) / f"{execution_id}.json",
        )
        return Execution(**data) if data is not None else None

    def list_all(self, backend_id: str) -> list[dict[str, Any]]:
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
