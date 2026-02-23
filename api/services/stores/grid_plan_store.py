"""
Grid search plan persistence.
"""
from pathlib import Path
from typing import Any

from api.services.stores.base import read_json, write_json


class GridPlanStore:
    """File I/O for grid search plan persistence and resume."""

    def __init__(self, base_dir: Path):
        self._base_dir = base_dir

    def _plans_dir(self, backend_id: str) -> Path:
        return self._base_dir / backend_id / "grid_plans"

    def save(
        self, backend_id: str, plan_id: str, plan_data: dict[str, Any],
    ) -> Path:
        """Write a grid search plan to disk."""
        path = self._plans_dir(backend_id) / f"{plan_id}.json"
        write_json(path, plan_data)
        return path

    def load(
        self, backend_id: str, plan_id: str,
    ) -> dict[str, Any] | None:
        """Read a grid search plan. Returns None if not found."""
        path = self._plans_dir(backend_id) / f"{plan_id}.json"
        if not path.exists():
            return None
        return read_json(path)

    def update_status(
        self, backend_id: str, plan_id: str, status: str,
    ) -> None:
        """Update the status field of an existing grid plan in-place."""
        path = self._plans_dir(backend_id) / f"{plan_id}.json"
        data = read_json(path)
        data["status"] = status
        write_json(path, data)

    def list_all(self, backend_id: str) -> list[dict[str, Any]]:
        """Return summary metadata for all grid plans on disk."""
        plans_dir = self._plans_dir(backend_id)
        if not plans_dir.exists():
            return []
        results = []
        for path in sorted(plans_dir.glob("gridplan_*.json")):
            data = read_json(path)
            sm = data.get("sampling_meta", {})
            axes = data.get("grid_axes", {})
            points = data.get("grid_points", data.get("combinations", []))
            results.append({
                "plan_id": data.get("plan_id", path.stem),
                "status": data.get("status", "unknown"),
                "n_points": len(points),
                "total_space": sm.get("total_space", "?"),
                "axes": list(axes.keys()),
                "exploration_rate": sm.get("exploration_rate", "?"),
            })
        return results
