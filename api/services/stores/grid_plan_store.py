"""
Grid search plan persistence.
"""
from typing import Any

from api.services.stores.base import _BasePlanStore


class GridPlanStore(_BasePlanStore):
    """File I/O for grid search plan persistence and resume."""

    _subdir = "grid_plans"
    _glob_prefix = "gridplan_"

    def update_status(
        self, backend_id: str, plan_id: str, status: str,
    ) -> None:
        """Update the status field of an existing grid plan in-place."""
        self.update(backend_id, plan_id, {"status": status})

    def list_all(self, backend_id: str) -> list[dict[str, Any]]:
        """Return summary metadata for all grid plans on disk."""
        results = []
        for data in super().list_all(backend_id):
            sm = data.get("sampling_meta", {})
            axes = data.get("grid_axes", {})
            points = data.get("grid_points", [])
            results.append({
                "plan_id": data.get("plan_id", ""),
                "status": data.get("status", "unknown"),
                "n_points": len(points),
                "total_space": sm.get("total_space", "?"),
                "axes": list(axes.keys()),
                "exploration_rate": sm.get("exploration_rate", "?"),
            })
        return results
