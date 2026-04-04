"""
PlanStore — file I/O for smart search plan persistence.
"""
from pathlib import Path
from typing import Any

from promptpotter.services.stores.base import EntityStore, read_json


class PlanStore(EntityStore):
    """File I/O for smart search plan persistence and resume."""

    def __init__(self, base_dir: Path):
        super().__init__(base_dir, "smart_search_plans")

    def list_all(self, backend_id: str) -> list[dict[str, Any]]:
        """Return summary metadata for all smart search plans on disk."""
        plans_dir = self._entity_dir(backend_id)
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
