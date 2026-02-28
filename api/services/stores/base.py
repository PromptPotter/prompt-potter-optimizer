"""
Shared I/O helpers for file-based stores.
"""
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

_SAFE_PATH_RE = re.compile(r"^[a-zA-Z0-9_\-\.]+$")


def validate_path_component(name: str) -> str:
    """Validate that *name* is safe for use as a filesystem path component.

    Raises ValueError on traversal attempts or disallowed characters.
    """
    if not name or not _SAFE_PATH_RE.match(name):
        raise ValueError(
            f"Invalid path component: {name!r}. "
            "Only alphanumerics, hyphens, underscores, and dots are allowed."
        )
    return name


def write_json(path: Path, data: Any) -> None:
    """Write *data* as pretty-printed JSON atomically.

    Writes to a temp file in the same directory, then uses ``os.replace()``
    to atomically swap it into place.  This prevents partial/corrupt files
    if the process crashes mid-write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        # Clean up temp file on failure (fd already closed by os.fdopen)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path: Path) -> Any:
    """Read and parse JSON from *path*."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def read_json_optional(path: Path) -> Any | None:
    """Read JSON from *path*, returning ``None`` if it does not exist."""
    if not path.exists():
        return None
    return read_json(path)


def append_jsonl(path: Path, item: dict) -> Path:
    """Append one JSON object as a line to a JSONL file.

    Creates parent directories if needed.  Returns *path*.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")
        f.flush()
    return path


def read_jsonl(path: Path) -> list[dict]:
    """Read all JSON objects from a JSONL file.

    Returns an empty list if *path* does not exist.
    """
    if not path.exists():
        return []
    items: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


# ---------------------------------------------------------------------------
# Base class for plan-like stores (grid plans, smart search plans, …)
# ---------------------------------------------------------------------------

class _BasePlanStore:
    """Shared save / load / update / list_all for plan stores.

    Subclasses set two class attributes:

    - ``_subdir``: directory name under ``{backend_id}/`` (e.g. ``"grid_plans"``)
    - ``_glob_prefix``: filename prefix for ``list_all`` (e.g. ``"gridplan_"``)
    """

    _subdir: str  # set by subclass
    _glob_prefix: str  # set by subclass

    def __init__(self, base_dir: Path):
        self._base_dir = base_dir

    def _plans_dir(self, backend_id: str) -> Path:
        validate_path_component(backend_id)
        return self._base_dir / backend_id / self._subdir

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
        """Return raw data for all plans matching the glob prefix."""
        plans_dir = self._plans_dir(backend_id)
        if not plans_dir.exists():
            return []
        return [
            read_json(path)
            for path in sorted(plans_dir.glob(f"{self._glob_prefix}*.json"))
        ]
