"""Dataset persistence store.

Saves and loads train/test dataset JSON files under::

    .promptpotter/projects/{backend_id}/datasets/
        train.json
        test_processes.json
        test_material.json

Each file: ``{"name", "created_at", "source_file", "row_count", "items": [...]}``.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from api.services.stores.base import (
    read_json_optional,
    validate_path_component,
    write_json,
)


class DatasetStore:
    """File-based store for ground-truth datasets."""

    def __init__(self, base_dir: Path):
        self._base_dir = base_dir

    def _datasets_dir(self, backend_id: str) -> Path:
        validate_path_component(backend_id)
        return self._base_dir / backend_id / "datasets"

    def save(
        self,
        backend_id: str,
        name: str,
        items: list[dict],
        *,
        source_file: str = "",
    ) -> Path:
        """Write a named dataset to disk.

        Args:
            backend_id: Backend identifier.
            name: Dataset name (e.g. "train", "test_processes").
            items: List of ``{"query", "ground_truth", ...}`` dicts.
            source_file: Original source filename for provenance.

        Returns:
            Path to the written JSON file.
        """
        validate_path_component(name)
        data: dict[str, Any] = {
            "name": name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_file": source_file,
            "row_count": len(items),
            "items": items,
        }
        path = self._datasets_dir(backend_id) / f"{name}.json"
        write_json(path, data)
        return path

    def load(self, backend_id: str, name: str) -> dict[str, Any] | None:
        """Load a named dataset. Returns ``None`` if not found."""
        validate_path_component(name)
        return read_json_optional(
            self._datasets_dir(backend_id) / f"{name}.json",
        )

