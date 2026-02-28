"""
Smart search plan persistence.
"""
from typing import Any

from api.services.stores.base import _BasePlanStore


class SmartSearchStore(_BasePlanStore):
    """File I/O for smart search plan persistence and resume."""

    _subdir = "smart_search_plans"
    _glob_prefix = "ssplan_"

    def list_all(self, backend_id: str) -> list[dict[str, Any]]:
        """Return summary metadata for all smart search plans on disk."""
        results = []
        for data in super().list_all(backend_id):
            config = data.get("config", {})
            scan = data.get("scan_results", {})
            results.append({
                "plan_id": data.get("plan_id", ""),
                "status": data.get("status", "unknown"),
                "n_diagnostic": config.get("n_diagnostic", "?"),
                "max_rounds": config.get("max_rounds", "?"),
                "n_axis_profiles": len(scan.get("axis_profiles", [])),
                "variant_library_hash": data.get("variant_library_hash", ""),
            })
        return results
