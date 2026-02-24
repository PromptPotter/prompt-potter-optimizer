"""
Smart search plan persistence.
"""
from pathlib import Path
from typing import Any

from api.services.stores.base import read_json, validate_path_component, write_json


class SmartSearchStore:
    """File I/O for smart search plan persistence and resume."""

    def __init__(self, base_dir: Path):
        self._base_dir = base_dir

    def _plans_dir(self, backend_id: str) -> Path:
        validate_path_component(backend_id)
        return self._base_dir / backend_id / "smart_search_plans"

    def save(
        self, backend_id: str, plan_id: str, plan_data: dict[str, Any],
    ) -> Path:
        """Write a smart search plan to disk."""
        path = self._plans_dir(backend_id) / f"{plan_id}.json"
        write_json(path, plan_data)
        return path

    def load(
        self, backend_id: str, plan_id: str,
    ) -> dict[str, Any] | None:
        """Read a smart search plan. Returns None if not found."""
        path = self._plans_dir(backend_id) / f"{plan_id}.json"
        if not path.exists():
            return None
        return read_json(path)

    def update(
        self, backend_id: str, plan_id: str, updates: dict[str, Any],
    ) -> None:
        """Merge updates into an existing smart search plan and write."""
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
                "plan_id": data.get("plan_id", path.stem),
                "status": data.get("status", "unknown"),
                "n_diagnostic": config.get("n_diagnostic", "?"),
                "max_rounds": config.get("max_rounds", "?"),
                "n_axis_profiles": len(scan.get("axis_profiles", [])),
            })
        return results
