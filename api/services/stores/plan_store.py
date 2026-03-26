"""
PlanStore — file I/O for smart search plan persistence.
"""
from pathlib import Path
from typing import Any

from api.services.stores.base import (
    read_json,
    read_json_optional,
    validate_path_component,
    write_json,
)


class PlanStore:
    """File I/O for smart search plan persistence and resume."""

    def __init__(self, base_dir: Path):
        self._base_dir = base_dir

    def _plans_dir(self, backend_id: str) -> Path:
        validate_path_component(backend_id)
        return self._base_dir / backend_id / "smart_search_plans"

    def save(
        self, backend_id: str, plan_id: str, plan_data: dict[str, Any],
    ) -> Path:
        """Write a plan to disk."""
        path = self._plans_dir(backend_id) / f"{plan_id}.json"
        write_json(path, plan_data)
        return path

    def load(
        self, backend_id: str, plan_id: str,
    ) -> dict[str, Any] | None:
        """Read a plan. Returns None if not found."""
        return read_json_optional(self._plans_dir(backend_id) / f"{plan_id}.json")

    def update(
        self, backend_id: str, plan_id: str, updates: dict[str, Any],
    ) -> None:
        """Merge *updates* into an existing plan and write back."""
        path = self._plans_dir(backend_id) / f"{plan_id}.json"
        data = read_json(path)
        data.update(updates)
        write_json(path, data)

    def list_all(self, backend_id: str) -> list[dict[str, Any]]:
        """Return summary metadata for all smart search plans on disk."""
        plans_dir = self._plans_dir(backend_id)
        if not plans_dir.exists():
            return []
        results = []
        for path in sorted(plans_dir.glob("ssplan_*.json")):
            data = read_json(path)
            config = data.get("config", {})
            scan = data.get("scan_results", {})
            results.append({
                "plan_id": data["plan_id"],
                "status": data["status"],
                "n_diagnostic": config.get("n_diagnostic", "?"),
                "max_rounds": config.get("max_rounds", "?"),
                "n_axis_profiles": len(scan.get("axis_profiles", [])),
                "variant_library_hash": data.get("variant_library_hash", ""),
            })
        return results
